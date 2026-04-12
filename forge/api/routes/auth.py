"""Authentication REST endpoints: register, login, and refresh."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from forge.api.security.jwt import create_access_token, create_refresh_token, decode_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# ── In-memory rate limiter ────────────────────────────────────────────
# State is stored on app.state (rate_limit_store, rate_limit_last_cleanup)
# so each app instance gets its own isolated rate-limit state.

RATE_LIMIT_MAX_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 60
_CLEANUP_INTERVAL = 300.0  # purge stale entries every 5 minutes


def _cleanup_rate_limit_store(request: Request) -> None:
    """Remove entries older than the rate-limit window (periodic)."""
    store: dict[str, list[float]] = request.app.state.rate_limit_store
    now = time.monotonic()
    last_cleanup = getattr(request.app.state, "rate_limit_last_cleanup", 0.0)
    if now - last_cleanup < _CLEANUP_INTERVAL:
        return
    request.app.state.rate_limit_last_cleanup = now
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    stale_keys = [k for k, v in store.items() if not v or v[-1] < cutoff]
    for k in stale_keys:
        del store[k]


def _check_rate_limit(request: Request) -> None:
    """Raise 429 if the client IP has exceeded the request limit."""
    client_ip = request.client.host if request.client else "unknown"
    _cleanup_rate_limit_store(request)
    store: dict[str, list[float]] = request.app.state.rate_limit_store
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    timestamps = store.get(client_ip, [])
    # Drop timestamps outside the window
    timestamps = [t for t in timestamps if t > cutoff]
    if len(timestamps) >= RATE_LIMIT_MAX_REQUESTS:
        store[client_ip] = timestamps
        logger.warning("Rate limit exceeded for IP %s", client_ip)
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later.",
        )
    timestamps.append(now)
    store[client_ip] = timestamps


# ── Request / response schemas ──────────────────────────────────────


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str


class AuthResponse(BaseModel):
    access_token: str
    user: UserOut


# ── Helpers ──────────────────────────────────────────────────────────


def _set_refresh_cookie(response: JSONResponse, refresh_token: str) -> None:
    """Set the refresh token as an httpOnly cookie on the response."""
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=7 * 24 * 60 * 60,  # 7 days
        path="/",
    )


def _build_auth_response(user, jwt_secret: str, *, status_code: int = 200) -> JSONResponse:
    """Build a JSONResponse with access_token in body, refresh_token in cookie."""
    access = create_access_token(subject=user.id, secret=jwt_secret, display_name=user.display_name)
    refresh = create_refresh_token(
        subject=user.id, secret=jwt_secret, display_name=user.display_name
    )
    body = {
        "access_token": access,
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
        },
    }
    response = JSONResponse(content=body, status_code=status_code)
    _set_refresh_cookie(response, refresh)
    return response


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/register", status_code=201)
async def register(body: RegisterRequest, request: Request) -> JSONResponse:
    """Register a new user account."""
    _check_rate_limit(request)
    db = request.app.state.db
    jwt_secret = request.app.state.jwt_secret

    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        user = await db.create_user(
            email=body.email,
            password=body.password,
            display_name=body.display_name,
        )
    except ValueError as exc:
        logger.warning("Registration failed: %s", exc)
        raise HTTPException(status_code=409, detail="Account registration failed")

    return _build_auth_response(user, jwt_secret, status_code=201)


@router.post("/login")
async def login(body: LoginRequest, request: Request) -> JSONResponse:
    """Authenticate and receive access token (refresh token set as cookie)."""
    _check_rate_limit(request)
    db = request.app.state.db
    jwt_secret = request.app.state.jwt_secret

    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    from forge.storage.db import Database

    user = await db.get_user_by_email(body.email)
    if user is None or not Database.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    return _build_auth_response(user, jwt_secret)


@router.post("/refresh")
async def refresh(request: Request) -> dict:
    """Exchange a valid refresh token cookie for a new access token."""
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = decode_token(refresh_token, secret=request.app.state.jwt_secret)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        if payload.get("exp", 0) < time.time():
            raise HTTPException(status_code=401, detail="Refresh token expired")
        new_access = create_access_token(
            subject=payload["sub"],
            secret=request.app.state.jwt_secret,
            display_name=payload.get("dn"),
        )
        return {"access_token": new_access}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
