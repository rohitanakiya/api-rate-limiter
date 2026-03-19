from pydantic import BaseModel
from typing import List, Optional
from app.auth.models import KeyScope, KeyTier


class CreateKeyRequest(BaseModel):
    """What the caller must send to create a new API key."""
    name: str
    scopes: List[KeyScope] = [KeyScope.READ]
    tier: KeyTier = KeyTier.FREE


class CreateKeyResponse(BaseModel):
    """
    What we send back after creating a key.
    raw_key is shown ONCE here — never retrievable again.
    """
    raw_key: str
    key_id: str
    name: str
    scopes: List[KeyScope]
    tier: KeyTier
    message: str = "Store this key safely. It will never be shown again."


class KeySummary(BaseModel):
    """Safe representation of a key — no raw key, no hash."""
    key_id: str
    name: str
    scopes: List[KeyScope]
    tier: KeyTier
    is_active: bool
    total_requests: int
    total_throttled: int