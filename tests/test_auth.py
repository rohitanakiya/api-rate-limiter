import pytest
from app.auth.key_manager import generate_api_key, verify_api_key, KeyManager
from app.auth.models import KeyScope, KeyTier, APIKey
from app.redis_client import InMemoryFallback


@pytest.fixture
def redis():
    """
    Every test gets a fresh in-memory Redis.
    No real Redis needed — tests run anywhere, anytime.
    This is called a test fixture — shared setup code for multiple tests.
    """
    return InMemoryFallback()


@pytest.fixture
def key_manager(redis):
    """Gives each test a KeyManager connected to the fake Redis."""
    return KeyManager(redis)


class TestKeyGeneration:

    def test_raw_key_has_prefix(self):
        """Keys must start with 'rl_' so users know what type of key it is."""
        raw_key, _ = generate_api_key()
        assert raw_key.startswith("rl_")

    def test_raw_key_and_hash_are_different(self):
        """The hash stored in Redis must never equal the raw key."""
        raw_key, key_hash = generate_api_key()
        assert raw_key != key_hash

    def test_same_key_produces_same_hash(self):
        """
        HMAC is deterministic — same input always gives same output.
        This is how we verify keys on each request without storing them.
        """
        raw_key, hash1 = generate_api_key()
        from app.auth.key_manager import _hash_key
        hash2 = _hash_key(raw_key)
        assert hash1 == hash2

    def test_different_keys_produce_different_hashes(self):
        """Two different keys must never produce the same hash."""
        _, hash1 = generate_api_key()
        _, hash2 = generate_api_key()
        assert hash1 != hash2


class TestKeyVerification:

    def test_correct_key_verifies(self):
        """A raw key must verify successfully against its own hash."""
        raw_key, key_hash = generate_api_key()
        assert verify_api_key(raw_key, key_hash) is True

    def test_wrong_key_fails(self):
        """A different raw key must not verify against a hash."""
        raw_key, key_hash = generate_api_key()
        assert verify_api_key("rl_wrongkey", key_hash) is False

    def test_empty_key_fails(self):
        """Empty strings must never verify."""
        _, key_hash = generate_api_key()
        assert verify_api_key("", key_hash) is False

    def test_empty_hash_fails(self):
        """Empty hash must never verify."""
        raw_key, _ = generate_api_key()
        assert verify_api_key(raw_key, "") is False


class TestKeyManager:

    async def test_create_and_retrieve_key(self, key_manager):
        """
        Create a key then immediately retrieve it.
        The retrieved key must match what we created.
        """
        raw_key, created = await key_manager.create_key(
            name="Test Key",
            scopes=[KeyScope.READ],
            tier=KeyTier.FREE,
        )
        retrieved = await key_manager.get_key_by_raw(raw_key)
        assert retrieved is not None
        assert retrieved.name == "Test Key"
        assert retrieved.tier == KeyTier.FREE
        assert KeyScope.READ in retrieved.scopes

    async def test_invalid_key_returns_none(self, key_manager):
        """A made-up key must return None, not crash."""
        result = await key_manager.get_key_by_raw("rl_fakekeythatdoesnotexist")
        assert result is None

    async def test_deactivated_key_is_rejected(self, key_manager):
        """
        After deactivation, the key must be rejected even if the hash matches.
        Deactivation is how you revoke access without deleting audit history.
        """
        raw_key, created = await key_manager.create_key(
            name="Soon Deactivated",
            scopes=[KeyScope.READ],
            tier=KeyTier.FREE,
        )
        await key_manager.deactivate_key(created.key_id)
        result = await key_manager.get_key_by_raw(raw_key)
        assert result is None

    async def test_list_keys_returns_all(self, key_manager):
        """List must return all created keys."""
        await key_manager.create_key("Key One", tier=KeyTier.FREE)
        await key_manager.create_key("Key Two", tier=KeyTier.PRO)
        keys = await key_manager.list_keys()
        assert len(keys) == 2

    async def test_usage_updates_after_request(self, key_manager):
        """
        After a request, total_requests must increment.
        This proves our metrics tracking works.
        """
        raw_key, created = await key_manager.create_key(
            name="Usage Test",
            tier=KeyTier.FREE,
        )
        await key_manager.update_usage(created.key_id, was_throttled=False)
        updated = await key_manager.get_key_by_id(created.key_id)
        assert updated.total_requests == 1