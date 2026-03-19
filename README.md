# API Rate Limiter

A production-grade API rate limiting and key management system built with
FastAPI, Redis, and Python. Implements the same patterns used by Stripe,
GitHub, and AWS to protect their APIs.

## What this project does

Any valuable API needs to answer three questions on every request:

- **Who are you?** — API key authentication with HMAC-SHA256 hashing
- **Are you allowed?** — Scoped permissions (read/write/admin)
- **How many times today?** — Dual rate limiting with token bucket + sliding window

This project implements all three with a focus on correctness, security,
and resilience.

## Architecture
```
Incoming request
      │
      ▼
 API Gateway (FastAPI)
      │
      ▼
 Authentication          ← HMAC-SHA256 key verification, scope check
      │
      ▼
 Token Bucket            ← Controls burst rate (tokens refill over time)
      │
      ▼
 Sliding Window          ← Controls per-minute volume (rolling 60s window)
      │
      ├── Allowed → Backend Service + Metrics recorded
      │
      └── Blocked → 429 Too Many Requests + Retry-After header
```

## Rate Limiting Algorithms

### Token Bucket
Each API key has a bucket with N tokens. Every request costs 1 token.
Tokens refill at a fixed rate per second. Allows short bursts while
enforcing a long-term average rate.
```
tokens_now = min(capacity, tokens_last + (elapsed_seconds × refill_rate))
```

### Sliding Window
Tracks every request timestamp in a Redis sorted set. On each request,
entries older than 60 seconds are removed and the remaining count is
checked against the tier limit. Prevents burst abuse at window boundaries.

Both algorithms run on every request — token bucket catches burst abuse,
sliding window catches per-minute volume abuse.

## Security Design

**API keys are never stored.** When a key is created:
1. A cryptographically random key is generated (`secrets.token_urlsafe`)
2. It is shown to the user exactly once
3. An HMAC-SHA256 hash is computed and stored in Redis
4. Verification compares hashes using `hmac.compare_digest` (constant-time)

This means a database breach exposes no usable keys — only hashes.

**Constant-time comparison** prevents timing attacks where an attacker
measures response time differences to guess key characters one by one.

## API Key Tiers

| Tier       | Requests/min | Bucket Capacity | Refill Rate   |
|------------|-------------|-----------------|---------------|
| Free       | 20          | 20 tokens       | 0.33/sec      |
| Pro        | 100         | 100 tokens      | 1.67/sec      |
| Enterprise | 1000        | 1000 tokens     | 16.67/sec     |

## Redis Resilience

If Redis becomes unreachable, the system automatically switches to an
in-memory fallback store with the same interface. The API stays alive
with degraded (non-shared) state rather than crashing entirely.

This is called **graceful degradation** — a core principle in
distributed systems design.

## Tech Stack

- **FastAPI** — async Python web framework with dependency injection
- **Redis** — shared state for rate limit counters and API key storage
- **Pydantic** — data validation and settings management
- **Docker** — containerised Redis for local development
- **pytest + pytest-asyncio** — async test suite

## Project Structure
```
app/
├── main.py              # FastAPI app, lifespan, routes
├── config.py            # Settings from .env via pydantic-settings
├── redis_client.py      # Redis connection + in-memory fallback
├── dependencies.py      # FastAPI dependency injection (auth + limiting)
├── middleware.py        # Request fingerprinting
├── auth/
│   ├── key_manager.py   # Key generation, HMAC hashing, CRUD
│   └── models.py        # APIKey, KeyScope, KeyTier, TIER_LIMITS
├── limiter/
│   ├── token_bucket.py  # Token bucket algorithm
│   └── sliding_window.py # Sliding window algorithm
├── admin/
│   ├── router.py        # POST/GET/DELETE /admin/keys
│   └── schemas.py       # Request/response models
└── metrics/
    ├── collector.py     # Per-key and global counters
    └── router.py        # GET /metrics/me, /metrics/global
```

## Running Locally

**Prerequisites:** Docker, Python 3.11+
```bash
# 1. Clone and enter the project
git clone https://github.com/YOUR_USERNAME/api-rate-limiter
cd api-rate-limiter

# 2. Start Redis
docker compose up -d

# 3. Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Configure environment
cp .env.example .env  # edit as needed

# 6. Start the server
uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for the interactive API documentation.

## API Endpoints

### Public
- `GET /` — Health and status
- `GET /health` — Health check for monitoring

### Admin (requires `X-Admin-Key` header)
- `POST /admin/keys` — Create a new API key
- `GET /admin/keys` — List all keys
- `DELETE /admin/keys/{key_id}` — Deactivate a key

### Protected (requires `X-API-Key` header)
- `GET /api/data` — Example read endpoint (requires `read` scope)
- `POST /api/data` — Example write endpoint (requires `write` scope)
- `GET /metrics/me` — Your key's usage stats
- `GET /metrics/global` — System-wide stats

## Creating and using an API key
```bash
# Create a key
curl -X POST http://localhost:8000/admin/keys \
  -H "X-Admin-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "My Key", "scopes": ["read"], "tier": "free"}'

# Use the key
curl http://localhost:8000/api/data \
  -H "X-API-Key: rl_your_returned_key"
```

## Running Tests
```bash
pytest tests/ -v
```

24 tests covering key generation, HMAC verification, token bucket
exhaustion, sliding window limits, tier isolation, and graceful
deactivation.

## Key Concepts Demonstrated

- **HMAC-SHA256 key hashing** with constant-time verification
- **Token bucket algorithm** for burst rate control
- **Sliding window algorithm** using Redis sorted sets
- **FastAPI dependency injection** for reusable auth + limiting logic
- **Atomic Redis operations** (`HINCRBY`) to prevent race conditions
- **Graceful degradation** with automatic Redis failover
- **Scoped permissions** (read/write/admin) per API key
- **TTL-based cleanup** — inactive buckets auto-expire from Redis
- **Retry-After headers** on 429 responses (RFC 6585 compliant)