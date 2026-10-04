"""The second factor: time-based one-time codes (RFC 6238) plus recovery codes, as in nexcanvas and nextrmnl.

* The seed is stored encrypted with the secret key (``crypto``), the recovery codes as SHA-256 hashes, all in
  ``settings``: there is one account. Seed and codes are shown once, at enrolment, and never logged.
* Enrolment takes two steps. ``begin_enrolment`` draws a seed and keeps it in memory for ten minutes; confirming needs a
  code from the app (proves the app holds the seed) and the password (proves the person at the keyboard is the owner).
  Only then is the seed stored.
* A sign-in with a second factor takes two steps as well. The password step opens nothing: it parks the sign-in in
  memory under a random token and hands the browser that token in a cookie. The code step turns it into the session.
  Five wrong codes end the pending sign-in; every wrong code counts on the login brake like a wrong password, and the
  password step does not reset that count (only a passed code does), so knowing the password is no way around it.
* A code counts once. The time step of the last accepted code is stored, and a code at or before that step is refused:
  something read over a shoulder cannot be replayed within its thirty seconds.
* A sign-in through OpenID Connect asks for no code here: the provider brings its own second factor.

Written out rather than taken from a library: RFC 6238 is thirty lines, and the tests hold the RFC's own vectors.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import secrets
import struct
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import segno
from sqlalchemy.orm import Session as OrmSession

from .. import crypto
from ..db import get_setting, set_setting

ISSUER = "nexcrate"
STEP_SECONDS = 30
DIGITS = 6
#: Steps before and after the current one that are still accepted: clocks drift, people are slow.
WINDOW = 1
SEED_BYTES = 20
ENROLMENT_SECONDS = 600
PENDING_SECONDS = 300
#: Wrong codes before a pending sign-in is thrown away and the password has to be given again.
MAX_ATTEMPTS = 5
RECOVERY_CODES = 8
#: No 0, o, 1, l, i: the codes get typed from paper.
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
RECOVERY_LENGTH = 10

SECRET = "totp_secret"
RECOVERY = "totp_recovery"
LAST_STEP = "totp_last_step"


# --- Codes ---------------------------------------------------------------------------------------------------------- #


def generate_seed() -> str:
    """A fresh seed, base32 without padding, the way authenticator apps take it."""
    return base64.b32encode(secrets.token_bytes(SEED_BYTES)).decode("ascii").rstrip("=")


class SeedUnreadable(Exception):
    """The stored seed cannot be opened: the secret key is not the one that sealed it."""


def _seed_bytes(seed: str) -> bytes:
    cleaned = seed.strip().replace(" ", "").upper()
    if not cleaned:
        # An empty seed would make every code computable by anyone; it must never verify anything.
        raise SeedUnreadable()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def code_at(seed: str, moment: float, step: int = STEP_SECONDS, digits: int = DIGITS) -> str:
    """The code for a point in time, RFC 6238 with HMAC-SHA1."""
    counter = int(moment) // step
    digest = hmac.new(_seed_bytes(seed), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10**digits)).zfill(digits)


def normalize_code(code: str) -> str:
    return code.strip().replace(" ", "").replace("-", "")


def verify_code(seed: str, code: str, *, after_step: int = 0, now: float | None = None) -> int | None:
    """The time step the code belongs to, or None. Steps at or before ``after_step`` are refused (replay)."""
    typed = normalize_code(code)
    if len(typed) != DIGITS or not typed.isdigit():
        return None
    moment = time.time() if now is None else now
    current = int(moment) // STEP_SECONDS
    matched: int | None = None
    # Every candidate is compared, in constant time each, so that the timing does not tell which one matched.
    for candidate in range(current - WINDOW, current + WINDOW + 1):
        expected = code_at(seed, candidate * STEP_SECONDS)
        if hmac.compare_digest(expected, typed) and candidate > after_step:
            matched = candidate
    return matched


def provisioning_uri(seed: str, account_name: str) -> str:
    label = quote(f"{ISSUER}:{account_name}", safe=":")
    return f"otpauth://totp/{label}?secret={seed}&issuer={ISSUER}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"


def qr_svg(uri: str) -> str:
    """The QR code as SVG, dark modules on white with a quiet zone, so that a phone reads it on a dark page too.

    With the SVG namespace: the page shows it as a ``data:`` image, and a browser draws an SVG in an ``<img>`` only when
    it names its namespace."""
    out = io.BytesIO()
    code = segno.make(uri, error="m")
    code.save(out, kind="svg", scale=4, dark="#111827", light="#ffffff", border=4, xmldecl=False, svgns=True)
    return out.getvalue().decode("utf-8")


# --- Recovery codes ------------------------------------------------------------------------------------------------- #


def generate_recovery_codes(count: int = RECOVERY_CODES) -> list[str]:
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_LENGTH))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def hash_recovery(code: str) -> str:
    cleaned = code.strip().replace("-", "").replace(" ", "").lower()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def load_recovery(stored: str) -> list[str]:
    try:
        data = json.loads(stored or "[]")
    except ValueError:
        return []
    return [entry for entry in data if isinstance(entry, str)] if isinstance(data, list) else []


def use_recovery(stored: str, code: str) -> str | None:
    """The stored list without the used code, or None when the code is not in it."""
    hashes = load_recovery(stored)
    wanted = hash_recovery(code)
    remaining = [entry for entry in hashes if not hmac.compare_digest(entry, wanted)]
    if len(remaining) == len(hashes):
        return None
    return json.dumps(remaining)


# --- What is stored ------------------------------------------------------------------------------------------------- #


def enabled(db: OrmSession) -> bool:
    return bool(get_setting(db, SECRET))


def recovery_left(db: OrmSession) -> int:
    return len(load_recovery(get_setting(db, RECOVERY))) if enabled(db) else 0


def store(db: OrmSession, seed: str, codes: list[str], step: int) -> None:
    """Stage the second factor. The caller commits."""
    set_setting(db, SECRET, crypto.encrypt(seed))
    set_setting(db, RECOVERY, json.dumps([hash_recovery(code) for code in codes]))
    set_setting(db, LAST_STEP, str(step))


def store_recovery(db: OrmSession, codes: list[str]) -> None:
    set_setting(db, RECOVERY, json.dumps([hash_recovery(code) for code in codes]))


def remove(db: OrmSession) -> None:
    """Stage switching the second factor off. The caller commits."""
    for key in (SECRET, RECOVERY, LAST_STEP):
        set_setting(db, key, "")
    forget()


def open_seed(db: OrmSession) -> str:
    """The seed, or ``SeedUnreadable`` when the secret key changed: then the second factor fails closed instead of
    accepting codes derived from nothing. ``python -m app.cli reset-second-factor`` is the way out."""
    seed = crypto.decrypt(get_setting(db, SECRET))
    if not seed:
        raise SeedUnreadable()
    return seed


def last_step(db: OrmSession) -> int:
    try:
        return int(get_setting(db, LAST_STEP, "0") or 0)
    except ValueError:
        return 0


def check(db: OrmSession, code: str) -> bool:
    """A code from the app or a recovery code, staged as used when it fits. The caller commits."""
    seed = open_seed(db)
    step = verify_code(seed, code, after_step=last_step(db))
    if step is not None:
        set_setting(db, LAST_STEP, str(step))
        return True
    remaining = use_recovery(get_setting(db, RECOVERY), code)
    if remaining is not None:
        set_setting(db, RECOVERY, remaining)
        return True
    return False


# --- What waits in memory ------------------------------------------------------------------------------------------- #


@dataclass
class PendingSignIn:
    #: The counter of the login brake the password step used, so the code step brakes the same one.
    brake_key: str
    expires: float
    attempts: int = 0


@dataclass
class _Store:
    enrolment: tuple[str, float] | None = None
    pending: dict[str, PendingSignIn] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


_store = _Store()


def begin_enrolment() -> str:
    seed = generate_seed()
    with _store.lock:
        _store.enrolment = (seed, time.monotonic() + ENROLMENT_SECONDS)
    return seed


def pending_seed() -> str | None:
    with _store.lock:
        if _store.enrolment is None:
            return None
        seed, expires = _store.enrolment
        if expires <= time.monotonic():
            _store.enrolment = None
            return None
        return seed


def drop_enrolment() -> None:
    with _store.lock:
        _store.enrolment = None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_pending(brake_key: str) -> str:
    """Parks the password step and returns the token for the cookie. A second password step replaces the first."""
    token = secrets.token_urlsafe(32)
    with _store.lock:
        _store.pending.clear()
        _store.pending[_hash(token)] = PendingSignIn(brake_key=brake_key, expires=time.monotonic() + PENDING_SECONDS)
    return token


def get_pending(token: str | None) -> PendingSignIn | None:
    if not token:
        return None
    with _store.lock:
        entry = _store.pending.get(_hash(token))
        if entry is None:
            return None
        if entry.expires <= time.monotonic():
            del _store.pending[_hash(token)]
            return None
        return entry


def fail_pending(token: str) -> bool:
    """Counts a wrong code. Returns whether the pending sign-in still stands."""
    with _store.lock:
        entry = _store.pending.get(_hash(token))
        if entry is None:
            return False
        entry.attempts += 1
        if entry.attempts >= MAX_ATTEMPTS:
            del _store.pending[_hash(token)]
            return False
        return True


def finish_pending(token: str) -> PendingSignIn | None:
    """Takes the pending sign-in out of the store; the caller turns it into a session."""
    with _store.lock:
        return _store.pending.pop(_hash(token), None)


def forget() -> None:
    """Nothing waits: after switching the factor off, and in the tests."""
    with _store.lock:
        _store.enrolment = None
        _store.pending.clear()
