"""How every named view's request is read (ADR 0082): fail closed, so an
unknown view, version, field, or value is refused with its relay code,
never ignored. Shared by `world/views.py` and `world/record_views.py`.
Standard library only."""

from __future__ import annotations

import re
from typing import Any

OBJECT_ID = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
REVISION_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ViewError(ValueError):
    """A view request or subject the relay refuses, with its refusal code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _bad(message: str) -> ViewError:
    return ViewError("view_request_invalid", message)


# ---------------------------------------------------------------------------
# Requests


def _closed(value: Any, required: set[str], optional: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _bad(f"{where} must be an object")
    missing, extra = required - set(value), set(value) - required - optional
    if missing or extra:
        raise _bad(f"{where} has missing {sorted(missing)} or unknown {sorted(extra)} fields")
    return value


def _object_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not OBJECT_ID.fullmatch(value):
        raise _bad(f"{where} must be an object identifier")
    return value


def _pin(value: Any, where: str) -> dict[str, str]:
    pin = _closed(value, {"id", "revision_id"}, set(), where)
    _object_id(pin["id"], f"{where} id")
    if not isinstance(pin["revision_id"], str) or not REVISION_ID.fullmatch(pin["revision_id"]):
        raise _bad(f"{where} revision_id must be a canonical SHA-256 digest")
    return pin
