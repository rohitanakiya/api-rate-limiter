import time
import logging
from app.auth.models import KeyTier

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    Records request metrics into Redis.
    Lightweight — just incrementing counters and storing timestamps.
    """

    def __init__(self, redis_client):
        self.redis = redis_client

    def _metrics_key(self, key_id: str) -> str:
        return f"metrics:{key_id}"

    def _global_key(self) -> str:
        return "metrics:global"

    async def record_request(self, key_id: str, tier: KeyTier) -> None:
        """Records a successful request."""
        now = str(time.time())
        await self.redis.hset(self._metrics_key(key_id), mapping={
            "last_request_at": now,
            "tier": tier,
        })
        await self.redis.incr(f"metrics:{key_id}:total")
        await self.redis.incr("metrics:global:total")

    async def record_throttled(self, key_id: str, tier: KeyTier) -> None:
        """Records a throttled (rejected) request."""
        await self.redis.incr(f"metrics:{key_id}:throttled")
        await self.redis.incr("metrics:global:throttled")

    async def get_stats(self, key_id: str) -> dict:
        """Returns metrics for a specific API key."""
        total = await self.redis.get(f"metrics:{key_id}:total") or "0"
        throttled = await self.redis.get(f"metrics:{key_id}:throttled") or "0"
        meta = await self.redis.hgetall(self._metrics_key(key_id))
        return {
            "key_id": key_id,
            "total_requests": int(total),
            "throttled_requests": int(throttled),
            "last_request_at": meta.get("last_request_at"),
            "tier": meta.get("tier"),
        }

    async def get_global_stats(self) -> dict:
        """Returns system-wide metrics."""
        total = await self.redis.get("metrics:global:total") or "0"
        throttled = await self.redis.get("metrics:global:throttled") or "0"
        return {
            "total_requests": int(total),
            "throttled_requests": int(throttled),
            "throttle_rate": round(
                int(throttled) / max(int(total), 1) * 100, 2
            ),
        }