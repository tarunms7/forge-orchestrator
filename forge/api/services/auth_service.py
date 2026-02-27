"""Authentication service: register and login."""

from __future__ import annotations

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.models.user import UserRow
from forge.api.security.jwt import create_access_token, create_refresh_token


def _hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


class AuthService:
    """Handles user registration and login with bcrypt password hashing."""

    def __init__(self, session: AsyncSession, *, jwt_secret: str) -> None:
        self._session = session
        self._jwt_secret = jwt_secret

    async def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
    ) -> dict:
        """Register a new user.

        Returns:
            Dict with ``access_token``, ``refresh_token``, and ``user`` info.

        Raises:
            ValueError: If the email is already registered.
        """
        # Check for existing user
        result = await self._session.execute(
            select(UserRow).where(UserRow.email == email)
        )
        if result.scalar_one_or_none() is not None:
            raise ValueError(f"Email {email} is already registered")

        # Create user with hashed password
        user = UserRow(
            email=email,
            password_hash=_hash_password(password),
            display_name=display_name,
        )
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)

        return self._build_response(user)

    async def login(self, *, email: str, password: str) -> dict:
        """Authenticate a user by email and password.

        Returns:
            Dict with ``access_token``, ``refresh_token``, and ``user`` info.

        Raises:
            ValueError: If email not found or password does not match.
        """
        result = await self._session.execute(
            select(UserRow).where(UserRow.email == email)
        )
        user = result.scalar_one_or_none()

        if user is None or not _verify_password(password, user.password_hash):
            raise ValueError("Invalid email or password")

        return self._build_response(user)

    def _build_response(self, user: UserRow) -> dict:
        """Build the token + user response dict."""
        return {
            "access_token": create_access_token(
                subject=user.id, secret=self._jwt_secret
            ),
            "refresh_token": create_refresh_token(
                subject=user.id, secret=self._jwt_secret
            ),
            "user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name,
            },
        }
