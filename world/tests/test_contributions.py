"""Contributions and band membership (ADR 0078): the payload and reference
rules, the inherited MusicBrainz roles, the resolved view, and the link to a
chord stack's part.

Run from the repository root: python3 -m unittest world.tests.test_contributions.
The module needs only the standard library. Every example is synthetic
(INV-23): the people, band, release, and studio are invented, and the stream
claims are the fixture archive's own stream record from fixture/plan.json,
lengthened so that spans fit. The instrument MBIDs are MusicBrainz's own (CC0).
"""

import copy
import json
import re
import unittest
from pathlib import Path

from world.contributions import (
    ARTIST_RECORDING,
    BAND_MEMBERSHIP,
    CONTRIBUTION,
    ROLE_TYPES,
    ContributionError,
    check_band_membership,
    check_contribution,
    part_contributions,
    resolve,
)

ROOT = Path(__file__).resolve().parents[2]
RATE = 8000
SECOND = RATE
TROMBONE = "https://musicbrainz.org/instrument/f6100277-c7b8-4c8d-aa26-d8cd014b6761"
BASS_TROMBONE = "https://musicbrainz.org/instrument/e659d40f-50b4-4726-b8d5-a3e254e8008b"
ELECTRIC_PIANO = "https://musicbrainz.org/instrument/75b8297f-d9fe-455d-85cf-d62843f14da5"


def pin(n: int) -> dict:
    return {"id": f"urn:uuid:00000000-0000-4000-8000-{n:012x}", "revision_id": "sha256:" + f"{n:02x}" * 32}


# Subjects and contributors, each with what Door would find at its revision.
ANN = pin(0xA1)            # a person: plays trombone
BO = pin(0xA2)             # a person: writes, plays keys
CY = pin(0xA3)             # a person: co-writer
BAND = pin(0xA4)           # an organisation: the band
MASTERING_HOUSE = pin(0xA5)
WORK = pin(0xB1)           # a musical_work
RELEASE = pin(0xB2)        # a release (edition)
RECORDING = pin(0xB3)      # a recording Entity
TAKE = pin(0xB4)           # an audio_selection version 2: a take in the archive
OLD_SELECTION = pin(0xB5)  # an audio_selection version 1
MIX = pin(0xC1)            # the mix's Artifact
MIX_STREAM = pin(0xC2)
MULTITRACK = pin(0xC3)     # an eight-channel capture Artifact
MULTITRACK_STREAM = pin(0xC4)
STEM = pin(0xC5)           # a one-channel Artifact: a stem or a channel's own file
BOOKLET = pin(0xD1)        # a scan of the booklet
DISCOGS = pin(0xD2)        # a saved Discogs API response
RUN = pin(0xE1)            # a derivation run


def fixture_stream(artifact: dict, seconds: int, channels: int) -> dict:
    """The fixture archive's audio stream claim, about `artifact`."""
    plan = json.loads((ROOT / "fixture/plan.json").read_text())
    records = [r for step in plan["steps"] for r in step.get("records", [])]
    record = next(r for r in records if r.get("namespace") == "archive.claim/audio_stream")
    payload = dict(record["payload"])
    assert payload["sample_rate"] == RATE, "the fixture stream's rate changed; update RATE"
    payload["sample_count"] = seconds * SECOND
    payload["channel_count"] = channels
    return {"shape": "claim", "contract": ("archive.claim/audio_stream", "1"), "payload": payload,
            "refs": [{"role": "artifact", **artifact}, {"role": "run", **RUN}]}


def entity(kind: str) -> dict:
    return {"shape": "entity", "kind": kind}


TARGETS = {
    ANN["revision_id"]: entity("person"),
    BO["revision_id"]: entity("person"),
    CY["revision_id"]: entity("person"),
    BAND["revision_id"]: entity("organisation"),
    MASTERING_HOUSE["revision_id"]: entity("organisation"),
    WORK["revision_id"]: entity("musical_work"),
    RELEASE["revision_id"]: entity("release"),
    RECORDING["revision_id"]: entity("recording"),
    TAKE["revision_id"]: {"shape": "claim", "contract": ("archive.claim/audio_selection", "2")},
    OLD_SELECTION["revision_id"]: {"shape": "claim", "contract": ("archive.claim/audio_selection", "1")},
    MIX["revision_id"]: {"shape": "artifact"},
    MIX_STREAM["revision_id"]: fixture_stream(MIX, 240, 2),
    MULTITRACK["revision_id"]: {"shape": "artifact"},
    MULTITRACK_STREAM["revision_id"]: fixture_stream(MULTITRACK, 240, 8),
    STEM["revision_id"]: {"shape": "artifact"},
    BOOKLET["revision_id"]: {"shape": "artifact"},
    DISCOGS["revision_id"]: {"shape": "artifact"},
    RUN["revision_id"]: {"shape": "claim", "contract": ("archive.claim/derivation_run", "1")},
}


