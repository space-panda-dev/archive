"""World's records of one chart as the importer reads it (ADR 0116, sections
1, 2 and 4), for the exporter's round trip: `read` is the importer's own
reading (`claims.chart_claims`), so the round trip tests the importer and
the exporter together and never a second reader that could agree with the
exporter while the importer does not.

`read(markup, key, style, tempo)` gives the payloads; `records(...)` wraps
them, with a Work, a composer credit and a lead sheet, as World's records
an exporter is given. Standard library, World and the importer's reader
only.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from world.contributions import check_contribution  # noqa: E402
from world.form import check_form, check_meter_layers_v3  # noqa: E402
from world.harmony import check_chord_sequence_v2  # noqa: E402
from world.lead_sheets import check_lead_sheet  # noqa: E402
from world.tonality import check_key_sequence  # noqa: E402

from claims import chart_claims  # noqa: E402


def read(markup: str, key_field: str, style: str = "", tempo: str = "") -> dict[str, Any]:
    """The payloads the importer reads a chart's markup, key field, style and
    tempo field into: `sequence` (chord_sequence v2), `form` (form v1),
    `meter` (untimed meter_layers v3, one written layer, with its directions)
    and `key` (key_sequence v1, or None for an empty key field). Raises
    UnsupportedChart."""
    read = chart_claims(markup, key_field, style, tempo)
    return {"sequence": read.sequence, "form": read.form, "meter": read.meter, "key": read.key}


# ---------------------------------------------------------------------------
# Records, as World gives them to an exporter


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _urn(seed: str) -> str:
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"urn:uuid:{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _record(seed: str, shape: str, payload: dict[str, Any], refs: list[dict[str, Any]],
            contract: tuple[str, str] | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {"id": _urn(seed), "shape": shape, "payload": payload, "refs": refs,
                              "agent": _urn("agent"), "contract": list(contract) if contract else None}
    if shape == "entity":
        record["kind"] = payload["kind"]
    record["revision_id"] = "sha256:" + hashlib.sha256(_canonical(record)).hexdigest()
    record["object_id"] = record["id"]
    return record


def _ref(role: str, record: dict[str, Any]) -> dict[str, str]:
    return {"role": role, "id": record["id"], "revision_id": record["revision_id"]}


def _claim(record: dict[str, Any]) -> dict[str, Any]:
    return {"payload": record["payload"], "refs": record["refs"], "revision_id": record["revision_id"]}


def records(title: str, composer: str, payloads: dict[str, Any], seed: str = "chart") \
        -> tuple[list[dict[str, Any]], dict[str, str]]:
    """World's records of one read chart linked to a Work titled `title`, with
    a composer credit (ADR 0116, section 4: role composer, the composer field
    verbatim as `text` and `credited_as`, a confirmed person as contributor)
    and a person's lead sheet pinning them, each checked with World's checks;
    and the lead sheet's pin, which an export renders."""
    document = _record(seed + "/document", "artifact", {"content": {"digest": "sha256:" + "0" * 64}}, [])
    doc = _ref("document", document)
    sequence = _record(seed + "/sequence", "claim", payloads["sequence"], [doc],
                       ("archive.claim/chord_sequence", "2"))
    form = _record(seed + "/form", "claim", payloads["form"], [doc], ("archive.claim/form", "1"))
    meter = _record(seed + "/meter", "claim", payloads["meter"],
                    [doc, _ref("form", form), _ref("sequence", sequence)], ("archive.claim/meter_layers", "3"))
    check_chord_sequence_v2(_claim(sequence))
    check_form(_claim(form))
    check_meter_layers_v3(_claim(meter), form_claim=_claim(form), sequence_claim=_claim(sequence))
    work = _record(seed + "/work", "entity", {"kind": "musical_work", "label": title}, [])
    made = [document, sequence, form, meter, work]
    refs = [_ref("work", work), _ref("chords", sequence), _ref("meter", meter), _ref("form", form)]
    if payloads["key"] is not None:
        key = _record(seed + "/key", "claim", payloads["key"], [doc], ("archive.claim/key_sequence", "1"))
        check_key_sequence(_claim(key))
        made.append(key)
        refs.append(_ref("key", key))
    if composer:
        person = _record(seed + "/composer", "entity", {"kind": "person", "label": composer}, [])
        credit = _record(seed + "/credit", "claim",
                         {"scope": "work", "role": {"type": "composer"}, "text": composer, "credited_as": composer},
                         [_ref("work", work), doc, _ref("contributor", person)], ("archive.claim/contribution", "1"))
        made += [person, credit]
        check_contribution(_claim(credit), _targets(made))
        refs.append(_ref("credits", credit))
    lead = _record(seed + "/lead", "claim", {"label": "iReal"}, refs, ("archive.claim/lead_sheet", "2"))
    check_lead_sheet({**_claim(lead), "agent": lead["agent"]}, _targets(made), {lead["agent"]: "person"},
                     version="2")
    made.append(lead)
    return made, {"id": lead["id"], "revision_id": lead["revision_id"]}


def _targets(made: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """What each record is, as World's checks read their targets."""
    return {record["revision_id"]: record for record in made}
