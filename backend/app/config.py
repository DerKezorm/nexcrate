"""Settings from the environment, prefix ``NEXCRATE_``.

Only what has to be known before the first start lives here. What the operator
changes at runtime (the log mode, later sources and versions) lives in the
database.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import PrivateAttr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

DEFAULT_DATA_DIR = PROJECT_DIR / "data"
DEFAULT_FRONTEND_DIR = PROJECT_DIR / "frontend" / "dist"

logger = logging.getLogger("nexcrate.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXCRATE_",
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    #: Database, secret key, logs, backups. Belongs on a local disk, never on an SMB or NFS share.
    data_dir: Path = DEFAULT_DATA_DIR
    #: Key for signing and encryption. Empty means: generate one in ``data/secret.key``.
    secret_key: str = ""
    #: Fixes the log mode (quiet, normal, detailed, trace). Empty means: the mode set in the interface.
    log_level: str = ""
    #: ``auto`` follows the scheme of each request, ``on`` always, ``off`` never.
    cookie_secure: str = "auto"
    #: The built frontend. Served with a fallback for every path outside /api when the directory exists.
    frontend_dir: Path = DEFAULT_FRONTEND_DIR
    #: A sub path nexcrate answers under behind a proxy, such as ``/nexcrate`` (Arr's "URL Base"). Empty: the root.
    #: Takes effect at the next start. ``/api/health`` answers without it too, for the container's health check.
    url_base: str = ""

    _generated_key: str | None = PrivateAttr(default=None)

    @field_validator("data_dir", "frontend_dir", mode="before")
    @classmethod
    def _empty_means_default(cls, value: Any, info: Any) -> Any:
        # An empty line copied from .env.example must not turn into the project root.
        if value is None or (isinstance(value, str) and not value.strip()):
            return DEFAULT_DATA_DIR if info.field_name == "data_dir" else DEFAULT_FRONTEND_DIR
        return value

    @field_validator("data_dir", "frontend_dir")
    @classmethod
    def _relative_to_project(cls, value: Path) -> Path:
        # A relative path means the project, not the directory the server happened to be
        # started from. Otherwise a start from backend/ silently creates a second, empty database.
        return value if value.is_absolute() else PROJECT_DIR / value

    @field_validator("url_base", mode="before")
    @classmethod
    def _url_base(cls, value: Any) -> str:
        return normalize_url_base(value)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "nexcrate.db"

    @property
    def secret_key_file(self) -> Path:
        return self.data_dir / "secret.key"

    def resolved_secret_key(self) -> str:
        """``NEXCRATE_SECRET_KEY``, or the key in ``data/secret.key``, created on first use.

        ⚠️ Whoever holds this file can read every stored credential. It is created with
        mode 0600 from the start, not made private afterwards. Windows knows no mode bits;
        there a failing chmod is not an error.
        """
        if self.secret_key:
            return self.secret_key
        if self._generated_key:
            return self._generated_key
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.secret_key_file
        key = ""
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            key = path.read_text(encoding="utf-8").strip()
        else:
            key = secrets.token_urlsafe(48)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(key)
            logger.info("Generated a new secret key file. Back it up together with the database.")
        if not key:
            # An empty file is a start that died between creating and writing it.
            key = secrets.token_urlsafe(48)
            path.write_text(key, encoding="utf-8")
            logger.warning("The secret key file was empty, a new key was written.")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self._generated_key = key
        return key


_URL_BASE_PART = re.compile(r"[A-Za-z0-9._-]+")


def normalize_url_base(value: Any) -> str:
    """``/nexcrate`` from ``nexcrate``, ``/nexcrate/`` or ``/nexcrate``; empty for the root. Raises ``ValueError`` for
    anything else: a part may hold letters, digits, dot, dash and underscore, and none may be ``.`` or ``..``."""
    text = str(value or "").strip().strip("/")
    if not text:
        return ""
    parts = text.split("/")
    if any(not _URL_BASE_PART.fullmatch(part) or part in (".", "..") for part in parts):
        raise ValueError("NEXCRATE_URL_BASE is a path such as /nexcrate")
    return "/" + "/".join(parts)


@lru_cache
def get_settings() -> Settings:
    return Settings()