def refs(**roles: dict) -> list[dict]:
    return [{"role": role, **value} for role, value in roles.items()]


def claim(n: int, payload: dict, agent: str = "operator", **roles: dict) -> dict:
    return {"revision_id": "sha256:" + f"{n:02x}" * 32, "agent": agent, "payload": payload, "refs": refs(**roles)}


def at(seconds: float) -> int:
    return int(seconds * SECOND)


def booklet_trombone() -> dict:
    """A performer on an instrument for a whole recording, from a booklet."""
    return claim(0x10, {
        "scope": "recording",
        "role": {"type": "instrument", "instruments": [TROMBONE]},
        "text": "A. Example – trombone",
        "credited_as": "A. Example",
        "locator": "page 3, track 2 credits",
    }, agent="importer", contributor=ANN, recording=RECORDING, document=BOOKLET)


def operator_trombone_span() -> dict:
    """The same performer refined to a span: "trombone from 2:31 to 3:05"."""
    return claim(0x11, {
        "scope": "span",
        "role": {"type": "instrument", "instruments": [TROMBONE]},
        "sample_rate": RATE,
        "spans": [{"start_sample": at(151), "end_sample": at(185)}],
    }, contributor=ANN, source=MIX, stream=MIX_STREAM, recording=RECORDING)


