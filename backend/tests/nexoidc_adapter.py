"""nexoidc contract tests: nexcrate's adapter (copied once from bauplaene/oidc, then nexcrate's file).

nexcrate has one account (blueprint 06). The contract tests name accounts ("alex", "owner", "max"); here every name is
that one account: ``make_account`` renames it and gives it the one password, so the operator, who is the same person,
signs in with the same. ``who`` answers the name the account carries now.

The app is the real one (``app.main.app``) on the test database that ``tests/conftest.py`` empties before every test.
Test values are made at run time; no password or token is written into this file.

This file is tracked, with ``tests/nexoidc/``, because the CI runs exactly these tests (``python -m pytest
tests/nexoidc``); the rest of the tests stay out of the repository. Next to the full test suite the conftest of
``tests/`` has set up the environment and empties the database before every test. Without it (the CI) the adapter does
both itself: a temporary data directory before ``app`` is imported, and an empty database for every test.
"""

from __future__ import annotations

import os
import secrets
import tempfile
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

#: True when nothing has set up the environment: the CI runs ``tests/nexoidc`` alone. The settings are read when
#: ``app`` is imported, so this comes first.
STANDALONE = "NEXCRATE_DATA_DIR" not in os.environ
if STANDALONE:
    _TMP = Path(tempfile.mkdtemp(prefix="nexcrate-oidc-tests-"))
    os.environ["NEXCRATE_DATA_DIR"] = str(_TMP / "data")
    os.environ["NEXCRATE_SECRET_KEY"] = "test-secret-key-only-for-tests"
    os.environ["NEXCRATE_LOG_LEVEL"] = ""
    os.environ["NEXCRATE_COOKIE_SECURE"] = "auto"
    os.environ["NEXCRATE_FRONTEND_DIR"] = str(_TMP / "no-frontend")

import httpx
from fastapi.testclient import TestClient
from oidc_contract import StandardAdapter
from sqlalchemy import delete, inspect

from app import crypto, migrations, security
from app import main as app_main
from app.db import SessionLocal, engine, get_setting, init_db, set_setting
from app.main import app
from app.models import ACCOUNT_ID, Account, Base
from app.routers import oidc as oidc_router
from app.routers.totp import PENDING_COOKIE
from app.security import hash_password
from app.services import anmeldebremse, jobs, oidc_store, totp, web


def _quick_init_db(bind: Any = None, backup_dir: Any = None) -> list[str]:
    """The schema never changes within a run, and the full ``init_db`` reflects every table (about 1.5 seconds per start of the
    app). After the first full run only the tables are looked at, as ``tests/conftest.py`` does."""
    if bind is not None or not set(Base.metadata.tables) <= set(inspect(engine).get_table_names()):
        return init_db(bind, backup_dir)
    migrations.store_latest(engine)
    return []


if STANDALONE:
    app_main.init_db = _quick_init_db
    # bcrypt with the real rounds costs about 0.3 seconds per hash; the mechanics stay the same (as in tests/conftest.py).
    security.BCRYPT_ROUNDS = 4

CSRF = {"X-Requested-With": "nexcrate"}
OPERATOR = "owner"
#: Failed attempts that hold the brake for minutes (each one past the free ones doubles the wait).
BRAKE_FAILURES = 12


