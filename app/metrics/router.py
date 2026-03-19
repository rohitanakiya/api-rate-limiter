from fastapi import APIRouter, Depends
from app.metrics.collector import MetricsCollector
from app.dependencies import get_metrics, verify_request
from app.auth.models import APIKey

router = APIRouter(prefix="/metrics", tags=["Metrics"])


@router.get("/me")
async def my_metrics(
    api_key: APIKey = Depends(verify_request),
    metrics: MetricsCollector = Depends(get_metrics),
):
    """
    Returns metrics for the currently authenticated API key.
    Any valid key can check its own usage.
    """
    stats = await metrics.get_stats(api_key.key_id)
    return {
        "key_id": api_key.key_id,
        "name": api_key.name,
        "tier": api_key.tier,
        "stats": stats,
    }


@router.get("/global")
async def global_metrics(
    metrics: MetricsCollector = Depends(get_metrics),
    api_key: APIKey = Depends(verify_request),
):
    """System-wide metrics. Available to all authenticated keys."""
    return await metrics.get_global_stats()