class Contributions(unittest.TestCase):
    def check(self, c: dict) -> dict:
        return check_contribution(c, TARGETS)

    def test_contracts_are_named(self):
        self.assertEqual(CONTRIBUTION, ("archive.claim/contribution", "1"))
        self.assertEqual(BAND_MEMBERSHIP, ("archive.claim/band_membership", "1"))

    def test_performer_on_instrument_for_a_recording_from_a_booklet(self):
        c = booklet_trombone()
        payload = self.check(c)
        self.assertEqual(payload["role"]["instruments"], [TROMBONE])
        # The booklet's own wording is kept verbatim beside the normalised role.
        self.assertEqual(payload["text"], "A. Example – trombone")

    def test_same_performer_refined_to_a_span(self):
        coarse, fine = booklet_trombone(), operator_trombone_span()
        self.check(fine)
        # Anchor: the two claims differ in scope and asserter.
        self.assertNotEqual(coarse["payload"]["scope"], fine["payload"]["scope"])
        self.assertNotEqual(coarse["agent"], fine["agent"])
        view = {e.revision_id: e for e in resolve([coarse, fine])}
        self.assertEqual(view[fine["revision_id"]].refines, (coarse["revision_id"],))
        self.assertEqual(view[coarse["revision_id"]].refined_by, (fine["revision_id"],))
        self.assertTrue(view[fine["revision_id"]].finest)
        # The coarser claim is kept, refined and never overruled, and says why.
        self.assertFalse(view[coarse["revision_id"]].finest)
        self.assertIn("by its own recording reference", view[coarse["revision_id"]].why[0])
        self.assertIn("same contributor", view[fine["revision_id"]].why[0])

    def test_span_of_a_take_refines_through_the_artifact_it_counts(self):
        whole = claim(0x12, {"scope": "recording", "role": {"type": "instrument", "instruments": [TROMBONE]}},
                      contributor=ANN, recording=MIX)
        fine = operator_trombone_span()
        fine["refs"] = [r for r in fine["refs"] if r["role"] != "recording"]
        self.check(whole)
        self.check(fine)
        entry = next(e for e in resolve([whole, fine]) if e.revision_id == fine["revision_id"])
        self.assertEqual(entry.refines, (whole["revision_id"],))
        self.assertIn("which is the Artifact it counts", entry.why[0])

    def test_a_narrower_span_refines_a_wider_one(self):
        wide = operator_trombone_span()
        narrow = copy.deepcopy(wide)
        narrow["revision_id"] = "sha256:" + "13" * 32
        narrow["payload"]["spans"] = [{"start_sample": at(160), "end_sample": at(170)}]
        view = {e.revision_id: e for e in resolve([wide, narrow])}
        self.assertEqual(view[narrow["revision_id"]].refines, (wide["revision_id"],))

    def test_refinement_needs_the_same_contributor_and_role(self):
        coarse, fine = booklet_trombone(), operator_trombone_span()
        other_person = copy.deepcopy(fine)
        other_person["refs"][0] = {"role": "contributor", **BO}
        other_instrument = copy.deepcopy(fine)
        other_instrument["payload"]["role"]["instruments"] = [BASS_TROMBONE]
        for c in (other_person, other_instrument):
            self.check(c)
            view = {e.revision_id: e for e in resolve([coarse, c])}
            self.assertEqual(view[coarse["revision_id"]].refined_by, ())
            self.assertEqual(view[c["revision_id"]].refines, ())

    def test_sources_at_one_scope_coexist(self):
        # The booklet and Discogs both credit the trombone: two claims, never
        # merged, neither refining the other.
        booklet = booklet_trombone()
        discogs = claim(0x14, {
            "scope": "recording",
            "role": {"type": "instrument", "instruments": [TROMBONE]},
            "text": "Trombone – Ann Example",
            "document_url": "https://api.discogs.com/releases/1",
            "retrieved_at": "2026-10-18T09:00:00Z",
            "locator": "tracklist[1].extraartists[0]",
        }, agent="importer", contributor=ANN, recording=RECORDING, document=DISCOGS)
        self.check(discogs)
        view = resolve([booklet, discogs])
        self.assertTrue(all(e.finest and not e.refines for e in view))

    def test_a_detector_locates_but_names_no_one(self):
        detected = claim(0x15, {
            "scope": "span",
            "role": {"type": "instrument", "instruments": [TROMBONE]},
            "sample_rate": RATE,
            "spans": [{"start_sample": at(150), "end_sample": at(186)}],
            "confidence": {"scale": "probability", "value": "0.81"},
        }, agent="pkg:generic/instrument-detector@1", source=MIX, stream=MIX_STREAM, run=RUN)
        self.check(detected)
        # Containment comes only from what a claim pins: the mix is not known
        # to be the booklet's recording, so nothing is located yet.
        self.assertEqual(resolve([booklet_trombone(), detected])[0].located_by, ())
        detected["refs"].append({"role": "recording", **RECORDING})
        self.check(detected)
        view = {e.revision_id: e for e in resolve([booklet_trombone(), detected])}
        coarse = view[booklet_trombone()["revision_id"]]
        self.assertEqual(coarse.located_by, (detected["revision_id"],))
        self.assertEqual(coarse.refined_by, ())
        self.assertIsNone(view[detected["revision_id"]].contributor)

    def test_writer_credit_on_a_work_by_a_band(self):
        self.check(claim(0x20, {"scope": "work", "role": {"type": "writer"}, "text": "Written by The Examples"},
                         contributor=BAND, work=WORK, document=BOOKLET))

    def test_writer_credit_on_a_work_by_two_people(self):
        music = claim(0x21, {"scope": "work", "role": {"type": "composer"}}, contributor=BO, work=WORK)
        words = claim(0x22, {"scope": "work", "role": {"type": "lyricist"}}, contributor=CY, work=WORK)
        for c in (music, words):
            self.check(c)
        # One claim per contributor: the two writers are two claims, and
        # neither refines the other.
        self.assertTrue(all(e.finest for e in resolve([music, words])))

    def test_mastering_credit_on_a_release(self):
        self.check(claim(0x23, {"scope": "release", "role": {"type": "mastering"}, "text": "Mastered by Example Mastering"},
                         contributor=MASTERING_HOUSE, release=RELEASE, document=BOOKLET))
        self.check(claim(0x24, {"scope": "release", "role": {"type": "producer", "attributes": ["executive"]}},
                         contributor=BO, release=RELEASE))
        self.check(claim(0x25, {"scope": "release", "role": {"type": "label"}}, contributor=BAND, release=RELEASE))

    def test_a_studio_is_kept_as_written(self):
        # A place has no Entity kind yet (ADR 0043), so a studio credit keeps
        # the typed role and the studio's name as text, and names no Entity.
        self.check(claim(0x26, {"scope": "release", "role": {"type": "mastered at"},
                                "text": "Mastered at Example Sound Rooms"}, release=RELEASE, document=BOOKLET))
        self.check(claim(0x27, {"scope": "recording", "role": {"type": "recorded at"},
                                "text": "Recorded at the rehearsal room"}, recording=TAKE))

    def test_channel_credit_links_an_input_to_a_person_and_instrument(self):
        # "Channel 3 is this player's electric piano."
        c = claim(0x30, {"scope": "channel", "channel": 3,
                         "role": {"type": "instrument", "instruments": [ELECTRIC_PIANO]}},
                  contributor=BO, source=MULTITRACK, stream=MULTITRACK_STREAM, recording=TAKE)
        self.assertEqual(self.check(c)["channel"], 3)
        # A channel's own file needs no index or stream.
        self.check(claim(0x31, {"scope": "channel", "role": {"type": "instrument", "instruments": [ELECTRIC_PIANO]}},
                         contributor=BO, source=STEM))

    def test_verbatim_credit_with_an_unmapped_role(self):
        payload = self.check(claim(0x40, {"scope": "recording", "text": "Sonic weather – Ann Example",
                                          "credited_as": "Ann Example"},
                                   contributor=ANN, recording=RECORDING, document=BOOKLET))
        self.assertNotIn("role", payload)

    def test_vocals_and_attributes(self):
        self.check(claim(0x41, {"scope": "recording", "role": {
            "type": "vocal", "attributes": ["guest"], "vocals": ["background vocals"]}}, contributor=CY, recording=TAKE))
        self.check(claim(0x42, {"scope": "recording", "role": {
            "type": "instrument arranger", "instruments": [TROMBONE], "attributes": ["co"]}}, contributor=BO,
            recording=RECORDING))

    def test_every_inherited_role_type_is_accepted_in_its_scopes(self):
        # Enumerate the table rather than naming today's entries.
        subjects = {"work": {"work": WORK}, "release": {"release": RELEASE}, "recording": {"recording": RECORDING}}
        checked = 0
        for scope, table in ROLE_TYPES.items():
            if scope not in subjects:
                continue
            for name, kind in table.items():
                role = {"type": name}
                if kind.instruments:
                    role["instruments"] = [TROMBONE]
                payload = {"scope": scope, "role": role}
                if kind.place:
                    payload["text"] = "Example place"
                    self.check(claim(0x50, payload, **subjects[scope]))
                else:
                    self.check(claim(0x50, payload, contributor=ANN, **subjects[scope]))
                checked += 1
        self.assertGreater(checked, 60)

    def test_inherited_identifiers_are_well_formed(self):
        uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
        seen = {}
        for table in ROLE_TYPES.values():
            for kind in table.values():
                if kind.gid is None:
                    self.assertEqual(kind.name, "label")
                    continue
                self.assertRegex(kind.gid, uuid)
                # One MusicBrainz type per (pair, name), and one gid per type.
                self.assertEqual(seen.setdefault(kind.gid, (kind.pair, kind.name)), (kind.pair, kind.name))
        self.assertEqual({k.pair for k in ARTIST_RECORDING.values()}, {"artist-recording"})


