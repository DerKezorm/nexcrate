"""Error answers with a code.

⚠️ The backend does not translate, it names. Every error carries a ``code``; the
frontend builds the sentence from ``src/i18n/<language>/errors.json``. The English
``message`` is the fallback for clients that use the API without the interface.

Every answer has the same shape, with extra values next to ``code``::

    {"detail": {"code": "login_throttled", "message": "...", "retry_after": 4}}

``tests/test_docs.py`` checks that every code used in the backend has a text in
both errors.json files, and that every route documents the codes it raises.

``/api/v1``, the contract for other programs, says the same with another shape, flat and with the
extra values gathered under ``params`` (N5)::

    {"code": "scope_missing", "message": "...", "params": {"scope": "request"}}

The routes raise the same ``error(...)`` either way; the app's error handlers pick the shape by the path.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

#: Prefix of the description of an error response in the OpenAPI document.
ERROR_CODES_PREFIX = "Error codes: "
#: Under this path errors are flat, see the module note.
V1_PREFIX = "/api/v1"


def meldung(code: str, message: str, **values: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **values}


def is_v1_path(path: str) -> bool:
    return path == V1_PREFIX or path.startswith(V1_PREFIX + "/")


def v1_body(detail: dict[str, Any]) -> dict[str, Any]:
    """The flat shape of ``/api/v1`` from the detail every route raises."""
    params = {key: value for key, value in detail.items() if key not in ("code", "message")}
    return {"code": detail.get("code"), "message": detail.get("message"), "params": params}


def error_body(path: str, detail: dict[str, Any]) -> dict[str, Any]:
    """The body of an error answer in the shape the path speaks."""
    return v1_body(detail) if is_v1_path(path) else {"detail": detail}


def error(
    code: str,
    message: str,
    status: int = 400,
    *,
    headers: dict[str, str] | None = None,
    **values: Any,
) -> HTTPException:
    return HTTPException(status_code=status, detail=meldung(code, message, **values), headers=headers)


class ErrorDetail(BaseModel):
    """What went wrong: a code for the interface, an English fallback, and extra values."""

    model_config = ConfigDict(extra="allow")

    code: str = Field(description="Stable name of the error. The interface translates it.", examples=["login_failed"])
    message: str = Field(description="English fallback text.", examples=["Username or password is wrong."])


class ErrorResponse(BaseModel):
    """The shape of every error answer."""

    detail: ErrorDetail


class ApiError(BaseModel):
    """The shape of every error answer of ``/api/v1``."""

    code: str = Field(description="Stable name of the error. Translate by it, never by the sentence.")
    message: str = Field(description="English fallback text.")
    params: dict[str, Any] = Field(description="The values that belong to the error; empty when it has none.")


def describe_codes(codes: list[str]) -> str:
    return ERROR_CODES_PREFIX + ", ".join(f"`{code}`" for code in codes)


def error_responses(*codes: tuple[int, str]) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` for a route: status and error code pairs, grouped by status."""
    grouped: dict[int, list[str]] = {}
    for status, code in codes:
        grouped.setdefault(status, []).append(code)
    return {status: {"model": ErrorResponse, "description": describe_codes(names)} for status, names in grouped.items()}


def v1_error_responses(*codes: tuple[int, str]) -> dict[int | str, dict[str, Any]]:
    """The same for a route of ``/api/v1``, whose errors are flat."""
    grouped: dict[int, list[str]] = {}
    for status, code in codes:
        grouped.setdefault(status, []).append(code)
    return {status: {"model": ApiError, "description": describe_codes(names)} for status, names in grouped.items()}