class Adapter(StandardAdapter):
    package = "app.vendor.nexoidc"
    api = "/api"
    single_account = True
    only_authentik = False
    supports_coupling = False
    has_second_factor = True
    legacy_model = "single"
    # nexcrate asked no code after the provider before the list (``oidc_store.TRUSTED_BEFORE``).
    trusted_before = oidc_store.TRUSTED_BEFORE
    old_callback_paths: tuple[str, ...] = ()
    operator_name = OPERATOR
    anonymous_status: tuple[int, ...] = (401,)
    member_status: tuple[int, ...] = (403,)
    home = "/"
    login_page = "/login"
    account_page = "/konto"

    def start(self) -> None:
        self._password = secrets.token_urlsafe(12)
        self._seed: str | None = None
        self._legacy_written = False
        self._held_jobs = jobs.registered() if STANDALONE else []
        if STANDALONE:
            self._empty_database()
        self._running = TestClient(app, base_url="http://testserver", follow_redirects=False)
        self._running.__enter__()
        oidc_store.configure()
        with SessionLocal() as db:
            db.add(
                Account(id=ACCOUNT_ID, username=OPERATOR, password_hash=hash_password(self._password), language="en")
            )
            db.commit()
        self._operator: TestClient | None = None
        self._clients: list[TestClient] = []

    @staticmethod
    def _empty_database() -> None:
        """What the conftest of ``tests/`` does before every test, for the run without it."""
        if not set(Base.metadata.tables) <= set(inspect(engine).get_table_names()):
            init_db()
        with SessionLocal() as db:
            for table in reversed(Base.metadata.sorted_tables):
                db.execute(delete(table))
            db.commit()
        anmeldebremse.reset_all()
        # No background round next to a test (the conftest of ``tests/`` takes its jobs out the same way).
        for job in jobs.registered():
            jobs.unregister(job.name)

    def stop(self) -> None:
        for client in self._clients:
            client.close()
        self._running.__exit__(None, None, None)
        for job in self._held_jobs:
            jobs.register(job.name, job.interval_seconds, job.func, job.ready)

    def password_for(self, name: str) -> str:
        # One account, one password, whatever name a test gives it.
        return self._password

    def browser(self) -> TestClient:
        client = TestClient(app, base_url="http://testserver", follow_redirects=False)
        client.headers.update(CSRF)
        self._clients.append(client)
        return client

    def operator(self) -> TestClient:
        if self._operator is None:
            self._operator = self.browser()
            self.sign_in_with_password(self._operator, self._name())
        return self._operator

    @staticmethod
    def _name() -> str:
        with SessionLocal() as db:
            account = db.get(Account, ACCOUNT_ID)
            return account.username if account else ""

    def make_account(self, name: str, *, password: bool = True, second_factor: bool = False) -> int:
        with SessionLocal() as db:
            account = db.get(Account, ACCOUNT_ID)
            assert account is not None
            account.username = name
            if second_factor:
                self._seed = totp.generate_seed()
                totp.store(db, self._seed, totp.generate_recovery_codes(), 0)
            elif totp.enabled(db):
                totp.remove(db)
                self._seed = None
            db.commit()
        return ACCOUNT_ID

    def sign_in_with_password(self, client: Any, name: str) -> None:
        response = client.post("/api/auth/login", json={"username": name, "password": self._password})
        assert response.status_code == 200, response.text
        if response.json().get("second_factor"):
            assert self._seed is not None
            with SessionLocal() as db:
                # A code counts once; the tests sign in several times within one window of 30 seconds.
                set_setting(db, totp.LAST_STEP, "0")
                db.commit()
            code = client.post("/api/auth/login/totp", json={"code": totp.code_at(self._seed, time.time())})
            assert code.status_code == 200, code.text

    def who(self, client: Any) -> str | None:
        response = client.get("/api/auth/me")
        return response.json()["username"] if response.status_code == 200 else None

    def second_factor_asked(self, client: Any, response: httpx.Response) -> bool:
        return response.headers.get("location", "").endswith("step=code") and bool(client.cookies.get(PENDING_COOKIE))

    def make_invite(self, rights: str) -> str:
        raise AssertionError("nexcrate has one account and no invitations")

    def rights_of(self, name: str) -> str | None:
        return "member" if name == self._name() else None

    def account_names(self) -> list[str]:
        name = self._name()
        return [name] if name else []

    def store(self) -> AbstractContextManager[Any]:
        @contextmanager
        def opened() -> Iterator[oidc_store.SqlStore]:
            with SessionLocal() as db:
                yield oidc_store.SqlStore(db)

        return opened()

    def trip_throttle(self, client: Any) -> None:
        # nexcrate's brake counts per target, not per address (behind a proxy every visitor shares one).
        for _ in range(BRAKE_FAILURES):
            anmeldebremse.failed(oidc_router.BRAKE_KEY)

    def set_public_url(self, url: str) -> None:
        with SessionLocal() as db:
            set_setting(db, web.SETTING, web.normalize(url))
            db.commit()

    def read_public_url(self) -> str:
        # From the database, as a restart would find it.
        with SessionLocal() as db:
            return web.web_url(db) or ""

    def save_public_url_route(self, url: str, *, confirm: bool = False) -> httpx.Response:
        # Card 1 and the address under "About" save the public address through the settings of the system.
        return self.operator().put("/api/system-settings", json={"web_url": url, "confirm_address": confirm})

    def legacy_setup(
        self, *, issuer: str, client_id: str, client_secret: str, label: str, subjects: dict[str, str]
    ) -> None:
        name = self._name()
        with SessionLocal() as db:
            set_setting(db, oidc_store.ISSUER, issuer)
            set_setting(db, oidc_store.CLIENT_ID, client_id)
            set_setting(db, oidc_store.CLIENT_SECRET, crypto.encrypt(client_secret))
            set_setting(db, oidc_store.PROVIDER_NAME, label)
            set_setting(db, oidc_store.SUBJECT, subjects.get(name, ""))
            if not self._legacy_written:
                # A database of 0.8.1 has never seen the list: the flag, the anchor and the fingerprint the start of
                # this version set go. 0.8.1 does not know them, so what it does later leaves them as they are.
                set_setting(db, oidc_store.MIGRATED, "")
                set_setting(db, oidc_store.ANCHOR, "")
                set_setting(db, oidc_store.FINGERPRINT, "")
            db.commit()
        self._legacy_written = True

    def run_migration(self) -> None:
        oidc_store.migrate_settings(backed_up=True)

    def legacy_read(self) -> dict[str, Any]:
        with SessionLocal() as db:
            legacy, subjects = oidc_store.read_legacy(db)
        return {
            "issuer": legacy.issuer,
            "client_id": legacy.client_id,
            "client_secret": legacy.client_secret,
            "label": legacy.label,
            "auto_create": legacy.auto_create,
            "subjects": {self._name(): subject for _account_id, subject in subjects},
        }

    def legacy_settings(self) -> dict[str, str]:
        """The old settings as nexcrate 0.8.1 reads them (for nexcrate's own tests of the way back)."""
        with SessionLocal() as db:
            return {key: get_setting(db, key) for key in oidc_store.LEGACY_PROVIDER + oidc_store.LEGACY_LINK}


__all__ = ["Adapter"]
