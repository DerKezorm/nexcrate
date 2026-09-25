"""Password hashing (bcrypt) and session token helpers."""

from __future__ import annotations

import hashlib
import secrets
from functools import cache

import bcrypt

#: Cost factor. The tests lower it; the mechanics stay the same.
BCRYPT_ROUNDS = 12
#: bcrypt reads at most 72 bytes, and bcrypt 5 raises on longer input instead of cutting it.
_BCRYPT_MAX_BYTES = 72
#: Minimum password length in characters.
PASSWORD_MIN_LENGTH = 8
#: Length of the session reference that may appear in log lines.
SESSION_REF_LENGTH = 8


def _password_bytes(password: str) -> bytes:
    # surrogatepass: JSON allows lone surrogates, and a password must not turn into a 500.
    return password.encode("utf-8", "surrogatepass")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_password_bytes(password), password_hash.encode("ascii"))
    except ValueError:
        return False


@cache
def dummy_hash() -> str:
    """The hash a login with an unknown username is checked against.

    A wrong username then costs the same bcrypt check as a wrong password, and the
    answer time does not tell which one was wrong.
    """
    return hash_password(secrets.token_urlsafe(16))


def new_session_token() -> str:
    """43 characters of base64url. Leaves the backend once, in the session cookie."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


def session_ref(token_hash: str) -> str:
    """A short reference to a session for log lines. Never the token itself."""
    return token_hash[:SESSION_REF_LENGTH]
