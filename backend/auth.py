"""
API Key authentication middleware.

All endpoints (except health checks) require a valid API key
sent via the X-API-Key header.
"""

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from backend.config import get_settings

# Header-based API key scheme
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    api_key: str | None = Security(_api_key_header),
) -> str:
    """
    FastAPI dependency that validates the X-API-Key header.
    If API_SECRET_KEY is the default placeholder, authentication is disabled.
    """
    settings = get_settings()

    # If no secret configured, skip auth entirely (local/personal use)
    if settings.api_secret_key == "change_this_to_a_strong_random_secret":
        return "no-auth"

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if api_key != settings.api_secret_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return api_key
