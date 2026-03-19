import logging
from fastapi import Depends, HTTPException, Security, Request
from fastapi.security import APIKeyHeader
from app.redis_client import redis_client
from app.auth.key_manager import KeyManager
from app.auth.models import APIKey, KeyScope, KeyTier
from app.limiter.token_bucket import TokenBucketLimiter
from app.limiter.sliding_window import SlidingWindowLimiter
from app.metrics.collector import MetricsCollector

logger = logging.getLogger(__name__)

# Tells FastAPI to look for an "X-API-Key" header on incoming requests
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_key_manager() -> KeyManager:
    """Dependency that provides a KeyManager instance."""
    return KeyManager(redis_client.client)


async def get_token_bucket() -> TokenBucketLimiter:
    """Dependency that provides a TokenBucketLimiter instance."""
    return TokenBucketLimiter(redis_client.client)


async def get_sliding_window() -> SlidingWindowLimiter:
    """Dependency that provides a SlidingWindowLimiter instance."""
    return SlidingWindowLimiter(redis_client.client)


async def get_metrics() -> MetricsCollector:
    """Dependency that provides a MetricsCollector instance."""
    return MetricsCollector(redis_client.client)


async def verify_request(
    request: Request,
    raw_key: str = Security(api_key_header),
    key_manager: KeyManager = Depends(get_key_manager),
    bucket_limiter: TokenBucketLimiter = Depends(get_token_bucket),
    window_limiter: SlidingWindowLimiter = Depends(get_sliding_window),
    metrics: MetricsCollector = Depends(get_metrics),
) -> APIKey:
    """
    The master dependency. Runs on every protected route.

    Order of operations:
    1. Check API key exists and is valid
    2. Check token bucket (controls burst rate)
    3. Check sliding window (controls per-minute volume)
    4. Record metrics
    5. Return the verified APIKey to the route handler
    """

    # ── Step 1: Authentication ────────────────────────────────────────────
    if not raw_key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "missing_api_key",
                "message": "Provide your API key in the X-API-Key header",
            }
        )

    api_key = await key_manager.get_key_by_raw(raw_key)

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_api_key",
                "message": "API key is invalid or has been deactivated",
            }
        )

    # ── Step 2: Token bucket check (burst control) ────────────────────────
    bucket_result = await bucket_limiter.check(
        identifier=api_key.key_id,
        tier=api_key.tier,
    )

    if not bucket_result.allowed:
        await key_manager.update_usage(api_key.key_id, was_throttled=True)
        await metrics.record_throttled(api_key.key_id, api_key.tier)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": "Token bucket exhausted. Slow down your request rate.",
                "tokens_remaining": bucket_result.tokens_remaining,
                "retry_after_seconds": bucket_result.retry_after,
            },
            headers={"Retry-After": str(bucket_result.retry_after)},
        )

    # ── Step 3: Sliding window check (per-minute volume) ──────────────────
    window_result = await window_limiter.check(
        identifier=api_key.key_id,
        tier=api_key.tier,
    )

    if not window_result.allowed:
        await key_manager.update_usage(api_key.key_id, was_throttled=True)
        await metrics.record_throttled(api_key.key_id, api_key.tier)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": "Per-minute request limit reached.",
                "requests_in_window": window_result.current_count,
                "limit": window_result.limit,
                "retry_after_seconds": window_result.retry_after,
            },
            headers={"Retry-After": str(window_result.retry_after)},
        )

    # ── Step 4: Record successful request metrics ─────────────────────────
    await key_manager.update_usage(api_key.key_id, was_throttled=False)
    await metrics.record_request(api_key.key_id, api_key.tier)

    return api_key


def require_scope(scope: KeyScope):
    """
    A dependency factory — creates a dependency that checks for a specific scope.

    Usage in a route:
        @app.get("/data", dependencies=[Depends(require_scope(KeyScope.READ))])

    This is a closure — it returns a function that remembers the scope it was
    created with. A very common Python pattern.
    """
    async def scope_checker(api_key: APIKey = Depends(verify_request)) -> APIKey:
        if scope not in api_key.scopes:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "insufficient_scope",
                    "message": f"This endpoint requires the '{scope}' scope.",
                    "your_scopes": api_key.scopes,
                }
            )
        return api_key
    return scope_checker