class ContributionRefusals(unittest.TestCase):
    def refuse(self, c: dict, message: str, targets: dict = TARGETS) -> None:
        with self.assertRaisesRegex(ContributionError, message):
            check_contribution(c, targets)

    def test_role_from_another_scope(self):
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "composer"}}, contributor=BO,
                          recording=RECORDING), "not a MusicBrainz type for a recording scope")
        # MusicBrainz deprecated mastering on a recording; it belongs to a release.
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "mastering"}}, contributor=BO,
                          recording=RECORDING), "not a MusicBrainz type")
        self.refuse(claim(1, {"scope": "work", "role": {"type": "lead guitar"}}, contributor=BO, work=WORK),
                    "not a MusicBrainz type")

    def test_instrument_rules(self):
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "instrument"}}, contributor=ANN,
                          recording=RECORDING), "names at least one instrument")
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "instrument", "instruments": ["trombone"]}},
                          contributor=ANN, recording=RECORDING), "musicbrainz.org/instrument")
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "instrument",
                                                             "instruments": [TROMBONE, TROMBONE]}},
                          contributor=ANN, recording=RECORDING), "repeats")
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "mix", "instruments": [TROMBONE]}},
                          contributor=ANN, recording=RECORDING), "takes no instruments")
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "instrument", "instruments": [TROMBONE],
                                                             "vocals": ["lead vocals"]}},
                          contributor=ANN, recording=RECORDING), "takes no vocals")
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "vocal", "vocals": ["shouting"]}},
                          contributor=ANN, recording=RECORDING), "not a MusicBrainz vocal attribute")

    def test_attributes_must_be_the_types_own(self):
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "instrument", "instruments": [TROMBONE],
                                                             "attributes": ["executive"]}},
                          contributor=ANN, recording=RECORDING), "does not take")
        self.refuse(claim(1, {"scope": "release", "role": {"type": "producer", "attributes": ["executive", "co"]}},
                          contributor=BO, release=RELEASE), "ascending")

    def test_who_and_what(self):
        self.refuse(claim(1, {"scope": "recording", "text": "someone"}, recording=RECORDING),
                    "names a contributor, a role, or both")
        self.refuse(claim(1, {"scope": "recording"}, contributor=ANN, recording=RECORDING),
                    "verbatim text")
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "performer"}}, contributor=WORK,
                          recording=RECORDING), "organisation or person")
        self.refuse(claim(1, {"scope": "release", "role": {"type": "recorded at"}, "text": "a studio"},
                          contributor=BAND, release=RELEASE), "credits a place")
        self.refuse(claim(1, {"scope": "release", "role": {"type": "recorded at"}}, release=RELEASE),
                    "place as written")
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "performer"}, "credited_as": "A."},
                          recording=RECORDING), "credited_as without a contributor")

    def test_subject_must_fit_the_scope(self):
        self.refuse(claim(1, {"scope": "work", "role": {"type": "writer"}}, contributor=BO, work=RECORDING),
                    "kind musical_work")
        self.refuse(claim(1, {"scope": "release", "role": {"type": "mastering"}}, contributor=BO, release=WORK),
                    "kind release")
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "performer"}}, contributor=BO,
                          recording=OLD_SELECTION), "version 2")
        self.refuse(claim(1, {"scope": "work", "role": {"type": "writer"}}, contributor=BO, recording=RECORDING),
                    "cannot have")
        self.refuse(claim(1, {"scope": "track", "role": {"type": "writer"}}, contributor=BO, work=WORK),
                    "scope must be one of")
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "performer"}}, contributor=BO,
                          recording=pin(0x99)), "not a committed revision")

    def test_span_rules(self):
        good = operator_trombone_span()

        def variant(**changes):
            c = copy.deepcopy(good)
            c["payload"].update(changes)
            return c

        self.refuse(variant(spans=[{"start_sample": 0, "end_sample": at(241)}]), "past the stream's sample count")
        self.refuse(variant(spans=[{"start_sample": 10, "end_sample": 10}]), "end after it starts")
        self.refuse(variant(spans=[{"start_sample": 0, "end_sample": 100}, {"start_sample": 50, "end_sample": 200}]),
                    "overlaps")
        self.refuse(variant(sample_rate=44100), "differs from the stream's")
        no_rate = copy.deepcopy(good)
        del no_rate["payload"]["sample_rate"]
        self.refuse(no_rate, "gives sample_rate and spans")
        other_stream = copy.deepcopy(good)
        other_stream["refs"][2] = {"role": "stream", **MULTITRACK_STREAM}
        self.refuse(other_stream, "stream of another Artifact revision")
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "performer"}, "sample_rate": RATE},
                          contributor=ANN, recording=RECORDING), "outside a span scope")

    def test_a_document_span_states_its_alignment(self):
        c = operator_trombone_span()
        c["refs"].append({"role": "document", **DISCOGS})
        c["payload"]["text"] = "Trombone [2:31 to 3:05] – Ann Example"
        self.refuse(c, "alignment exactly when it pins a document")
        c["payload"]["alignment"] = {"method": "manual", "max_error_samples": SECOND}
        check_contribution(c, TARGETS)

    def test_provenance_fields(self):
        self.refuse(claim(1, {"scope": "release", "role": {"type": "mastering"}}, contributor=BO, release=RELEASE,
                          document=BOOKLET), "document's own wording")
        self.refuse(claim(1, {"scope": "release", "role": {"type": "mastering"},
                              "document_url": "https://example.org/r/1"}, contributor=BO, release=RELEASE),
                    "without pinning the document")
        self.refuse(claim(1, {"scope": "release", "role": {"type": "mastering"}, "text": "x",
                              "document_url": "ftp://example.org/r/1"}, contributor=BO, release=RELEASE,
                          document=DISCOGS), "http or https")
        self.refuse(claim(1, {"scope": "release", "role": {"type": "mastering"}, "text": "x",
                              "retrieved_at": "2026-02-30"}, contributor=BO, release=RELEASE, document=DISCOGS),
                    "calendar date")
        self.refuse(claim(1, {"scope": "release", "role": {"type": "mastering"},
                              "confidence": {"scale": "probability", "value": 0.5}}, contributor=BO,
                          release=RELEASE), "exact decimal text")
        self.refuse(claim(1, {"scope": "release", "role": {"type": "mastering"}}, contributor=BO, release=RELEASE,
                          run=RELEASE), "derivation_run")

    def test_channel_rules(self):
        base = {"scope": "channel", "role": {"type": "instrument", "instruments": [ELECTRIC_PIANO]}}
        self.refuse(claim(1, {**base, "channel": 9}, contributor=BO, source=MULTITRACK, stream=MULTITRACK_STREAM),
                    r"channel must be an integer in 1\.\.8")
        self.refuse(claim(1, {**base, "channel": 3}, contributor=BO, source=MULTITRACK),
                    "without the stream")
        self.refuse(claim(1, {**base, "channel": 3}, contributor=BO, source=WORK, stream=MULTITRACK_STREAM),
                    "must pin an Artifact")
        self.refuse(claim(1, {"scope": "recording", "role": {"type": "performer"}, "channel": 1},
                          contributor=BO, recording=RECORDING), "outside a channel scope")

    def test_payload_and_refs_are_closed(self):
        c = booklet_trombone()
        c["payload"]["instrument"] = "trombone"
        self.refuse(c, "unknown")
        c = booklet_trombone()
        c["refs"].append({"role": "contributor", **BO})
        self.refuse(c, "0 to 1 'contributor'")


