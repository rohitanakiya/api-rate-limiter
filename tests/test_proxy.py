"""
Tests for the gateway proxy (/gw/{path}).

Covers:
  - 503 when UPSTREAM_URL is unset
  - Forwarding GET/POST with body and query
  - X-Authenticated-* identity headers added to upstream call
  - X-API-Key and X-Admin-Key stripped before forwarding
  - 502 on connection errors, 504 on timeouts

We override `verify_request` so these tests don't need real Redis or a
real key — they're unit tests of the proxy logic itself, not the auth
pipeline (which has its own tests).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth.models import APIKey, KeyScope, KeyTier
from app.config import get_settings
from app.dependencies import verify_request
from app.main import app


# ── Helpers ───────────────────────────────────────────────────────────


def _fake_api_key() -> APIKey:
    return APIKey(
        key_id="test-key-id",
        key_hash="test-hash",
        name="test key",
        scopes=[KeyScope.READ, KeyScope.WRITE],
        tier=KeyTier.FREE,
    )


def _mock_httpx_response(
    status_code: int = 200,
    content: bytes = b'{"ok": true}',
    headers: dict[str, str] | None = None,
):
    """Build a fake httpx.Response-like object."""
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    response.headers = httpx.Headers(headers or {"content-type": "application/json"})
    return response


def _patched_async_client(request_mock: AsyncMock):
    """
    Return an object that, when used as `httpx.AsyncClient`, behaves like
    a context manager whose .request method is the supplied AsyncMock.
    """
    client_instance = MagicMock()
    client_instance.request = request_mock
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_instance)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=cm)
    return factory


@pytest.fixture
def configured_upstream(monkeypatch):
    """Set UPSTREAM_URL and bust the lru_cache so the new value is read."""
    monkeypatch.setenv("UPSTREAM_URL", "http://upstream.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def auth_overridden():
    """Replace verify_request with a stub that returns a fake APIKey."""
    async def fake() -> APIKey:
        return _fake_api_key()
    app.dependency_overrides[verify_request] = fake
    yield
    app.dependency_overrides.pop(verify_request, None)


@pytest.fixture
def client(auth_overridden):
    return TestClient(app)


# ── Tests ─────────────────────────────────────────────────────────────


class TestProxyDisabled:
    """When UPSTREAM_URL isn't set, /gw/* must return 503."""

    def test_returns_503_when_upstream_not_configured(self, client, monkeypatch):
        monkeypatch.delenv("UPSTREAM_URL", raising=False)
        get_settings.cache_clear()
        try:
            response = client.get("/gw/anything")
            assert response.status_code == 503
            body = response.json()
            assert body["detail"]["error"] == "proxy_disabled"
        finally:
            get_settings.cache_clear()


class TestProxyForwarding:
    """The proxy faithfully forwards method, path, query, body."""

    def test_get_request_is_forwarded(self, client, configured_upstream):
        request_mock = AsyncMock(return_value=_mock_httpx_response(200, b'{"hi": 1}'))
        with patch("app.proxy.router.httpx.AsyncClient", _patched_async_client(request_mock)):
            response = client.get("/gw/menu?city=bangalore&veg=true")
            assert response.status_code == 200
            assert response.json() == {"hi": 1}

        # Was upstream called with the right method + URL?
        call = request_mock.call_args
        assert call.kwargs["method"] == "GET"
        assert call.kwargs["url"].startswith("http://upstream.test/menu")
        assert "city=bangalore" in call.kwargs["url"]
        assert "veg=true" in call.kwargs["url"]

    def test_post_body_is_forwarded(self, client, configured_upstream):
        request_mock = AsyncMock(return_value=_mock_httpx_response(201, b'{"created": true}'))
        with patch("app.proxy.router.httpx.AsyncClient", _patched_async_client(request_mock)):
            response = client.post(
                "/gw/chat/recommend",
                json={"text": "high protein"},
            )
            assert response.status_code == 201

        call = request_mock.call_args
        assert call.kwargs["method"] == "POST"
        assert b'"text"' in call.kwargs["content"]
        assert b"high protein" in call.kwargs["content"]


class TestIdentityHeaders:
    """Upstream receives X-Authenticated-* headers describing the caller."""

    def test_identity_headers_added(self, client, configured_upstream):
        request_mock = AsyncMock(return_value=_mock_httpx_response())
        with patch("app.proxy.router.httpx.AsyncClient", _patched_async_client(request_mock)):
            client.get("/gw/whatever", headers={"X-API-Key": "rl_anything"})

        forwarded = request_mock.call_args.kwargs["headers"]
        assert forwarded["X-Authenticated-Key-Id"] == "test-key-id"
        assert forwarded["X-Authenticated-Key-Name"] == "test key"
        assert "read" in forwarded["X-Authenticated-Scopes"]
        assert "write" in forwarded["X-Authenticated-Scopes"]
        assert forwarded["X-Authenticated-Tier"] == "free"


class TestSecretHeadersStripped:
    """Caller secrets must never reach the upstream."""

    def test_x_api_key_is_stripped(self, client, configured_upstream):
        request_mock = AsyncMock(return_value=_mock_httpx_response())
        with patch("app.proxy.router.httpx.AsyncClient", _patched_async_client(request_mock)):
            client.get("/gw/anything", headers={"X-API-Key": "rl_secret"})

        forwarded = {k.lower(): v for k, v in request_mock.call_args.kwargs["headers"].items()}
        assert "x-api-key" not in forwarded

    def test_x_admin_key_is_stripped(self, client, configured_upstream):
        request_mock = AsyncMock(return_value=_mock_httpx_response())
        with patch("app.proxy.router.httpx.AsyncClient", _patched_async_client(request_mock)):
            client.get("/gw/anything", headers={"X-Admin-Key": "secret"})

        forwarded = {k.lower(): v for k, v in request_mock.call_args.kwargs["headers"].items()}
        assert "x-admin-key" not in forwarded


class TestUpstreamFailures:
    """Transport-level failures are mapped to clean gateway responses."""

    def test_timeout_maps_to_504(self, client, configured_upstream):
        request_mock = AsyncMock(side_effect=httpx.TimeoutException("slow upstream"))
        with patch("app.proxy.router.httpx.AsyncClient", _patched_async_client(request_mock)):
            response = client.get("/gw/anything")

        assert response.status_code == 504
        assert response.json()["detail"]["error"] == "upstream_timeout"

    def test_connect_error_maps_to_502(self, client, configured_upstream):
        request_mock = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("app.proxy.router.httpx.AsyncClient", _patched_async_client(request_mock)):
            response = client.get("/gw/anything")

        assert response.status_code == 502
        assert response.json()["detail"]["error"] == "upstream_unreachable"

    def test_upstream_4xx_passes_through(self, client, configured_upstream):
        """An upstream 404 should reach the client as 404, not a gateway 502."""
        request_mock = AsyncMock(
            return_value=_mock_httpx_response(404, b'{"error": "not_found"}')
        )
        with patch("app.proxy.router.httpx.AsyncClient", _patched_async_client(request_mock)):
            response = client.get("/gw/missing")

        assert response.status_code == 404
        assert response.json() == {"error": "not_found"}
