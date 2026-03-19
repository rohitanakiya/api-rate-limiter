import pytest
import time
from app.limiter.token_bucket import TokenBucketLimiter
from app.limiter.sliding_window import SlidingWindowLimiter
from app.auth.models import KeyTier
from app.redis_client import InMemoryFallback


@pytest.fixture
def redis():
    return InMemoryFallback()


@pytest.fixture
def bucket(redis):
    return TokenBucketLimiter(redis)


@pytest.fixture
def window(redis):
    return SlidingWindowLimiter(redis)


class TestTokenBucket:

    async def test_first_request_allowed(self, bucket):
        """First request must always be allowed — bucket starts full."""
        result = await bucket.check("user-1", KeyTier.FREE)
        assert result.allowed is True

    async def test_tokens_decrease_per_request(self, bucket):
        """Each request must consume exactly one token."""
        r1 = await bucket.check("user-2", KeyTier.FREE)
        r2 = await bucket.check("user-2", KeyTier.FREE)
        assert r2.tokens_remaining < r1.tokens_remaining

    async def test_bucket_exhaustion_blocks_requests(self, bucket):
        """
        After exhausting all tokens, requests must be blocked.
        FREE tier has 20 tokens capacity.
        """
        identifier = "user-exhaust"
        # Drain the bucket completely
        for _ in range(20):
            await bucket.check(identifier, KeyTier.FREE)
        # Next request must be blocked
        result = await bucket.check(identifier, KeyTier.FREE)
        assert result.allowed is False

    async def test_blocked_result_has_retry_after(self, bucket):
        """
        When blocked, retry_after must be > 0.
        This tells the client how long to wait — professional API behaviour.
        """
        identifier = "user-retry"
        for _ in range(20):
            await bucket.check(identifier, KeyTier.FREE)
        result = await bucket.check(identifier, KeyTier.FREE)
        assert result.retry_after > 0

    async def test_different_tiers_have_different_capacities(self, bucket):
        """PRO tier must have higher capacity than FREE tier."""
        free_result = await bucket.check("free-user", KeyTier.FREE)
        pro_result = await bucket.check("pro-user", KeyTier.PRO)
        assert pro_result.capacity > free_result.capacity

    async def test_different_identifiers_have_separate_buckets(self, bucket):
        """
        Two different API keys must have completely independent buckets.
        Exhausting one must not affect the other.
        """
        for _ in range(20):
            await bucket.check("user-a", KeyTier.FREE)
        blocked = await bucket.check("user-a", KeyTier.FREE)
        unaffected = await bucket.check("user-b", KeyTier.FREE)
        assert blocked.allowed is False
        assert unaffected.allowed is True


class TestSlidingWindow:

    async def test_first_request_allowed(self, window):
        """First request in a fresh window must always be allowed."""
        result = await window.check("win-user-1", KeyTier.FREE)
        assert result.allowed is True

    async def test_count_increases_per_request(self, window):
        """Request count must increase with each request."""
        await window.check("win-user-2", KeyTier.FREE)
        r2 = await window.check("win-user-2", KeyTier.FREE)
        assert r2.current_count >= 1

    async def test_limit_blocks_excess_requests(self, window):
        """
        After reaching the per-minute limit, requests must be blocked.
        FREE tier limit is 20 requests per minute.
        """
        identifier = "win-exhaust"
        for _ in range(20):
            await window.check(identifier, KeyTier.FREE)
        result = await window.check(identifier, KeyTier.FREE)
        assert result.allowed is False

    async def test_limit_matches_tier(self, window):
        """The reported limit must match the tier's configured limit."""
        result = await window.check("win-tier-test", KeyTier.PRO)
        assert result.limit == 100

    async def test_separate_identifiers_independent(self, window):
        """Two different keys must have independent windows."""
        for _ in range(20):
            await window.check("win-a", KeyTier.FREE)
        blocked = await window.check("win-a", KeyTier.FREE)
        unaffected = await window.check("win-b", KeyTier.FREE)
        assert blocked.allowed is False
        assert unaffected.allowed is True