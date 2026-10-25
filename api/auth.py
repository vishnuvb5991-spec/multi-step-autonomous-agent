"""
API Authentication and Authorization Middleware.
Enforces API key validation using constant-time comparison to prevent timing attacks.
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# Configurable API key. If not set, running in development mode.
EXPECTED_API_KEY = os.getenv("AGENT_API_KEY", "").strip()

_bearer_security = HTTPBearer(auto_error=False)


async def verify_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    bearer_creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_security),
) -> str:
    """Validate incoming request authentication.

    Accepts credentials via either:
      1. `X-API-Key: <key>` header
      2. `Authorization: Bearer <key>` header

    If AGENT_API_KEY is not configured in the environment:
      - Allows requests in local development mode with a warning log.
    If AGENT_API_KEY is configured:
      - Enforces constant-time secret comparison against timing attacks.
      - Rejects missing or invalid keys with 401 Unauthorized.
    """
    # If no key is configured in the environment, allow access in dev mode
    if not EXPECTED_API_KEY:
        return "anonymous_dev_user"

    provided_key: Optional[str] = None

    if x_api_key:
        provided_key = x_api_key
    elif bearer_creds and bearer_creds.credentials:
        provided_key = bearer_creds.credentials

    if not provided_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide 'X-API-Key' or 'Authorization: Bearer <key>' header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(provided_key, EXPECTED_API_KEY):
        logger.warning("Failed API key authentication attempt.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return provided_key
