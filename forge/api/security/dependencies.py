"""Shared authentication dependencies for FastAPI routes."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from forge.api.security.jwt import decode_token

security = HTTPBearer(auto_error=False)
_security_dep = Depends(security)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = _security_dep,
) -> str:
    """Extract and verify JWT token. Returns user_id.

    Raises:
        HTTPException: 401 if token is missing or invalid.
    """
    if getattr(request.app.state, 'auth_disabled', False):
        return 'local'
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials, secret=request.app.state.jwt_secret)
        return payload["sub"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
