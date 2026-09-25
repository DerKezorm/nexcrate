"""Sending mail through the owner's SMTP server, with the standard library (taken over from Nexview).

``smtplib`` blocks, so it runs in a thread. The certificate is always checked: a self-signed one shows as an error
instead of silently pretending an encrypted connection.
"""

from __future__ import annotations

import asyncio
import re
import smtplib
import socket
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .base import ChannelError

TIMEOUT_SECONDS = 15
#: Lenient on purpose: the mail server has the last word, this only catches obvious typos.
ADDRESS_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SECURITY_MODES = ("none", "starttls", "ssl")


@dataclass(frozen=True)
class Server:
    host: str
    port: int
    security: str
    username: str
    password: str
    from_address: str
    from_name: str


def valid_address(address: str) -> bool:
    return bool(ADDRESS_PATTERN.match(address.strip()))


def _failure(error: Exception, server: Server) -> ChannelError:
    # A server that offers no STARTTLS on this port says so with SMTPNotSupportedError: the encryption does not fit.
    if isinstance(error, ssl.SSLError | smtplib.SMTPNotSupportedError):
        return ChannelError("mail_tls")
    if isinstance(error, TimeoutError | socket.timeout):
        return ChannelError("mail_timeout", host=server.host, port=server.port)
    if isinstance(error, ConnectionRefusedError):
        return ChannelError("mail_refused", host=server.host, port=server.port)
    if isinstance(error, socket.gaierror):
        return ChannelError("mail_dns", host=server.host)
    return ChannelError("mail_unreachable", host=server.host, port=server.port)


def _connect(server: Server) -> smtplib.SMTP:
    context = ssl.create_default_context()
    try:
        if server.security == "ssl":
            connection: smtplib.SMTP = smtplib.SMTP_SSL(
                server.host, server.port, timeout=TIMEOUT_SECONDS, context=context
            )
        else:
            connection = smtplib.SMTP(server.host, server.port, timeout=TIMEOUT_SECONDS)
            if server.security == "starttls":
                connection.starttls(context=context)
                connection.ehlo()
    except (OSError, smtplib.SMTPException) as exc:
        raise _failure(exc, server) from exc
    if server.username:
        try:
            connection.login(server.username, server.password)
        except smtplib.SMTPException as exc:
            connection.close()
            raise ChannelError("mail_auth") from exc
    return connection


def _verify(server: Server) -> None:
    connection = _connect(server)
    try:
        connection.noop()
        connection.quit()
    except smtplib.SMTPException as exc:
        raise ChannelError("mail_unreachable", host=server.host, port=server.port) from exc


def _send(server: Server, message: EmailMessage) -> None:
    connection = _connect(server)
    try:
        connection.send_message(message)
    except smtplib.SMTPRecipientsRefused as exc:
        raise ChannelError("mail_recipient_refused") from exc
    except smtplib.SMTPSenderRefused as exc:
        raise ChannelError("mail_sender_refused") from exc
    except smtplib.SMTPException as exc:
        raise ChannelError("mail_rejected") from exc
    finally:
        try:
            connection.quit()
        except smtplib.SMTPException:
            pass


async def verify(server: Server) -> None:
    """Connection and sign-in, without sending a mail."""
    if not server.host:
        raise ChannelError("notify_field_missing", field="smtp_host")
    await asyncio.to_thread(_verify, server)


async def send(server: Server, to: str, subject: str, text: str, html: str) -> None:
    """One message, always with a text and an HTML part: HTML alone lands in spam more often."""
    if not server.host or not server.from_address:
        raise ChannelError("notify_field_missing", field="smtp_host" if not server.host else "smtp_from_address")
    if not valid_address(to):
        raise ChannelError("mail_address_invalid")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((server.from_name or "nexcrate", server.from_address))
    message["To"] = to.strip()
    message["Message-ID"] = make_msgid(domain=server.from_address.split("@")[-1])
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    await asyncio.to_thread(_send, server, message)
