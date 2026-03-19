from pydantic import BaseModel, Field
from typing import List
from enum import Enum
import time


class KeyScope(str, Enum):
    """
    Defines what actions an API key is allowed to perform.
    Like different permission levels on a key card.
    """
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class KeyTier(str, Enum):
    """
    Defines the rate limit tier for an API key.
    Higher tier = more requests allowed per minute.
    """
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


TIER_LIMITS = {
    KeyTier.FREE: {
        "requests_per_minute": 20,
        "bucket_capacity": 20,
        "refill_rate": 0.33,
    },
    KeyTier.PRO: {
        "requests_per_minute": 100,
        "bucket_capacity": 100,
        "refill_rate": 1.67,
    },
    KeyTier.ENTERPRISE: {
        "requests_per_minute": 1000,
        "bucket_capacity": 1000,
        "refill_rate": 16.67,
    },
}


class APIKey(BaseModel):
    """
    Represents a single API key and all its metadata.
    This is what gets stored in Redis for each key.
    """
    key_id: str
    key_hash: str
    name: str
    scopes: List[KeyScope] = [KeyScope.READ]
    tier: KeyTier = KeyTier.FREE
    is_active: bool = True
    created_at: float = Field(default_factory=time.time)
    last_used_at: float = Field(default_factory=time.time)
    total_requests: int = 0
    total_throttled: int = 0

    def to_redis_dict(self) -> dict:
        return {
            "key_id": self.key_id,
            "key_hash": self.key_hash,
            "name": self.name,
            "scopes": ",".join(self.scopes),
            "tier": self.tier,
            "is_active": str(self.is_active),
            "created_at": str(self.created_at),
            "last_used_at": str(self.last_used_at),
            "total_requests": str(self.total_requests),
            "total_throttled": str(self.total_throttled),
        }

    @classmethod
    def from_redis_dict(cls, data: dict) -> "APIKey":
        return cls(
            key_id=data["key_id"],
            key_hash=data["key_hash"],
            name=data["name"],
            scopes=data["scopes"].split(","),
            tier=data["tier"],
            is_active=data["is_active"] == "True",
            created_at=float(data["created_at"]),
            last_used_at=float(data["last_used_at"]),
            total_requests=int(data["total_requests"]),
            total_throttled=int(data["total_throttled"]),
        )