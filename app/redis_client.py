import redis.asyncio as aioredis
import logging
from typing import Optional
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class InMemoryFallback:
    """
    A simple in-memory store that mimics the Redis commands we use.
    Used automatically when Redis is unreachable.
    """

    def __init__(self):
        self._store: dict = {}
        self._expiry: dict = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> Optional[str]:
        import time
        if key in self._expiry:
            if time.time() > self._expiry[key]:
                del self._store[key]
                del self._expiry[key]
                return None
        return self._store.get(key)

    async def set(self, key: str, value, ex: int = None) -> bool:
        import time
        self._store[key] = str(value)
        if ex:
            self._expiry[key] = time.time() + ex
        return True

    async def incr(self, key: str) -> int:
        current = int(self._store.get(key, 0))
        self._store[key] = str(current + 1)
        return current + 1

    async def expire(self, key: str, seconds: int) -> bool:
        import time
        if key in self._store:
            self._expiry[key] = time.time() + seconds
        return True

    async def ttl(self, key: str) -> int:
        import time
        if key in self._expiry:
            remaining = self._expiry[key] - time.time()
            return max(0, int(remaining))
        return -1

    async def delete(self, key: str) -> int:
        existed = key in self._store
        self._store.pop(key, None)
        self._expiry.pop(key, None)
        return 1 if existed else 0

    async def zadd(self, key: str, mapping: dict) -> int:
        if key not in self._store:
            self._store[key] = {}
        self._store[key].update(mapping)
        return len(mapping)

    async def zremrangebyscore(self, key: str, min_score, max_score) -> int:
        if key not in self._store:
            return 0
        before = len(self._store[key])
        self._store[key] = {
            k: v for k, v in self._store[key].items()
            if not (min_score <= v <= max_score)
        }
        return before - len(self._store[key])

    async def zcard(self, key: str) -> int:
        return len(self._store.get(key, {}))

    async def hset(self, key: str, mapping: dict = None, **kwargs) -> int:
        if key not in self._store:
            self._store[key] = {}
        if mapping:
            self._store[key].update(mapping)
        if kwargs:
            self._store[key].update(kwargs)
        return len(self._store[key])

    async def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        if key not in self._store:
            self._store[key] = {}
        current = int(self._store[key].get(field, 0))
        self._store[key][field] = str(current + amount)
        return current + amount 

    async def hgetall(self, key: str) -> dict:
        return dict(self._store.get(key, {}))

    async def keys(self, pattern: str = "*") -> list:
        import fnmatch
        return [k for k in self._store.keys() if fnmatch.fnmatch(k, pattern)]


class RedisClient:
    """
    Manages the Redis connection with automatic fallback to in-memory store.
    """

    def __init__(self):
        self._redis = None
        self._fallback = InMemoryFallback()
        self._using_fallback = False

    async def connect(self) -> None:
        try:
            self._redis = aioredis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                password=settings.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await self._redis.ping()
            self._using_fallback = False
            logger.info("Connected to Redis successfully")

        except Exception as e:
            logger.warning(f"Redis unavailable: {e}. Using in-memory fallback.")
            self._redis = None
            self._using_fallback = True

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.aclose()
            logger.info("Redis connection closed")

    @property
    def client(self):
        if self._using_fallback or self._redis is None:
            return self._fallback
        return self._redis

    @property
    def is_fallback(self) -> bool:
        return self._using_fallback


redis_client = RedisClient()


async def get_redis():
    return redis_client.client