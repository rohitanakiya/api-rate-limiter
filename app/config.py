from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    app_name: str = "API Rate Limiter"
    debug: bool = False

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""

    # Admin
    admin_api_key: str = "change-me-in-production"

    # Rate limiting defaults
    default_requests_per_minute: int = 60
    default_bucket_capacity: int = 100
    default_refill_rate: float = 10.0  # tokens per second

    # Proxy / gateway mode
    # When upstream_url is set, requests to /gw/{path} are authenticated,
    # rate-limited, then forwarded to upstream_url + /{path}.
    # Leave empty to disable proxy mode.
    upstream_url: str = ""
    proxy_request_timeout: float = 30.0  # seconds

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
