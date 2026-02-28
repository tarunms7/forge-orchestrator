"""Authentication REST endpoints: register, login, and refresh."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from forge.api.security.jwt import create_access_token, decode_token
from forge.api.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


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
        path="/",  # Must be "/" so cookie is sent to /api/auth/refresh
    )


def _build_auth_response(result: dict, *, status_code: int = 200) -> JSONResponse:
    """Build a JSONResponse with access_token in body, refresh_token in cookie."""
    body = {
        "access_token": result["access_token"],
        "user": result["user"],
    }
    response = JSONResponse(content=body, status_code=status_code)
    _set_refresh_cookie(response, result["refresh_token"])
    return response


# ── Endpoints ────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
async def register(body: RegisterRequest, request: Request) -> JSONResponse:
    """Register a new user account."""
    session_factory = request.app.state.async_session
    jwt_secret = request.app.state.jwt_secret

    async with session_factory() as session:
        svc = AuthService(session, jwt_secret=jwt_secret)
        try:
            result = await svc.register(
                email=body.email,
                password=body.password,
                display_name=body.display_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    return _build_auth_response(result, status_code=201)


@router.post("/login")
async def login(body: LoginRequest, request: Request) -> JSONResponse:
    """Authenticate and receive access token (refresh token set as cookie)."""
    session_factory = request.app.state.async_session
    jwt_secret = request.app.state.jwt_secret

    async with session_factory() as session:
        svc = AuthService(session, jwt_secret=jwt_secret)
        try:
            result = await svc.login(
                email=body.email,
                password=body.password,
            )
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc))

    return _build_auth_response(result)


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
        new_access = create_access_token(
            subject=payload["sub"], secret=request.app.state.jwt_secret
        )
        return {"access_token": new_access}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
