"""Linking chart documents to Works and people (ADR 0116, section 4).

`candidates` reads an `archive export` and playlist files, and writes, into
a directory the operator names (never into an archive or this repository),
one line per admitted chart document: candidate Works, each with a score and
the evidence for it (normalised title equal, a composer credit that agrees,
the Work's performances and MusicBrainz correspondences), and candidate
person Entities for the composer field. A title alone is never identity (ADR
0042): the file proposes nothing, and losing it loses nothing. It also
writes `decisions.json`, a template for the operator to fill.

`link` turns the operator's filled decisions into one batch per confirmed
chart: a new `musical_work` Entity when asked for, the operator's link (a
`document_source` claim pinning the Work), per composer a `contribution` of
scope `work` by the importer under a run that used the document and the
link, and the operator's `lead_sheet` of the Work pinning the chart's
sequence, meter, form, key and credits. A link already asserted for the same
document and Work yields nothing. A machine never chooses the Work.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENRICH = Path(__file__).resolve().parents[2] / "enrich"
if str(ENRICH) not in sys.path:
    sys.path.insert(0, str(ENRICH))

import adapter  # noqa: E402  (the enrichment adapter: reading the archive, staging, identities)
from batches import Batch, admit, combined
from codec import CodecError, chart_link, decode_playlist
from importer import (AGENT, FORM, KEY, KINDS, MEDIA_TYPE, METER, RUN, RUN_NAMESPACE,
                      SEQUENCE, SOFTWARE, SOURCE)
from claims import METHOD
from symbols import GRAMMAR_VERSION

CONTRIBUTION = ("archive.claim/contribution", "1")
LEAD_SHEET = ("archive.claim/lead_sheet", "1")
PERFORMANCE_WORK = "archive.claim/performance_work"
MUSICBRAINZ = "archive.claim/work_musicbrainz_correspondence"
WORK_NAMESPACE = "archive.audio/song"
ENTITY_NAMESPACE = "archive.core/entity"
CREDIT_METHOD = ("ireal-credit", "1")
LEAD_SHEET_LABEL = "iReal"
REPOSITORY = Path(__file__).resolve().parents[2]


class LinkError(Exception):
    """A decisions file or a place the command refuses."""


# ---------------------------------------------------------------------------
# Names


def normalise(title: str) -> str:
    """A title for comparison only: accents, case, punctuation and a leading
    or trailing (iReal's `Name, The`) article dropped."""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold().replace("&", " and ")
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^a-z0-9 ,]", "", text)
    text = re.sub(r",\s*(the|a|an)\s*$", "", text).replace(",", " ")
    text = re.sub(r"^(the|a|an)\s+", "", text.strip())
    return " ".join(text.split())


def name_tokens(name: str) -> set[str]:
    """The words of a person's name of three letters or more, for comparison."""
    return {token for token in normalise(name.replace("-", " ").replace("/", " ")).split() if len(token) >= 3}


# ---------------------------------------------------------------------------
# What the export says


@dataclass
class Index:
    reader: adapter.ArchiveReader
    works: dict[str, dict[str, Any]] = field(default_factory=dict)
    people: dict[str, dict[str, Any]] = field(default_factory=dict)
    credits: dict[str, list[str]] = field(default_factory=dict)        # work id -> credited names
    performances: dict[str, int] = field(default_factory=dict)
    musicbrainz: dict[str, int] = field(default_factory=dict)
    links: dict[str, set[str]] = field(default_factory=dict)          # document id -> linked work ids

    @classmethod
    def of(cls, reader: adapter.ArchiveReader) -> "Index":
        index = cls(reader)
        for object_id, record in reader.heads.items():
            if record["shape"] == "entity":
                kind = record["payload"].get("kind")
                if kind == "musical_work":
                    index.works[object_id] = record
                elif kind == "person":
                    index.people[object_id] = record
        for record in reader.heads.values():
            if record["shape"] != "claim":
                continue
            refs = record.get("refs", [])
            works = [ref["id"] for ref in refs if ref["role"] == "work"]
            contracts = {c["id"] for c in record.get("contracts", [])}
            if CONTRIBUTION[0] in contracts and record["payload"].get("scope") == "work":
                names = [record["payload"].get(key) for key in ("credited_as", "text")]
                names += [index.people[ref["id"]]["payload"].get("label") for ref in refs
                          if ref["role"] == "contributor" and ref["id"] in index.people]
                for work in works:
                    index.credits.setdefault(work, []).extend(n for n in names if n)
            if PERFORMANCE_WORK in contracts:
                for work in works:
                    index.performances[work] = index.performances.get(work, 0) + 1
            if MUSICBRAINZ in contracts:
                for work in works:
                    index.musicbrainz[work] = index.musicbrainz.get(work, 0) + 1
            if SOURCE[0] in contracts and works:
                for ref in refs:
                    if ref["role"] == "document":
                        index.links.setdefault(ref["id"], set()).update(works)
        return index