class Membership(unittest.TestCase):
    def membership(self, payload: dict, **roles) -> dict:
        return {"payload": payload, "refs": refs(**({"band": BAND, "member": ANN} | roles))}

    def test_band_membership_with_dates(self):
        payload = check_band_membership(self.membership({
            "instruments": [TROMBONE], "vocals": ["background vocals"], "attributes": ["original"],
            "from": "2011-03", "to": "2019",
        }), TARGETS)
        self.assertEqual(payload["from"], "2011-03")

    def test_membership_from_a_document(self):
        check_band_membership(self.membership({
            "from": "2011", "ended": True, "text": "Ann Example (trombone, 2011–?)",
            "document_url": "https://musicbrainz.org/artist/00000000-0000-4000-8000-0000000000a4",
            "retrieved_at": "2026-10-18",
        }, document=DISCOGS), TARGETS)

    def test_refusals(self):
        def refuse(payload, message, **roles):
            with self.assertRaisesRegex(ContributionError, message):
                check_band_membership(self.membership(payload, **roles), TARGETS)

        refuse({"from": "2019", "to": "2011-06"}, "ends before it begins")
        refuse({"from": "2019-13"}, "calendar date")
        refuse({"from": "19"}, "YYYY")
        refuse({"to": "2019", "ended": True}, "only when no end date")
        refuse({"ended": False}, "ended is true")
        refuse({"attributes": ["guest"]}, "does not take")
        refuse({}, "kind organisation", band=ANN, member=BO)
        refuse({}, "kind person", member=BAND)
        refuse({}, "document's own wording", document=BOOKLET)
        # A month that contains the start is not "before" it.
        check_band_membership(self.membership({"from": "2019-06-20", "to": "2019-06"}), TARGETS)


