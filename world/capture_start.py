"""ADR 0107 capture starts: a device clock statement, never admission time."""
from __future__ import annotations
from datetime import datetime
import re
from typing import Any

CONTRACT = ("archive.claim/capture_start", "1")
START = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:([0-5][0-9]|60)(\.[0-9]+)?(Z|[+-]([01][0-9]|2[0-3]):[0-5][0-9])?")


def check(payload: Any) -> dict:
    """Check the calendar while preserving a local clock's lack of offset."""
    if not isinstance(payload, dict) or not {"basis"} <= set(payload) <= {"basis", "started", "clock"}:
        raise ValueError("capture start has an invalid closed payload")
    if payload["basis"] not in ("grammar", "header", "client", "person"):
        raise ValueError("capture start has an unknown basis")
    if ("started" in payload) != ("clock" in payload):
        raise ValueError("capture start and clock appear together")
    if "started" not in payload:
        return payload
    started, clock = payload["started"], payload["clock"]
    if not isinstance(started, str) or not 19 <= len(started) <= 100 or not START.fullmatch(started):
        raise ValueError("capture start must use RFC 3339 date and clock syntax")
    # datetime has no leap second: validate its calendar with 59 and retain 60.
    calendar = started[:17] + ("59" if started[17:19] == "60" else started[17:19]) + started[19:]
    try:
        value = datetime.fromisoformat(calendar)
    except ValueError as exc:
        raise ValueError("capture start must be a valid calendar time") from exc
    if clock not in ("local", "zoned") or (value.tzinfo is not None) != (clock == "zoned"):
        raise ValueError("capture start must match its stated clock")
    return payload
