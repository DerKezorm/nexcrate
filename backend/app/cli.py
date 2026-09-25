"""Command line tools for the operator.

    python -m app.cli reset-password

In the container::

    docker exec -it nexcrate python -m app.cli reset-password

There is one account and no second way back in, so a forgotten password needs a door that
only whoever controls the server can open. The new password is asked for twice with getpass
and never taken as an argument: an argument ends up in the shell history and in the process
list.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

logger = logging.getLogger("nexcrate.cli")

#: The user the server runs as in the container.
RUN_AS_USER = "nexcrate"

NO_ACCOUNT = "No account exists yet. Open nexcrate in the browser and create it there; nothing was changed."


def _drop_root() -> None:
    """Continue as the user nexcrate when started as root.

    ⚠️ ``docker exec`` runs as root. A database journal or log file created by root could no
    longer be written by the server, which runs as the user nexcrate, and it would stop working.
    """
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    try:
        import pwd

        entry = pwd.getpwnam(RUN_AS_USER)
    except (ImportError, KeyError):
        return
    os.setgroups([])
    os.setgid(entry.pw_gid)
    os.setuid(entry.pw_uid)


def reset_password(read_password: Callable[[str], str] | None = None, out: TextIO | None = None) -> int:
    """Set a new password and end every session. Returns the exit code.

    ``read_password`` asks for a password without echo; ``getpass.getpass`` unless given.
    """
    from sqlalchemy import delete
    from sqlalchemy.exc import OperationalError

    from .config import get_settings
    from .db import SessionLocal
    from .models import ACCOUNT_ID, Account, Session
    from .security import PASSWORD_MIN_LENGTH, hash_password

    read = read_password or getpass.getpass
    stream = out or sys.stdout

    def say(text: str) -> None:
        print(text, file=stream)

    # Never create a database here: a tool that is run by mistake must not set anything up.
    if not get_settings().database_path.is_file():
        say(NO_ACCOUNT)
        return 1

    with SessionLocal() as db:
        try:
            account = db.get(Account, ACCOUNT_ID)
        except OperationalError:
            account = None
        if account is None:
            say(NO_ACCOUNT)
            return 1
        try:
            password = read("New password: ")
            if len(password) < PASSWORD_MIN_LENGTH:
                say(f"The password needs at least {PASSWORD_MIN_LENGTH} characters. Nothing was changed.")
                return 1
            repeated = read("Repeat the new password: ")
        except (EOFError, KeyboardInterrupt):
            say("")
            say("Cancelled. Nothing was changed.")
            return 1
        if password != repeated:
            say("The two passwords do not match. Nothing was changed.")
            return 1
        account.password_hash = hash_password(password)
        result = db.execute(delete(Session))
        db.commit()

    ended = int(getattr(result, "rowcount", 0) or 0)
    logger.warning("Password reset from the command line, ended %d sessions", ended)
    say("The password has been changed and every session has been ended. Log in with the new password.")
    say("If logins were throttled, wait up to five minutes or restart nexcrate.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Command line tools for nexcrate.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "reset-password",
        help="set a new password for the account and end every session",
        description="Asks for the new password twice, without showing it, and ends every session.",
    )
    arguments = parser.parse_args(argv)

    _drop_root()
    from .services import logs

    logs.setup(console=False)
    if arguments.command == "reset-password":
        return reset_password()
    return 2


if __name__ == "__main__":
    sys.exit(main())
