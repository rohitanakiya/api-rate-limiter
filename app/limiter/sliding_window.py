import time
import logging
from dataclasses import dataclass
from app.auth.models import KeyTier, TIER_LIMITS

logger = logging.getLogger(__name__)


@dataclass
class SlidingWindowResult:
    """Result of a sliding window rate limit check."""
    allowed: bool
    current_count: int
    limit: int
    window_seconds: int
    retry_after: float


class SlidingWindowLimiter:
    """
    Implements the sliding window algorithm using Redis sorted sets.

    Each request is stored as a member of a sorted set,
    with its timestamp as the score. To check the limit,
    we remove old entries and count what remains.

    Why sorted sets? Redis lets us query by score range in O(log N).
    Removing all entries older than 60 seconds is one command.
    """

    def __init__(self, redis_client):
        self.redis = redis_client

    def _window_key(self, identifier: str) -> str:
        return f"window:{identifier}"

    async def check(
        self,
        identifier: str,
        tier: KeyTier = KeyTier.FREE,
        window_seconds: int = 60,
    ) -> SlidingWindowResult:
        """
        Core sliding window check. Called on every request.
        """
        limits = TIER_LIMITS[tier]
        limit = limits["requests_per_minute"]

        window_key = self._window_key(identifier)
        now = time.time()
        window_start = now - window_seconds

        # Step 1: Remove all requests older than the window
        # zremrangebyscore removes entries with score between min and max
        await self.redis.zremrangebyscore(window_key, 0, window_start)

        # Step 2: Count how many requests remain in the window
        current_count = await self.redis.zcard(window_key)

        # Step 3: Decide
        if current_count < limit:
            # Step 4: Record this request with current timestamp as score
            # We use a unique member name to avoid collisions
            member = f"{now}-{identifier}"
            await self.redis.zadd(window_key, {member: now})
            allowed = True
            retry_after = 0.0
        else:
            allowed = False
            # Tell client when the oldest request will fall outside the window
            retry_after = round(window_seconds - (now - window_start), 2)

        # Auto-expire the sorted set after the window duration
        await self.redis.expire(window_key, window_seconds + 1)

        result = SlidingWindowResult(
            allowed=allowed,
            current_count=current_count,
            limit=limit,
            window_seconds=window_seconds,
            retry_after=retry_after,
        )

        if not allowed:
            logger.warning(
                f"Sliding window limit hit: {identifier} | "
                f"count={current_count}/{limit}"
            )

        return result