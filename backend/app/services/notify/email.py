"""E-mail: two levels, the mail server above and the addresses below it.

nexcrate has no mail settings of its own, so the server is a tile like an ntfy instance. No confirmation code, as in
Nexview: behind an address may sit nobody who could read one, and a distribution list would pass it to everybody. The
test mail still has to be accepted by the server before an address is saved.
"""

from __future__ import annotations

import html as html_escape
import re
from dataclasses import dataclass

from . import mail
from .base import ChannelError, Notice, language_of

LABEL = "E-Mail"
PARENT_FIELDS = (
    "smtp_host", "smtp_port", "smtp_security", "username", "password", "smtp_from_address", "smtp_from_name",
)  # fmt: skip
PARENT_REQUIRED = ("smtp_host", "smtp_from_address")
CHILD_FIELDS = ("address", "subject", "language")
CHILD_REQUIRED = ("address",)
FIELDS = PARENT_FIELDS + CHILD_FIELDS
SECRETS = ("password",)
REQUIRES_CODE = False
#: The one placeholder in the subject: ``{title}`` puts the message's title in.
TITLE_PLACEHOLDER = "{title}"


@dataclass(frozen=True)
class Config:
    server: mail.Server
    address: str
    subject: str
    language: str


def build(values: dict[str, str]) -> Config:
    try:
        port = int(values.get("smtp_port") or 587)
    except ValueError:
        port = 587
    security = values.get("smtp_security") or "starttls"
    return Config(
        server=mail.Server(
            host=values.get("smtp_host", "").strip(),
            port=port if 0 < port < 65536 else 587,
            security=security if security in mail.SECURITY_MODES else "starttls",
            username=values.get("username", "").strip(),
            password=values.get("password", ""),
            from_address=values.get("smtp_from_address", "").strip(),
            from_name=values.get("smtp_from_name", "").strip() or "nexcrate",
        ),
        address=values.get("address", "").strip(),
        subject=values.get("subject", "").strip(),
        language=language_of(values),
    )


async def check(config: Config) -> dict[str, str] | None:
    """Does the mail server take connection and sign-in?"""
    if not mail.valid_address(config.server.from_address):
        raise ChannelError("mail_address_invalid")
    await mail.verify(config.server)
    return None


def _subject(config: Config, title: str) -> str:
    if not config.subject:
        # The test message says "nexcrate: Code 1234" already; the prefix once is enough.
        return title if title.lower().startswith("nexcrate") else f"nexcrate: {title}"
    return config.subject.replace(TITLE_PLACEHOLDER, title)


def _html(notice: Notice) -> str:
    body = html_escape.escape(notice.body)
    body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body).replace("\n", "<br>")
    image = (
        f'<p><img src="{html_escape.escape(notice.poster_url, quote=True)}" alt="" width="154"></p>'
        if notice.poster_url
        else ""
    )
    return (
        '<!doctype html><html><body style="font-family:sans-serif">'
        f"<h2>{html_escape.escape(notice.title)}</h2>{image}<p>{body}</p></body></html>"
    )


async def send(config: Config, notice: Notice) -> None:
    if not config.address:
        raise ChannelError("notify_field_missing", field="address")
    text = f"{notice.title}\n\n{notice.body.replace('**', '')}\n"
    await mail.send(config.server, config.address, _subject(config, notice.title), text, _html(notice))
