"""Fingerprinting as the last fallback of the file mapping (M4.9, decisions 38 to 41).

* **Only** for files that tags, position and name leave open, at most ``MAX_FILES`` per download.
* ``fpcalc`` (Chromaprint, a package of the image) runs in a child process: an argument list, no shell, stdin closed, a
  small environment, a timeout. Without it: ``fpcalc_missing``, said on the settings page, never guessed.
* **AcoustID** with the installation's own application key (the owner's answer of 18.09.2026, decision 41): stored
  encrypted, never in an answer or a log line. Without one, nexcrate's shipped key (``acoustid_app``, the owner's
  answer of 19.09.2026). A key is checked when it is stored (``check_key``). Without any key, with the switch off, or
  AcoustID not reachable, nothing is asked and the owner assigns by hand.
* At most three requests a second, as AcoustID asks. A recording id of the answer counts like one from the tags when it
  hits exactly one track and the length fits (``file_matching``).

Log lines carry counts and codes, never names, paths or the key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...db import get_setting, set_setting
from .. import http_log, media
from . import acoustid_app

logger = logging.getLogger("nexcrate.music")

SETTING_ENABLED = "acoustid_enabled"
SETTING_KEY = "acoustid_key"
SETTING_LAST_ERROR = "acoustid_last_error_code"
SETTING_LAST_OK = "acoustid_last_ok_at"
LOOKUP_URL = "https://api.acoustid.org/v2/lookup"
#: A made-up track id: AcoustID answers ``ok`` for a known key and code 4 for an unknown one, and no fingerprint goes
#: out (measured 19.09.2026).
CHECK_TRACK_ID = "00000000-0000-0000-0000-000000000000"
#: The first two minutes are enough for a fingerprint; AcoustID compares that much.
LENGTH_SECONDS = 120
TIMEOUT_SECONDS = 60
MAX_FILES = 60
#: AcoustID allows three requests a second.
PACE_SECONDS = 0.34
#: Below this score an answer is not taken.
MIN_SCORE = 0.8
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


# --- Settings ----------------------------------------------------------------------------------------------------- #


def enabled(db: OrmSession) -> bool:
    """The operator's switch (decision 40); on from the start, but nothing is asked without a key."""
    return get_setting(db, SETTING_ENABLED, "1") != "0"


def own_key(db: OrmSession) -> str | None:
    """⚠️ The installation's own decrypted application key."""
    stored = get_setting(db, SETTING_KEY, "")
    if not stored:
        return None
    try:
        return crypto.decrypt(stored) or None
    except Exception:  # noqa: BLE001 - a key nexcrate cannot read is no key
        logger.warning("The stored AcoustID key could not be read")
        return None


def shipped_key() -> str | None:
    return acoustid_app.APPLICATION_KEY or None


def key(db: OrmSession) -> str | None:
    """⚠️ The key a lookup uses, for the lookup only: the installation's own, else the shipped one."""
    return own_key(db) or shipped_key()


def key_source(db: OrmSession) -> str | None:
    if get_setting(db, SETTING_KEY, ""):
        return "own"
    return "shipped" if shipped_key() else None


def set_key(db: OrmSession, value: str | None) -> None:
    set_setting(db, SETTING_KEY, crypto.encrypt(value) if value else "")
    set_setting(db, SETTING_LAST_ERROR, "")


def set_enabled(db: OrmSession, value: bool) -> None:
    set_setting(db, SETTING_ENABLED, "1" if value else "0")


def fpcalc() -> str | None:
    return shutil.which("fpcalc")


def state(db: OrmSession) -> dict[str, Any]:
    """For the settings page: switch, key present, tool present, the last answer (decision 40)."""
    return {
        "enabled": enabled(db),
        "key_set": bool(get_setting(db, SETTING_KEY, "")),
        "key_source": key_source(db),
        "fpcalc_available": fpcalc() is not None,
        "last_error_code": get_setting(db, SETTING_LAST_ERROR, "") or None,
        "last_ok_at": get_setting(db, SETTING_LAST_OK, "") or None,
    }


def usable(db: OrmSession) -> bool:
    return enabled(db) and key(db) is not None and fpcalc() is not None


# --- The fingerprint ------------------------------------------------------------------------------------------------ #


@dataclass(frozen=True)
class Print:
    duration: int
    fingerprint: str