def work_candidates(index: Index, title: str, composer: str) -> list[dict[str, Any]]:
    wanted = normalise(title)
    surnames = name_tokens(composer)
    out = []
    for work_id, work in index.works.items():
        reasons = []
        score = 0
        if wanted and normalise(work["payload"].get("label", "")) == wanted:
            reasons.append("normalised title equal")
            score += 1
        agreeing = [name for name in index.credits.get(work_id, []) if surnames & name_tokens(name)]
        if agreeing:
            reasons.append("composer credit agrees: " + "; ".join(sorted(set(agreeing))))
            score += 2
        if not reasons:
            continue
        out.append({"work": work_id, "label": work["payload"].get("label"), "score": score, "reasons": reasons,
                    "evidence": {"credits": sorted(set(index.credits.get(work_id, []))),
                                 "performances": index.performances.get(work_id, 0),
                                 "musicbrainz_correspondences": index.musicbrainz.get(work_id, 0)}})
    return sorted(out, key=lambda c: (-c["score"], c["label"] or "", c["work"]))


def person_candidates(index: Index, composer: str) -> list[dict[str, Any]]:
    surnames = name_tokens(composer)
    out = []
    for person_id, person in index.people.items():
        shared = surnames & name_tokens(person["payload"].get("label", ""))
        if shared:
            out.append({"person": person_id, "label": person["payload"].get("label"),
                        "reasons": ["shares the name " + ", ".join(sorted(shared))]})
    return sorted(out, key=lambda c: (c["label"] or "", c["person"]))


def _outside(place: Path, *roots: Path) -> None:
    place = place.resolve()
    for root in roots:
        root = root.resolve()
        if place == root or place.is_relative_to(root):
            raise LinkError(f"{place} is inside {root}; candidates are private and operational, "
                            f"written only outside archives and this repository")


def candidates(reader: adapter.ArchiveReader, playlists: list[Path], out: Path) -> dict[str, Any]:
    """Write `candidates.jsonl` and `decisions.json` into `out`. Returns counts."""
    _outside(out, REPOSITORY, reader.root)
    index = Index.of(reader)
    lines, decisions = [], []
    counts = {"charts": 0, "admitted": 0, "with_work_candidates": 0, "linked": 0}
    seen: set[str] = set()
    for path in playlists:
        try:
            playlist = decode_playlist(path.read_bytes())
        except CodecError as error:
            raise LinkError(f"{path} is not an iReal Pro link: {error}") from None
        for number, song in enumerate(playlist.songs, start=1):
            counts["charts"] += 1
            artifact = reader.artifact_with_digest(adapter.digest_of(chart_link(song)))
            if artifact is None or artifact["object_id"] in seen:
                continue
            seen.add(artifact["object_id"])
            counts["admitted"] += 1
            works = work_candidates(index, song.title, song.composer)
            linked = sorted(index.links.get(artifact["object_id"], set()))
            counts["with_work_candidates"] += bool(works)
            counts["linked"] += bool(linked)
            lines.append({"document": artifact["object_id"], "playlist": path.name, "number": number,
                          "title": song.title, "composer": song.composer, "linked": linked, "works": works,
                          "people": person_candidates(index, song.composer)})
            decisions.append({"document": artifact["object_id"], "title": song.title, "work": None,
                              "composers": [{"credited_as": song.composer, "person": None}] if song.composer else []})
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "candidates.jsonl", "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
    (out / "decisions.json").write_text(json.dumps({"charts": decisions}, ensure_ascii=False, indent=1) + "\n",
                                        encoding="utf-8")
    return counts


# ---------------------------------------------------------------------------
# Link


def operator_of(reader: adapter.ArchiveReader) -> str:
    """The operator: the person Entity that asserted itself at `archive init`."""
    found = [object_id for object_id, record in reader.heads.items()
             if record["shape"] == "entity" and record["payload"].get("kind") == "person"
             and record["agent"] == object_id]
    if len(found) != 1:
        raise LinkError(f"the archive names {len(found)} operators; pass --operator")
    return found[0]


