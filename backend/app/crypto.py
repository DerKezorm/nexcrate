"""Encryption of stored credentials (Fernet).

API keys of Radarr and later services are stored encrypted, with the prefix
``enc:``. The key is derived from ``NEXCRATE_SECRET_KEY`` or the generated
``data/secret.key``.

⚠️ Changing or losing the secret key makes every stored credential unreadable.
That case is logged loudly as an error, with the likely cause. Nexview used to
return an empty value without a word, and two operators reported the vanished
connection as a riddle.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

logger = logging.getLogger("nexcrate.crypto")

PREFIX = "enc:"

_fernet_cache: Fernet | None = None


def _fernet() -> Fernet:
    global _fernet_cache
    if _fernet_cache is None:
        secret = get_settings().resolved_secret_key().encode("utf-8")
        # A prefix of its own, so this key differs from any other use of the same secret.
        digest = hashlib.sha256(b"nexcrate-credentials:" + secret).digest()
        _fernet_cache = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet_cache


def forget_key() -> None:
    """Drop the derived key; the next call derives it again. For tests and a key change."""
    global _fernet_cache
    _fernet_cache = None


def is_encrypted(value: str) -> bool:
    return value.startswith(PREFIX)


def encrypt(value: str) -> str:
    return PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    """The plain value, or an empty string when it cannot be read, with a loud log line."""
    if not value:
        return ""
    if not is_encrypted(value):
        logger.warning("A stored credential is not encrypted. It is used as it is; saving it again encrypts it.")
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.error(
            "A stored credential cannot be decrypted with the current secret key. "
            "Was NEXCRATE_SECRET_KEY changed, or was data/secret.key lost when the container "
            "was recreated? The affected credentials have to be entered again."
        )
        return ""
