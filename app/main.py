import logging
import logging.config
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from app.config import get_settings
from app.redis_client import redis_client
from app.dependencies import verify_request, require_scope
from app.auth.models import APIKey, KeyScope
from app.admin.router import router as admin_router
from app.metrics.router import router as metrics_router
from app.proxy.router import router as proxy_router

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs startup and shutdown logic.
    'yield' is the point where the app is alive and serving requests.
    Everything before yield = startup. Everything after = shutdown.
    """
    logger.info(f"Starting {settings.app_name}")
    await redis_client.connect()
    logger.info(f"Redis connected. Fallback mode: {redis_client.is_fallback}")
    if settings.upstream_url:
        logger.info(f"Gateway proxy enabled. Forwarding /gw/* to {settings.upstream_url}")
    else:
        logger.info("Gateway proxy disabled (UPSTREAM_URL not set).")
    yield
    await redis_client.disconnect()
    logger.info("Shutdown complete")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="API Rate Limiter",
    description="Production-grade rate limiting with token bucket, sliding window, and scoped API keys",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(admin_router)
app.include_router(metrics_router)
# Proxy router is always mounted; it returns 503 if UPSTREAM_URL isn't set.
# Mounting unconditionally keeps the route table predictable and lets
# operators flip the flag in .env without restarting code paths.
app.include_router(proxy_router)


# ── Routes ───────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    """Public endpoint — no auth required."""
    return {
        "name": settings.app_name,
        "version": "1.0.0",
        "status": "operational",
        "redis_mode": "fallback" if redis_client.is_fallback else "redis",
        "gateway": "enabled" if settings.upstream_url else "disabled",
    }


@app.get("/health")
async def health():
    """Health check endpoint for monitoring systems."""
    return {
        "status": "healthy",
        "redis": "fallback" if redis_client.is_fallback else "connected",
    }


@app.get("/api/data")
async def get_data(api_key: APIKey = Depends(require_scope(KeyScope.READ))):
    """
    Protected endpoint — requires a valid API key with READ scope.
    This is the endpoint we use to test rate limiting.
    """
    return {
        "message": "Here is your data",
        "requested_by": api_key.name,
        "tier": api_key.tier,
    }


@app.post("/api/data")
async def post_data(api_key: APIKey = Depends(require_scope(KeyScope.WRITE))):
    """Protected endpoint — requires WRITE scope."""
    return {
        "message": "Data written successfully",
        "written_by": api_key.name,
    }
