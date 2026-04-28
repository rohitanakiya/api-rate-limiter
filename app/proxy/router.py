"""
Gateway / proxy mode.

Requests to /gw/{path} flow:
  1. verify_request validates the X-API-Key, runs token-bucket and
     sliding-window checks, records metrics. Throws 401/403/429 on failure.
  2. We forward the same method/body/query to UPSTREAM_URL/{path}.
  3. Identity headers (X-Authenticated-Key-Id, etc.) are added so the
     upstream service knows who's calling without re-validating the key.
  4. We strip the X-API-Key header before forwarding so upstreams never
     see the caller's secret.
  5. Upstream's response (status, headers, body) is streamed back as-is.

This turns the rate-limiter into an API gateway in front of any HTTP
service. Set UPSTREAM_URL in .env to enable.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.auth.models import APIKey
from app.config import get_settings
from app.dependencies import verify_request

logger = logging.getLogger(__name__)
router = APIRouter()

# Headers that should NOT be forwarded to the upstream service.
# - hop-by-hop headers per RFC 7230 (connection-specific, not end-to-end)
# - the caller's secret (X-API-Key) — upstream gets identity via
#   X-Authenticated-* headers instead
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",  # httpx will recompute this
}

SECRET_HEADERS = {
    "x-api-key",
    "x-admin-key",
}


def _filter_request_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip headers we don't want forwarded to upstream."""
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() not in SECRET_HEADERS
    }


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    """Strip hop-by-hop headers from upstream response before returning."""
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP
    }


def _identity_headers(api_key: APIKey) -> dict[str, str]:
    """Headers describing the authenticated caller, added to every forwarded request."""
    return {
        "X-Authenticated-Key-Id": api_key.key_id,
        "X-Authenticated-Key-Name": api_key.name,
        "X-Authenticated-Scopes": ",".join(api_key.scopes),
        "X-Authenticated-Tier": api_key.tier,
    }


def _build_upstream_url(upstream_base: str, path: str, query: str) -> str:
    """
    Join the configured upstream base URL with the proxied path and query.
    Ensures the path is appended cleanly even if upstream_base has no
    trailing slash and path has no leading slash.
    """
    base = upstream_base.rstrip("/") + "/"
    target = urljoin(base, path.lstrip("/"))
    if query:
        target = f"{target}?{query}"
    return target


@router.api_route(
    "/gw/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    summary="Authenticated, rate-limited proxy to the configured upstream service",
)
async def proxy(
    path: str,
    request: Request,
    api_key: APIKey = Depends(verify_request),
) -> Response:
    settings = get_settings()
    if not settings.upstream_url:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "proxy_disabled",
                "message": (
                    "Gateway proxy is not configured. Set UPSTREAM_URL "
                    "in the rate-limiter .env to enable."
                ),
            },
        )

    target_url = _build_upstream_url(
        settings.upstream_url,
        path,
        request.url.query,
    )

    forwarded_headers = _filter_request_headers(dict(request.headers))
    forwarded_headers.update(_identity_headers(api_key))

    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=settings.proxy_request_timeout) as client:
            upstream_response = await client.request(
                method=request.method,
                url=target_url,
                headers=forwarded_headers,
                content=body,
            )
    except httpx.TimeoutException:
        logger.warning("Upstream timed out: %s %s", request.method, target_url)
        raise HTTPException(
            status_code=504,
            detail={
                "error": "upstream_timeout",
                "message": "Upstream service did not respond in time.",
            },
        )
    except httpx.RequestError as exc:
        logger.warning("Upstream unreachable: %s %s — %s", request.method, target_url, exc)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "upstream_unreachable",
                "message": "Could not reach the upstream service.",
            },
        )

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=_filter_response_headers(upstream_response.headers),
        media_type=upstream_response.headers.get("content-type"),
    )