def compute(path: Path) -> Print | None:
    """The fingerprint of one absolute local path. Never raises."""
    tool = fpcalc()
    if tool is None or not path.is_absolute():
        return None
    try:
        # An argument list, no shell, only a local path.
        done = subprocess.run(
            [tool, "-json", "-length", str(LENGTH_SECONDS), str(path)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=media._environment(),
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except OSError, ValueError, subprocess.TimeoutExpired:
        logger.info("fpcalc did not answer for a file")
        return None
    if done.returncode != 0:
        return None
    try:
        data = json.loads(done.stdout.decode("utf-8"))
    except UnicodeDecodeError, ValueError:
        return None
    duration, fingerprint = data.get("duration"), data.get("fingerprint")
    if not isinstance(duration, int | float) or not isinstance(fingerprint, str) or not fingerprint:
        return None
    return Print(duration=int(duration), fingerprint=fingerprint)


# --- AcoustID ------------------------------------------------------------------------------------------------------- #


class LookupFailed(Exception):
    """``acoustid_key_invalid``, ``acoustid_unreachable``, ``acoustid_error``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def recordings_of(answer: dict[str, Any]) -> list[str]:
    """The recording ids of an AcoustID answer with a score of at least ``MIN_SCORE``, best first."""
    found: list[tuple[float, str]] = []
    for result in answer.get("results") or []:
        score = result.get("score")
        if not isinstance(score, int | float) or score < MIN_SCORE:
            continue
        for recording in result.get("recordings") or []:
            mbid = recording.get("id") if isinstance(recording, dict) else None
            if isinstance(mbid, str) and mbid:
                found.append((float(score), mbid))
    ordered: list[str] = []
    for _score, mbid in sorted(found, key=lambda item: -item[0]):
        if mbid not in ordered:
            ordered.append(mbid)
    return ordered


async def lookup(client: httpx.AsyncClient, application_key: str, found: Print) -> list[str]:
    data = {
        "client": application_key,
        "meta": "recordings",
        "duration": str(found.duration),
        "fingerprint": found.fingerprint,
        "format": "json",
    }
    try:
        response = await http_log.send(client, "POST", LOOKUP_URL, data=data)
    except httpx.HTTPError as exc:
        raise LookupFailed("acoustid_unreachable") from exc
    try:
        answer = response.json()
    except ValueError as exc:
        raise LookupFailed("acoustid_error") from exc
    if not isinstance(answer, dict) or answer.get("status") != "ok":
        error = answer.get("error") if isinstance(answer, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        # AcoustID's code 4 is an invalid application key.
        raise LookupFailed("acoustid_key_invalid" if code == 4 else "acoustid_error")
    return recordings_of(answer)


async def check_key(application_key: str) -> None:
    """Asks AcoustID whether it knows the key, with a made-up track id and no fingerprint. Raises ``LookupFailed``."""
    client = http_log.client("acoustid", timeout=TIMEOUT, log_bodies=False, origin_only=True)
    data = {"client": application_key, "trackid": CHECK_TRACK_ID, "format": "json"}
    try:
        try:
            response = await http_log.send(client, "POST", LOOKUP_URL, data=data)
        except httpx.HTTPError as exc:
            raise LookupFailed("acoustid_unreachable") from exc
    finally:
        await client.aclose()
    try:
        answer = response.json()
    except ValueError as exc:
        raise LookupFailed("acoustid_error") from exc
    if isinstance(answer, dict) and answer.get("status") == "ok":
        return
    error = answer.get("error") if isinstance(answer, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    raise LookupFailed("acoustid_key_invalid" if code == 4 else "acoustid_error")


async def _lookup_all(application_key: str, prints: dict[int, Print]) -> dict[int, list[str]]:
    client = http_log.client("acoustid", timeout=TIMEOUT, log_bodies=False, origin_only=True)
    found: dict[int, list[str]] = {}
    try:
        for index, (file_key, printed) in enumerate(prints.items()):
            if index:
                await asyncio.sleep(PACE_SECONDS)
            found[file_key] = await lookup(client, application_key, printed)
    finally:
        await client.aclose()
    return found


@dataclass(frozen=True)
class Outcome:
    #: File key to the recording ids AcoustID named.
    recordings: dict[int, list[str]]
    #: File key to ``matched``, ``none`` or ``no_print``.
    states: dict[int, str]
    error_code: str | None = None


def identify(paths: dict[int, Path], application_key: str) -> Outcome:
    """Fingerprint and look up at most ``MAX_FILES`` files. Never raises."""
    started = time.perf_counter()
    prints: dict[int, Print] = {}
    states: dict[int, str] = {}
    for file_key, path in list(paths.items())[:MAX_FILES]:
        printed = compute(path)
        if printed is None:
            states[file_key] = "no_print"
        else:
            prints[file_key] = printed
    if not prints:
        return Outcome({}, states)
    try:
        recordings = asyncio.run(_lookup_all(application_key, prints))
    except LookupFailed as exc:
        logger.info("AcoustID answered with %s; the files stay for the owner", exc.code)
        return Outcome({}, states, exc.code)
    for file_key in prints:
        states[file_key] = "matched" if recordings.get(file_key) else "none"
    logger.info(
        "Fingerprints: %d files looked up in %dms, %d with a recording",
        len(prints),
        (time.perf_counter() - started) * 1000,
        sum(1 for value in recordings.values() if value),
    )
    return Outcome(recordings, states)
