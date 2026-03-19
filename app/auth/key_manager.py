import hashlib
import hmac
import secrets
import time
import logging
from typing import Optional, Tuple
from app.auth.models import APIKey, KeyScope, KeyTier, TIER_LIMITS
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Prefix so people know what kind of key it is — like GitHub's "ghp_" prefix
KEY_PREFIX = "rl_"


def generate_api_key() -> Tuple[str, str]:
    """
    Generates a new API key.

    Returns a tuple of:
    - raw_key: shown to the user ONCE, never stored
    - key_hash: stored in Redis, used for verification
    """
    raw_key = KEY_PREFIX + secrets.token_urlsafe(32)
    key_hash = _hash_key(raw_key)
    return raw_key, key_hash


def _hash_key(raw_key: str) -> str:
    """
    Hashes a raw API key using HMAC-SHA256.
    The admin_api_key acts as a secret salt — 
    even if two users have identical keys, hashes differ.
    """
    return hmac.new(
        settings.admin_api_key.encode(),
        raw_key.encode(),
        hashlib.sha256
    ).hexdigest()


def verify_api_key(raw_key: str, stored_hash: str) -> bool:
    """
    Safely compares a raw key against a stored hash.
    Uses hmac.compare_digest instead of == to prevent timing attacks.
    """
    if not raw_key or not stored_hash:
        return False
    expected_hash = _hash_key(raw_key)
    return hmac.compare_digest(expected_hash, stored_hash)


class KeyManager:
    """
    Handles all API key operations: create, get, list, deactivate.
    All data is stored in Redis as hash maps.
    """

    def __init__(self, redis_client):
        self.redis = redis_client

    def _key_redis_key(self, key_id: str) -> str:
        """Builds the Redis storage key for an API key"""
        return f"apikey:{key_id}"

    async def create_key(
        self,
        name: str,
        scopes: list[KeyScope] = None,
        tier: KeyTier = KeyTier.FREE,
    ) -> Tuple[str, APIKey]:
        """
        Creates a new API key.
        Returns (raw_key, APIKey) — raw_key must be shown to user immediately.
        """
        if scopes is None:
            scopes = [KeyScope.READ]

        raw_key, key_hash = generate_api_key()

        # key_id is derived from the hash — public identifier, not secret
        key_id = key_hash[:16]

        api_key = APIKey(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            scopes=scopes,
            tier=tier,
        )

        # Store in Redis as a hash map
        await self.redis.hset(
            self._key_redis_key(key_id),
            mapping=api_key.to_redis_dict()
        )

        logger.info(f"Created API key '{name}' with id={key_id} tier={tier}")
        return raw_key, api_key

    async def get_key_by_raw(self, raw_key: str) -> Optional[APIKey]:
        """
        Finds and returns an APIKey by verifying a raw key.
        This is called on every single API request.
        """
        if not raw_key or not raw_key.startswith(KEY_PREFIX):
            return None

        key_hash = _hash_key(raw_key)
        key_id = key_hash[:16]

        data = await self.redis.hgetall(self._key_redis_key(key_id))

        if not data:
            return None

        api_key = APIKey.from_redis_dict(data)

        # Verify hash matches and key is active
        if not verify_api_key(raw_key, api_key.key_hash):
            return None

        if not api_key.is_active:
            return None

        return api_key

    async def get_key_by_id(self, key_id: str) -> Optional[APIKey]:
        """Fetches an API key directly by its ID"""
        data = await self.redis.hgetall(self._key_redis_key(key_id))
        if not data:
            return None
        return APIKey.from_redis_dict(data)

    async def list_keys(self) -> list[APIKey]:
        """Returns all API keys stored in Redis"""
        pattern = "apikey:*"
        keys = await self.redis.keys(pattern)
        result = []
        for k in keys:
            data = await self.redis.hgetall(k)
            if data:
                result.append(APIKey.from_redis_dict(data))
        return result

    async def deactivate_key(self, key_id: str) -> bool:
        """Deactivates a key without deleting it — keeps audit history"""
        redis_key = self._key_redis_key(key_id)
        data = await self.redis.hgetall(redis_key)
        if not data:
            return False
        await self.redis.hset(redis_key, mapping={"is_active": "False"})
        logger.info(f"Deactivated API key id={key_id}")
        return True

    async def update_usage(
        self, key_id: str, was_throttled: bool = False
    ) -> None:
        """Updates request count and last_used timestamp after each request."""
        redis_key = self._key_redis_key(key_id)

        # Check key exists first
        data = await self.redis.hgetall(redis_key)
        if not data:
            return

        # Use atomic increments instead of read-modify-write
        await self.redis.hincrby(redis_key, "total_requests", 1)
        if was_throttled:
            await self.redis.hincrby(redis_key, "total_throttled", 1)

        await self.redis.hset(
            redis_key,
            mapping={"last_used_at": str(time.time())},
        )