def chart_readings(reader: adapter.ArchiveReader, document: str) -> dict[str, dict[str, str]]:
    """The importer's claims about a chart document from its latest
    `ireal-chart` run of this grammar version: role name to pin."""
    method = {"id": METHOD, "version": str(GRAMMAR_VERSION)}
    runs = {record["revision_id"]: record for record in reader.heads.values()
            if record["shape"] == "claim" and reader.cites(record, RUN) and record["agent"] == AGENT
            and record["payload"].get("method") == method}
    by_run: dict[str, dict[str, dict[str, str]]] = {}
    for record in reader.heads.values():
        if record["shape"] != "claim" or record["agent"] != AGENT:
            continue
        refs = record.get("refs", [])
        run = next((ref["revision_id"] for ref in refs if ref["role"] == "run"), None)
        if run not in runs or not any(ref["role"] == "document" and ref["id"] == document for ref in refs):
            continue
        for name, contract in (("sequence", SEQUENCE), ("form", FORM), ("meter", METER), ("key", KEY)):
            if reader.cites(record, contract):
                by_run.setdefault(run, {})[name] = {"id": record["object_id"], "revision_id": record["revision_id"]}
    complete = [run for run, found in by_run.items() if {"sequence", "form", "meter"} <= set(found)]
    if not complete:
        return {}
    latest = max(complete, key=lambda run: (runs[run]["payload"]["started_at"], run))
    return by_run[latest]


def _ref(role: str, pin: dict[str, str]) -> dict[str, str]:
    return {"role": role, **pin}


def _decisions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("charts"), list):
        raise LinkError("a decisions file is an object with a list `charts`")
    for index, chart in enumerate(value["charts"]):
        where = f"decision {index}"
        if not isinstance(chart, dict) or not isinstance(chart.get("document"), str):
            raise LinkError(f"{where} names no document")
        work = chart.get("work")
        if work is not None and not isinstance(work, str):
            raise LinkError(f"{where} work is a Work's URN, \"new\", or null")
        if work == "new" and not (isinstance(chart.get("title"), str) and chart["title"].strip()):
            raise LinkError(f"{where} asks for a new Work but gives no title to label it")
        composers = chart.get("composers", [])
        if not isinstance(composers, list) or any(
                not isinstance(c, dict) or not isinstance(c.get("credited_as"), str) or not c["credited_as"]
                or not (c.get("person") is None or isinstance(c.get("person"), str)) for c in composers):
            raise LinkError(f"{where} composers are objects with credited_as text and person URN, \"new\" or null")
    return value["charts"]


