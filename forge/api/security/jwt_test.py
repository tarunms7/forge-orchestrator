"""Tests for JWT token utilities."""

import time

import pytest
from jose import JWTError


SECRET = "test-secret-key-for-jwt"


def test_create_and_decode_access_token():
    """An access token should round-trip through create and decode."""
    from forge.api.security.jwt import create_access_token, decode_token

    token = create_access_token(subject="user-123", secret=SECRET)
    payload = decode_token(token, secret=SECRET)

    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"
    assert "exp" in payload
    assert "iat" in payload


def test_create_and_decode_refresh_token():
    """A refresh token should round-trip through create and decode."""
    from forge.api.security.jwt import create_refresh_token, decode_token

    token = create_refresh_token(subject="user-456", secret=SECRET)
    payload = decode_token(token, secret=SECRET)

    assert payload["sub"] == "user-456"
    assert payload["type"] == "refresh"


def test_expired_token_raises():
    """An expired token should raise JWTError on decode."""
    from forge.api.security.jwt import create_access_token, decode_token

    # Create a token that expires immediately (negative ttl)
    token = create_access_token(subject="user-789", secret=SECRET, expires_delta_seconds=-1)

    with pytest.raises(JWTError):
        decode_token(token, secret=SECRET)


def test_invalid_secret_raises():
    """Decoding with wrong secret should raise JWTError."""
    from forge.api.security.jwt import create_access_token, decode_token

    token = create_access_token(subject="user-abc", secret=SECRET)

    with pytest.raises(JWTError):
        decode_token(token, secret="wrong-secret")


def test_access_token_default_expiry():
    """Access token should have a reasonable default expiry (15-60 min)."""
    from forge.api.security.jwt import create_access_token, decode_token

    token = create_access_token(subject="user-exp", secret=SECRET)
    payload = decode_token(token, secret=SECRET)

    now = time.time()
    exp = payload["exp"]
    # Should expire between 10 minutes and 2 hours from now
    assert exp > now + 600
    assert exp < now + 7200


def test_refresh_token_longer_expiry():
    """Refresh token should have a longer expiry than access token."""
    from forge.api.security.jwt import create_access_token, create_refresh_token, decode_token

    access = create_access_token(subject="user-cmp", secret=SECRET)
    refresh = create_refresh_token(subject="user-cmp", secret=SECRET)

    access_payload = decode_token(access, secret=SECRET)
    refresh_payload = decode_token(refresh, secret=SECRET)

    assert refresh_payload["exp"] > access_payload["exp"]
