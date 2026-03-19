import logging
from fastapi import APIRouter, HTTPException, Header, Depends
from app.admin.schemas import CreateKeyRequest, CreateKeyResponse, KeySummary
from app.auth.key_manager import KeyManager
from app.dependencies import get_key_manager
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/admin", tags=["Admin"])


def verify_admin(x_admin_key: str = Header(...)):
    """
    Simple admin authentication.
    Checks the X-Admin-Key header against our secret from .env.
    Completely separate from API key auth — admins use a different credential.
    """
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_admin_key",
                "message": "Invalid admin key"
            }
        )
    return x_admin_key


@router.post("/keys", response_model=CreateKeyResponse)
async def create_key(
    body: CreateKeyRequest,
    key_manager: KeyManager = Depends(get_key_manager),
    _: str = Depends(verify_admin),
):
    """
    Creates a new API key.
    Only callable with a valid X-Admin-Key header.
    """
    raw_key, api_key = await key_manager.create_key(
        name=body.name,
        scopes=body.scopes,
        tier=body.tier,
    )
    return CreateKeyResponse(
        raw_key=raw_key,
        key_id=api_key.key_id,
        name=api_key.name,
        scopes=api_key.scopes,
        tier=api_key.tier,
    )


@router.get("/keys", response_model=list[KeySummary])
async def list_keys(
    key_manager: KeyManager = Depends(get_key_manager),
    _: str = Depends(verify_admin),
):
    """Returns all API keys — without raw keys or hashes."""
    keys = await key_manager.list_keys()
    return [
        KeySummary(
            key_id=k.key_id,
            name=k.name,
            scopes=k.scopes,
            tier=k.tier,
            is_active=k.is_active,
            total_requests=k.total_requests,
            total_throttled=k.total_throttled,
        )
        for k in keys
    ]


@router.delete("/keys/{key_id}")
async def deactivate_key(
    key_id: str,
    key_manager: KeyManager = Depends(get_key_manager),
    _: str = Depends(verify_admin),
):
    """Deactivates a key. It stays in Redis for audit history."""
    success = await key_manager.deactivate_key(key_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail={"error": "key_not_found", "message": f"No key with id={key_id}"}
        )
    return {"message": f"Key {key_id} deactivated successfully"}