def plan_link(reader: adapter.ArchiveReader, decisions: Any, operator: str,
              now=adapter.now) -> tuple[list[Batch], list[dict[str, Any]]]:
    """One batch per confirmed chart, and a report line per decision."""
    batches, report = [], []
    for chart in _decisions(decisions):
        document_id, work_choice = chart["document"], chart.get("work")
        line: dict[str, Any] = {"document": document_id}
        report.append(line)
        if work_choice is None:
            line["status"] = "none"
            continue
        document = reader.heads.get(document_id)
        if document is None or document["shape"] != "artifact":
            raise LinkError(f"{document_id} is not an Artifact of this archive")
        if work_choice != "new":
            work = reader.heads.get(work_choice)
            if work is None or work["shape"] != "entity" or work["payload"].get("kind") != "musical_work":
                raise LinkError(f"{work_choice} is not a musical_work Entity of this archive")
        # A rerun of the same decisions writes nothing: "new" is taken once,
        # by the first link the operator asserted for this document.
        linked = _linked_by(reader, document_id, operator)
        if (work_choice == "new" and linked) or work_choice in linked:
            line.update(status="already_linked", works=sorted(linked))
            continue
        readings = chart_readings(reader, document_id)
        if not readings:
            raise LinkError(f"{document_id} has no complete ireal-chart reading to link (a failed or unread chart)")
        batch = Batch(f"link-{len(batches) + 1}", reader)
        doc_pin = {"id": document_id, "revision_id": document["revision_id"]}
        if work_choice == "new":
            work_pin = batch.add(WORK_NAMESPACE, f"song-{uuid.uuid4()}", "entity",
                                 {"kind": "musical_work", "label": chart["title"].strip()}, [],
                                 agent=operator, by=AGENT)
        else:
            work_pin = {"id": work_choice, "revision_id": reader.heads[work_choice]["revision_id"]}
        link = batch.add(SOURCE[0], f"ireal-link-{uuid.uuid4()}", "claim", {"media_type": MEDIA_TYPE, "kinds": KINDS},
                         [_ref("document", doc_pin), _ref("work", work_pin)], agent=operator, by=AGENT,
                         contract=SOURCE)
        credits = []
        composers = chart.get("composers", [])
        if composers:
            started = now()
            run_name = "run-" + str(uuid.uuid4())
            run = batch.add(RUN_NAMESPACE, run_name, "claim",
                            {"method": {"id": CREDIT_METHOD[0], "version": CREDIT_METHOD[1]},
                             "software": [{"name": n, "version": v} for n, v in SOFTWARE],
                             "started_at": adapter.utc_instant(started), "ended_at": adapter.utc_instant(now())},
                            [_ref("used", doc_pin), _ref("used", link)], agent=AGENT, by=AGENT, contract=RUN)
            for number, composer in enumerate(composers):
                refs = [_ref("work", work_pin), _ref("document", doc_pin), _ref("run", run)]
                payload: dict[str, Any] = {"scope": "work", "role": {"type": "composer"},
                                           "text": composer["credited_as"]}
                person = composer.get("person")
                if person == "new":
                    pin = batch.add(ENTITY_NAMESPACE, f"person-{uuid.uuid4()}", "entity",
                                    {"kind": "person", "label": composer["credited_as"]}, [],
                                    agent=operator, by=AGENT)
                    refs.append(_ref("contributor", pin))
                elif person is not None:
                    target = reader.heads.get(person)
                    if target is None or target["shape"] != "entity" or target["payload"].get("kind") != "person":
                        raise LinkError(f"{person} is not a person Entity of this archive")
                    refs.append(_ref("contributor", {"id": person, "revision_id": target["revision_id"]}))
                if person is not None:
                    payload["credited_as"] = composer["credited_as"]
                credits.append(batch.add(CONTRIBUTION[0], f"{run_name}-credit-{number}", "claim", payload, refs,
                                         agent=AGENT, by=AGENT, contract=CONTRIBUTION))
        refs = [_ref("work", work_pin), _ref("chords", readings["sequence"]), _ref("meter", readings["meter"]),
                _ref("form", readings["form"])]
        if "key" in readings:
            refs.append(_ref("key", readings["key"]))
        refs += [_ref("credits", pin) for pin in credits]
        batch.add(LEAD_SHEET[0], f"ireal-lead-sheet-{uuid.uuid4()}", "claim", {"label": LEAD_SHEET_LABEL}, refs,
                  agent=operator, by=AGENT, contract=LEAD_SHEET)
        batches.append(batch)
        line.update(status="linked", work=work_pin["id"], new_work=work_choice == "new", credits=len(credits))
    return batches, report


def _linked_by(reader: adapter.ArchiveReader, document: str, operator: str) -> set[str]:
    """The Works the operator's own source claims link this document to."""
    works: set[str] = set()
    for record in reader.heads.values():
        if record["shape"] == "claim" and record["agent"] == operator and reader.cites(record, SOURCE):
            refs = record.get("refs", [])
            if any(r["role"] == "document" and r["id"] == document for r in refs):
                works.update(r["id"] for r in refs if r["role"] == "work")
    return works


def run_link(decisions_path: Path, *, archive: Path, door: str = "door", archive_command: str = "archive",
             dry_run: bool = False, operator: str | None = None, proposals: Path | None = None,
             env: dict[str, str] | None = None) -> dict[str, Any]:
    reader = adapter.ArchiveReader(archive, archive_command, env)
    for contract in (SOURCE, RUN, CONTRIBUTION, LEAD_SHEET):
        reader.citation(contract)
    try:
        decisions = json.loads(Path(decisions_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LinkError(f"{decisions_path} could not be read as JSON: {error}") from None
    batches, report = plan_link(reader, decisions, operator or operator_of(reader))
    if batches:
        if dry_run:
            admit(combined("link", batches), door=door, dry_run=True, staging=None, env=env,
                  keep=proposals / "0000-link" if proposals else None)
        else:
            for number, batch in enumerate(batches):
                admit(batch, door=door, dry_run=False, staging=None, env=env,
                      keep=proposals / f"{number:04d}-{batch.label}" if proposals else None)
    return {"status": "unchanged" if not batches else ("predicted" if dry_run else "committed"),
            "batches": len(batches), "charts": report}
