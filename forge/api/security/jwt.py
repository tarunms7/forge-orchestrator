"""JWT token creation and verification utilities."""

from __future__ import annotations

import time

from jose import JWTError, jwt

ALGORITHM = "HS256"
DEFAULT_ACCESS_TTL = 1800  # 30 minutes
DEFAULT_REFRESH_TTL = 604800  # 7 days


def create_access_token(
    *,
    subject: str,
    secret: str,
    expires_delta_seconds: int = DEFAULT_ACCESS_TTL,
) -> str:
    """Create a signed JWT access token.

    Args:
        subject: The ``sub`` claim (typically a user ID).
        secret: HMAC signing key.
        expires_delta_seconds: Seconds until expiry. May be negative for
            testing expired tokens.
    """
    now = time.time()
    payload = {
        "sub": subject,
        "type": "access",
        "iat": int(now),
        "exp": int(now) + expires_delta_seconds,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def create_refresh_token(
    *,
    subject: str,
    secret: str,
    expires_delta_seconds: int = DEFAULT_REFRESH_TTL,
) -> str:
    """Create a signed JWT refresh token.

    Args:
        subject: The ``sub`` claim (typically a user ID).
        secret: HMAC signing key.
        expires_delta_seconds: Seconds until expiry.
    """
    now = time.time()
    payload = {
        "sub": subject,
        "type": "refresh",
        "iat": int(now),
        "exp": int(now) + expires_delta_seconds,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, *, secret: str) -> dict:
    """Decode and verify a JWT token.

    Args:
        token: The encoded JWT string.
        secret: HMAC signing key used to verify the signature.

    Returns:
        The decoded payload dictionary.

    Raises:
        jose.JWTError: If the token is expired, tampered with, or invalid.
    """
    return jwt.decode(token, secret, algorithms=[ALGORITHM])