class WhosePart(unittest.TestCase):
    def test_a_stack_from_a_players_channel_names_its_contributor(self):
        channel = claim(0x30, {"scope": "channel", "role": {"type": "instrument", "instruments": [ELECTRIC_PIANO]}},
                        contributor=BO, source=STEM)
        unrelated = booklet_trombone()
        stacks = {"payload": {"sample_rate": RATE, "part": "keys", "spans": []},
                  "refs": refs(source=MIX, stream=MIX_STREAM, analysed=STEM)}
        found = part_contributions(stacks, [channel, unrelated])
        self.assertEqual([r for r, _ in found], [channel["revision_id"]])
        self.assertIn("analysed from", found[0][1])

    def test_a_stack_from_a_documents_track_pins_its_contribution(self):
        credit = booklet_trombone()
        stacks = {"payload": {"sample_rate": RATE, "part": "Trombone", "spans": []},
                  "refs": refs(source=MIX, stream=MIX_STREAM, document=DISCOGS) +
                  [{"role": "contribution", "id": "urn:uuid:00000000-0000-4000-8000-000000000010",
                    "revision_id": credit["revision_id"]}]}
        self.assertEqual([r for r, _ in part_contributions(stacks, [credit])], [credit["revision_id"]])
        with self.assertRaisesRegex(ContributionError, "not given"):
            part_contributions(stacks, [])


if __name__ == "__main__":
    unittest.main()
