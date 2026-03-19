import time
import logging
from dataclasses import dataclass
from app.auth.models import KeyTier, TIER_LIMITS

logger = logging.getLogger(__name__)


@dataclass
class TokenBucketResult:
    """
    The result of a rate limit check.
    Every field tells the caller exactly what happened and what to do next.
    """
    allowed: bool
    tokens_remaining: float
    capacity: float
    retry_after: float  # seconds to wait before retrying


class TokenBucketLimiter:
    """
    Implements the token bucket algorithm using Redis for shared state.

    Why Redis? If you have 10 servers, each needs to see the same bucket.
    Redis is the single shared memory across all of them.
    """

    def __init__(self, redis_client):
        self.redis = redis_client

    def _bucket_key(self, identifier: str) -> str:
        """Each API key / IP gets its own bucket in Redis"""
        return f"bucket:{identifier}"

    async def check(
        self,
        identifier: str,
        tier: KeyTier = KeyTier.FREE,
    ) -> TokenBucketResult:
        """
        The core function. Called on every single API request.
        Returns whether the request is allowed and how many tokens remain.
        """
        limits = TIER_LIMITS[tier]
        capacity = float(limits["bucket_capacity"])
        refill_rate = float(limits["refill_rate"])  # tokens per second

        bucket_key = self._bucket_key(identifier)
        now = time.time()

        # Step 1: Read the current bucket state from Redis
        data = await self.redis.hgetall(bucket_key)

        if data:
            # Bucket exists — calculate how many tokens refilled since last request
            tokens = float(data["tokens"])
            last_refill = float(data["last_refill"])

            time_elapsed = now - last_refill
            refilled = time_elapsed * refill_rate

            # Add refilled tokens but never exceed capacity
            tokens = min(capacity, tokens + refilled)
        else:
            # First request from this identifier — start with a full bucket
            tokens = capacity

        # Step 2: Decide — do we have a token to spend?
        if tokens >= 1.0:
            tokens -= 1.0
            allowed = True
            retry_after = 0.0
        else:
            allowed = False
            # Tell the client exactly how long to wait for 1 token
            retry_after = round((1.0 - tokens) / refill_rate, 2)

        # Step 3: Save the updated bucket back to Redis
        await self.redis.hset(bucket_key, mapping={
            "tokens": str(tokens),
            "last_refill": str(now),
            "capacity": str(capacity),
            "identifier": identifier,
        })

        # Bucket expires after 2 minutes of inactivity — auto cleanup
        await self.redis.expire(bucket_key, 120)

        result = TokenBucketResult(
            allowed=allowed,
            tokens_remaining=round(tokens, 2),
            capacity=capacity,
            retry_after=retry_after,
        )

        if not allowed:
            logger.warning(
                f"Rate limited: {identifier} | "
                f"tokens={tokens:.2f} | retry_after={retry_after}s"
            )

        return result
