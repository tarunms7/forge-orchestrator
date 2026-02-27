"""Authentication REST endpoints: register and login."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr

from forge.api.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Request / response schemas ──────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
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
    refresh_token: str
    user: UserOut


# ── Endpoints ────────────────────────────────────────────────────────

@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(body: RegisterRequest, request: Request) -> AuthResponse:
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

    return AuthResponse(**result)


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, request: Request) -> AuthResponse:
    """Authenticate and receive access + refresh tokens."""
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

    return AuthResponse(**result)
