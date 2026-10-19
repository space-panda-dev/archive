"""Tracklists, the audio of a recording, and catalogue correspondence
(ADR 0087): accepted claims, malformed claims refused, and credits per track.

Run from the repository root: python3 -m unittest world.tests.test_catalogue.
Every example is synthetic (INV-23): the people, band, release, and catalogue
numbers are invented. The MBIDs are random UUIDs, not MusicBrainz entries.
"""

import copy
import unittest

from world.catalogue import (
    CATALOGUES,
    CORRESPONDENCE,
    RECORDING_AUDIO,
    TRACKLIST,
    CatalogueError,
    check_correspondence,
    check_recording_audio,
    check_tracklist,
    corresponding,
    external_entry,
    track_credits,
    tracks,
)
from world.contributions import check_contribution


def pin(n: int) -> dict:
    return {"id": f"urn:uuid:00000000-0000-4000-8000-{n:012x}", "revision_id": "sha256:" + f"{n:02x}" * 32}


def ref(role: str, target: dict) -> dict:
    return {"role": role, **target}


def entity(kind: str) -> dict:
    return {"shape": "entity", "kind": kind}


PLAYER = pin(0xA1)          # a person
BAND = pin(0xA2)            # an organisation
ENGINEER = pin(0xA3)        # a person
RELEASE = pin(0xB1)         # one edition
OTHER_RELEASE = pin(0xB2)
REC_1, REC_2, REC_3 = pin(0xC1), pin(0xC2), pin(0xC3)   # recording Entities
WORK = pin(0xC9)            # a musical_work: not a recording
RIP = pin(0xD1)             # an Artifact: a file ripped from the release
TAKE = pin(0xD2)            # an audio_selection version 2
OLD_TAKE = pin(0xD3)        # an audio_selection version 1
SAVED_RESPONSE = pin(0xE1)  # a saved catalogue response, admitted as an Artifact
RUN = pin(0xE2)

TARGETS = {
    PLAYER["revision_id"]: entity("person"),
    BAND["revision_id"]: entity("organisation"),
    ENGINEER["revision_id"]: entity("person"),
    RELEASE["revision_id"]: entity("release"),
    OTHER_RELEASE["revision_id"]: entity("release"),
    REC_1["revision_id"]: entity("recording"),
    REC_2["revision_id"]: entity("recording"),
    REC_3["revision_id"]: entity("recording"),
    WORK["revision_id"]: entity("musical_work"),
    RIP["revision_id"]: {"shape": "artifact"},
    TAKE["revision_id"]: {"shape": "claim", "contract": ("archive.claim/audio_selection", "2")},
    OLD_TAKE["revision_id"]: {"shape": "claim", "contract": ("archive.claim/audio_selection", "1")},
    SAVED_RESPONSE["revision_id"]: {"shape": "artifact"},
    RUN["revision_id"]: {"shape": "claim", "contract": ("archive.claim/derivation_run", "1")},
}

MB_ARTIST = "https://musicbrainz.org/artist/0b9e4c7a-3f1d-4c2e-9a6b-1d2e3f4a5b6c"
MB_RELEASE = "https://musicbrainz.org/release/7a1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e"
MB_GROUP = "https://musicbrainz.org/release-group/1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
MB_RECORDING = "https://musicbrainz.org/recording/2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f6a"
MB_WORK = "https://musicbrainz.org/work/3e4f5a6b-7c8d-4e9f-8a1b-2c3d4e5f6a7b"
DISCOGS_ARTIST = "https://www.discogs.com/artist/123456"
DISCOGS_RELEASE = "https://www.discogs.com/release/9876543"
DISCOGS_MASTER = "https://www.discogs.com/master/24680"


def tracklist(n: int = 0x10, **extra) -> dict:
    """A two-disc release: disc 1 carries recordings 1 and 2, disc 2 carries
    recording 3 and then recording 1 again (a reprise of the same audio)."""
    payload = {"media": [
        {"position": 1, "format": "CD", "tracks": [
            {"position": 1, "number": "1", "title": "First Song", "length_ms": 215000},
            {"position": 2, "number": "2", "title": "Second Song"}]},
        {"position": 2, "format": "CD", "title": "Bonus Disc", "tracks": [
            {"position": 1, "number": "1", "title": "Third Song", "artist_text": "The Band feat. A Guest"},
            {"position": 2, "number": "2", "title": "First Song (single edit)"}]},
    ]}
    payload.update(extra)
    return {"revision_id": "sha256:" + f"{n:02x}" * 32, "agent": "operator", "payload": payload,
            "refs": [ref("release", RELEASE), ref("track", REC_1), ref("track", REC_2),
                     ref("track", REC_3), ref("track", REC_1)]}


def with_target(claim: dict) -> dict:
    """TARGETS plus `claim` as a committed revision."""
    return {**TARGETS, claim["revision_id"]: {"shape": "claim", "contract": TRACKLIST,
                                             "payload": claim["payload"], "refs": claim["refs"]}}


def audio_claim(payload: dict, *refs: dict, n: int = 0x20) -> dict:
    return {"revision_id": "sha256:" + f"{n:02x}" * 32, "agent": "operator", "payload": payload, "refs": list(refs)}


def correspondence(external: str, mapping: str, subject: tuple, *more: dict, n: int = 0x30, **extra) -> dict:
    return {"revision_id": "sha256:" + f"{n:02x}" * 32, "agent": "operator",
            "payload": {"external": external, "mapping": mapping, **extra},
            "refs": [ref(*subject), *more]}


class Names(unittest.TestCase):
    def test_contracts_are_new_version_1_contracts(self):
        self.assertEqual(TRACKLIST, ("archive.claim/tracklist", "1"))
        self.assertEqual(RECORDING_AUDIO, ("archive.claim/recording_audio", "1"))
        self.assertEqual(CORRESPONDENCE, ("archive.claim/catalogue_correspondence", "1"))

    def test_every_catalogue_pattern_is_anchored_and_distinct(self):
        for kind in CATALOGUES:
            self.assertTrue(kind.pattern.pattern.endswith(r"\Z"), kind)
        self.assertEqual(len({(k.catalogue, k.entry) for k in CATALOGUES}), len(CATALOGUES))
        self.assertEqual(external_entry(MB_GROUP).entry, "release-group")
        self.assertEqual(external_entry(DISCOGS_MASTER).entry, "master")


class Tracklists(unittest.TestCase):
    def check(self, claim):
        return check_tracklist(claim, TARGETS)

    def refused(self, claim, fragment=""):
        with self.assertRaises(CatalogueError) as caught:
            self.check(claim)
        self.assertIn(fragment, str(caught.exception))

    def test_two_discs_accepted_and_the_same_recording_may_recur(self):
        claim = tracklist()
        self.check(claim)
        listed = tracks(claim)
        self.assertEqual([(m, t) for m, t, _ in listed], [(1, 1), (1, 2), (2, 1), (2, 2)])
        self.assertEqual(listed[0][2]["id"], listed[3][2]["id"])

    def test_from_a_saved_response_with_its_provenance(self):
        claim = tracklist(document_url="https://example.org/ws/release/1", retrieved_at="2026-10-18",
                          locator="media")
        claim["refs"].append(ref("document", SAVED_RESPONSE))
        self.check(claim)

    def test_document_fields_need_the_document(self):
        self.refused(tracklist(document_url="https://example.org/ws/release/1"), "without pinning")

    def test_a_machine_pins_its_run(self):
        claim = tracklist(confidence={"scale": "lookup score 0-100", "value": "97"})
        claim["refs"].append(ref("run", RUN))
        self.check(claim)
        claim["refs"][-1] = ref("run", TAKE)
        self.refused(claim, "derivation_run")

    def test_a_pregap_track_is_position_0_and_first(self):
        claim = tracklist()
        claim["payload"]["media"][0]["tracks"].insert(0, {"position": 0, "title": "Hidden"})
        claim["refs"].insert(1, ref("track", REC_2))
        self.check(claim)
        claim["payload"]["media"][0]["tracks"][0]["position"] = 1
        self.refused(claim, "after the one before")

    def test_one_track_reference_per_track(self):
        claim = tracklist()
        claim["refs"].pop()
        self.refused(claim, "one per track")

    def test_tracks_pin_recording_entities(self):
        claim = tracklist()
        claim["refs"][1] = ref("track", WORK)
        self.refused(claim, "recording")
        claim["refs"][1] = ref("track", RIP)
        self.refused(claim, "recording")

    def test_subject_is_a_release_entity(self):
        claim = tracklist()
        claim["refs"][0] = ref("release", REC_1)
        self.refused(claim, "release")
        claim["refs"].pop(0)
        self.refused(claim, "'release'")

    def test_a_complete_list_leaves_no_gap(self):
        claim = tracklist()
        claim["payload"]["media"][0]["tracks"][1]["position"] = 3
        self.refused(claim, "no gap")
        claim["payload"]["media"][0]["tracks"][1]["position"] = 1
        self.refused(claim, "after the one before")
        claim = tracklist()
        claim["payload"]["media"][1]["position"] = 3
        self.refused(claim, "numbers its media")

    def test_a_partial_list_may_leave_gaps_but_stays_ordered(self):
        claim = tracklist(partial=True)
        claim["payload"]["media"][1]["position"] = 3
        claim["payload"]["media"][0]["tracks"][1]["position"] = 7
        self.check(claim)
        claim["payload"]["media"][0]["tracks"][1]["position"] = 1
        self.refused(claim, "after the one before")
        self.refused(tracklist(partial=False), "partial is true")

    def test_payload_is_closed(self):
        self.refused(tracklist(title="An Album"), "unknown")
        claim = tracklist()
        claim["payload"]["media"][0]["tracks"][0]["isrc"] = "XX0000000000"
        self.refused(claim, "unknown")
        claim = tracklist()
        del claim["payload"]["media"][0]["tracks"][0]["position"]
        self.refused(claim, "missing")

    def test_field_bounds(self):
        for change, fragment in (
            (lambda p: p["media"][0]["tracks"][0].update(length_ms=0), "length_ms"),
            (lambda p: p["media"][0]["tracks"][0].update(length_ms=True), "length_ms"),
            (lambda p: p["media"][0]["tracks"][0].update(number=""), "number"),
            (lambda p: p["media"][0]["tracks"][0].update(title="x" * 501), "title"),
            (lambda p: p["media"][0].update(format=3), "format"),
            (lambda p: p["media"][0].update(tracks=[]), "tracks"),
            (lambda p: p.update(media=[]), "media"),
        ):
            claim = tracklist()
            change(claim["payload"])
            self.refused(claim, fragment)

    def test_references_pin_exact_revisions(self):
        claim = tracklist()
        del claim["refs"][1]["revision_id"]
        self.refused(claim, "missing")
        claim = tracklist()
        claim["refs"].append({"role": "work", **WORK})
        self.refused(claim, "cannot have")


class RecordingAudio(unittest.TestCase):
    def setUp(self):
        self.listed = tracklist()
        self.targets = with_target(self.listed)

    def check(self, claim):
        return check_recording_audio(claim, self.targets)

    def refused(self, claim, fragment=""):
        with self.assertRaises(CatalogueError) as caught:
            self.check(claim)
        self.assertIn(fragment, str(caught.exception))

    def test_a_rip_is_a_whole_recording_as_a_track(self):
        self.check(audio_claim({"extent": "whole", "medium": 2, "track": 2},
                               ref("recording", REC_1), ref("audio", RIP),
                               ref("tracklist", {"id": f"urn:uuid:00000000-0000-4000-8000-{0x10:012x}",
                                                 "revision_id": self.listed["revision_id"]})))

    def test_a_take_is_a_recording_without_a_tracklist(self):
        self.check(audio_claim({"extent": "whole"}, ref("recording", REC_2), ref("audio", TAKE)))
        self.check(audio_claim({"extent": "part", "text": "the fade is cut short"},
                               ref("recording", REC_2), ref("audio", RIP)))

    def test_audio_is_an_artifact_or_a_version_2_take(self):
        self.refused(audio_claim({"extent": "whole"}, ref("recording", REC_2), ref("audio", OLD_TAKE)),
                     "version 2")
        self.refused(audio_claim({"extent": "whole"}, ref("recording", REC_2), ref("audio", REC_1)), "audio")

    def test_recording_is_a_recording_entity(self):
        self.refused(audio_claim({"extent": "whole"}, ref("recording", WORK), ref("audio", RIP)), "recording")

    def test_extent_is_closed(self):
        self.refused(audio_claim({"extent": "most"}, ref("recording", REC_1), ref("audio", RIP)), "extent")
        self.refused(audio_claim({}, ref("recording", REC_1), ref("audio", RIP)), "missing")

    def track_ref(self):
        return ref("tracklist", {"id": f"urn:uuid:00000000-0000-4000-8000-{0x10:012x}",
                                 "revision_id": self.listed["revision_id"]})

    def test_the_track_must_carry_the_same_recording(self):
        self.refused(audio_claim({"extent": "whole", "medium": 1, "track": 2},
                                 ref("recording", REC_1), ref("audio", RIP), self.track_ref()),
                     "another recording")

    def test_the_track_must_be_listed(self):
        self.refused(audio_claim({"extent": "whole", "medium": 3, "track": 1},
                                 ref("recording", REC_1), ref("audio", RIP), self.track_ref()),
                     "does not list")

    def test_position_exactly_with_a_tracklist(self):
        self.refused(audio_claim({"extent": "whole", "medium": 1, "track": 1},
                                 ref("recording", REC_1), ref("audio", RIP)), "exactly when")
        self.refused(audio_claim({"extent": "whole", "medium": 1},
                                 ref("recording", REC_1), ref("audio", RIP), self.track_ref()), "exactly when")
        self.refused(audio_claim({"extent": "whole"},
                                 ref("recording", REC_1), ref("audio", RIP), self.track_ref()), "exactly when")

    def test_the_tracklist_must_be_a_tracklist(self):
        self.refused(audio_claim({"extent": "whole", "medium": 1, "track": 1},
                                 ref("recording", REC_1), ref("audio", RIP), ref("tracklist", TAKE)),
                     "tracklist version 1")


class Correspondence(unittest.TestCase):
    def check(self, claim):
        return check_correspondence(claim, TARGETS)

    def refused(self, claim, fragment=""):
        with self.assertRaises(CatalogueError) as caught:
            self.check(claim)
        self.assertIn(fragment, str(caught.exception))

    def test_every_accepted_pairing(self):
        for external, mapping, subject in (
            (MB_ARTIST, "skos:exactMatch", ("artist", PLAYER)),
            (MB_ARTIST, "skos:closeMatch", ("artist", BAND)),
            (DISCOGS_ARTIST, "skos:exactMatch", ("artist", BAND)),
            (MB_RELEASE, "skos:exactMatch", ("release", RELEASE)),
            (DISCOGS_RELEASE, "skos:closeMatch", ("release", RELEASE)),
            (MB_GROUP, "skos:broadMatch", ("release", RELEASE)),
            (DISCOGS_MASTER, "skos:broadMatch", ("release", RELEASE)),
            (MB_RECORDING, "skos:exactMatch", ("recording", REC_1)),
        ):
            with self.subTest(external=external, mapping=mapping):
                self.check(correspondence(external, mapping, subject))

    def test_a_group_of_editions_is_only_broader_than_an_edition(self):
        self.refused(correspondence(DISCOGS_MASTER, "skos:exactMatch", ("release", RELEASE)), "broadMatch")
        self.refused(correspondence(MB_GROUP, "skos:closeMatch", ("release", RELEASE)), "broadMatch")
        self.refused(correspondence(MB_RELEASE, "skos:broadMatch", ("release", RELEASE)), "exactMatch")

    def test_subject_and_entry_must_be_the_same_kind_of_thing(self):
        self.refused(correspondence(MB_RECORDING, "skos:exactMatch", ("release", RELEASE)),
                     "'release' reference to a musicbrainz recording")
        self.refused(correspondence(MB_ARTIST, "skos:exactMatch", ("recording", REC_1)),
                     "'recording' reference to a musicbrainz artist")
        self.refused(correspondence(MB_RELEASE, "skos:exactMatch", ("artist", PLAYER)),
                     "'artist' reference to a musicbrainz release")

    def test_works_stay_with_adr_0042(self):
        self.refused(correspondence(MB_WORK, "skos:exactMatch", ("recording", REC_1)), "canonical")

    def test_discogs_has_no_recordings(self):
        self.refused(correspondence("https://www.discogs.com/recording/1", "skos:exactMatch",
                                    ("recording", REC_1)), "canonical")

    def test_only_canonical_uris(self):
        for uri in ("http://musicbrainz.org/artist/0b9e4c7a-3f1d-4c2e-9a6b-1d2e3f4a5b6c",
                    MB_ARTIST.upper(), MB_ARTIST + "/", "https://www.discogs.com/release/9876543-Some-Title",
                    "https://discogs.com/release/9876543", "https://www.discogs.com/release/0123",
                    "https://www.discogs.com/artist/1234567890123", 12345):
            with self.subTest(uri=uri):
                self.refused(correspondence(uri, "skos:exactMatch", ("artist", PLAYER)), "canonical")

    def test_no_identity_mapping(self):
        for term in ("owl:sameAs", "skos:relatedMatch", "skos:narrowMatch", "exactMatch"):
            self.refused(correspondence(MB_ARTIST, term, ("artist", PLAYER)), "only by")

    def test_exactly_one_subject_of_the_right_kind(self):
        self.refused(correspondence(MB_ARTIST, "skos:exactMatch", ("artist", PLAYER), ref("release", RELEASE)),
                     "exactly one")
        claim = correspondence(MB_ARTIST, "skos:exactMatch", ("artist", PLAYER))
        claim["refs"] = []
        self.refused(claim, "exactly one")
        self.refused(correspondence(MB_ARTIST, "skos:exactMatch", ("artist", RELEASE)), "organisation or person")
        self.refused(correspondence(MB_RECORDING, "skos:exactMatch", ("recording", RIP)), "recording")
        self.refused(correspondence(MB_ARTIST, "skos:exactMatch", ("work", WORK)), "cannot have")

    def test_provenance_of_an_import_and_of_a_machine(self):
        self.check(correspondence(MB_RELEASE, "skos:exactMatch", ("release", RELEASE), ref("document", SAVED_RESPONSE),
                                  document_url="https://example.org/ws/release/1",
                                  retrieved_at="2026-10-18T09:00:00Z"))
        self.check(correspondence(MB_RECORDING, "skos:closeMatch", ("recording", REC_1), ref("run", RUN),
                                  confidence={"scale": "fingerprint score 0-1", "value": "0.93"}))
        self.refused(correspondence(MB_RELEASE, "skos:exactMatch", ("release", RELEASE),
                                    retrieved_at="2026-10-18"), "without pinning")
        self.refused(correspondence(MB_RELEASE, "skos:exactMatch", ("release", RELEASE), ref("document", SAVED_RESPONSE),
                                    retrieved_at="2026-13-40"), "retrieved_at")
        self.refused(correspondence(MB_RECORDING, "skos:closeMatch", ("recording", REC_1), ref("run", RUN),
                                    confidence={"scale": "score", "value": 0.93}), "decimal")

    def test_payload_is_closed(self):
        self.refused(correspondence(MB_ARTIST, "skos:exactMatch", ("artist", PLAYER), title="A Name"), "unknown")

    def test_an_importer_recognises_entities_and_merges_none(self):
        first = correspondence(MB_ARTIST, "skos:exactMatch", ("artist", PLAYER), n=0x31)
        second = correspondence(MB_ARTIST, "skos:closeMatch", ("artist", BAND), n=0x32)
        other = correspondence(DISCOGS_ARTIST, "skos:exactMatch", ("artist", PLAYER), n=0x33)
        for claim in (first, second, other):
            self.check(claim)
        found = corresponding(MB_ARTIST, [first, second, other])
        self.assertEqual([entity_id for entity_id, _, _ in found], [PLAYER["id"], BAND["id"]])
        self.assertEqual(corresponding(MB_GROUP, [first, second, other]), [])


def contribution(n: int, payload: dict, *refs: dict) -> dict:
    return {"revision_id": "sha256:" + f"{n:02x}" * 32, "agent": "operator", "payload": payload, "refs": list(refs)}


class CreditsPerTrack(unittest.TestCase):
    def setUp(self):
        self.listed = tracklist()
        # A release-wide producer and a release-wide player on an instrument.
        self.producer = contribution(0x40, {"scope": "release", "role": {"type": "producer"}},
                                     ref("contributor", ENGINEER), ref("release", RELEASE))
        self.release_bass = contribution(0x41, {"scope": "release", "role": {"type": "performer"}},
                                         ref("contributor", PLAYER), ref("release", RELEASE))
        # The same player, credited on one recording.
        self.track_bass = contribution(0x42, {"scope": "recording", "role": {"type": "performer"}},
                                       ref("contributor", PLAYER), ref("recording", REC_2))
        # A credit on archived audio, tied to the recording by recording audio.
        self.take_credit = contribution(0x43, {"scope": "recording", "role": {"type": "mix"}},
                                        ref("contributor", ENGINEER), ref("recording", TAKE))
        self.on_take = audio_claim({"extent": "whole"}, ref("recording", REC_2), ref("audio", TAKE), n=0x50)
        # Another release's credit never reaches this recording.
        self.elsewhere = contribution(0x44, {"scope": "release", "role": {"type": "producer"}},
                                      ref("contributor", PLAYER), ref("release", OTHER_RELEASE))
        targets = {**TARGETS, TAKE["revision_id"]: TARGETS[TAKE["revision_id"]]}
        for claim in (self.producer, self.release_bass, self.track_bass, self.take_credit, self.elsewhere):
            check_contribution(claim, targets)
        check_tracklist(self.listed, TARGETS)
        check_recording_audio(self.on_take, TARGETS)

    def credits(self, recording):
        return track_credits(recording["id"], [self.listed],
                             [self.producer, self.release_bass, self.track_bass, self.take_credit, self.elsewhere],
                             [self.on_take])

    def test_release_credits_reach_every_track_with_where_it_sits(self):
        for recording in (REC_1, REC_2, REC_3):
            with self.subTest(recording=recording["id"]):
                found = {c.revision_id: c for c in self.credits(recording)}
                self.assertIn(self.producer["revision_id"], found)
                self.assertEqual(found[self.producer["revision_id"]].via, "release")
                self.assertNotIn(self.elsewhere["revision_id"], found)
        why = {c.revision_id: c.why for c in self.credits(REC_1)}[self.producer["revision_id"]]
        self.assertIn("medium 1 track 1", why)
        self.assertIn("medium 2 track 2", why)

    def test_a_recording_credit_refines_the_release_credit_and_neither_is_hidden(self):
        found = {c.revision_id: c for c in self.credits(REC_2)}
        self.assertNotEqual(self.release_bass["revision_id"], self.track_bass["revision_id"])
        self.assertEqual(found[self.release_bass["revision_id"]].refined_by, (self.track_bass["revision_id"],))
        self.assertEqual(found[self.track_bass["revision_id"]].via, "recording")
        self.assertEqual(found[self.producer["revision_id"]].refined_by, ())
        self.assertEqual({c.revision_id: c for c in self.credits(REC_1)}[self.release_bass["revision_id"]].refined_by,
                         ())

    def test_a_credit_on_archived_audio_reaches_the_recording_through_recording_audio(self):
        found = {c.revision_id: c for c in self.credits(REC_2)}
        self.assertEqual(found[self.take_credit["revision_id"]].via, "audio")
        self.assertIn(self.on_take["revision_id"], found[self.take_credit["revision_id"]].why)
        self.assertNotIn(self.take_credit["revision_id"], {c.revision_id for c in self.credits(REC_1)})

    def test_without_a_tracklist_no_release_credit_reaches_a_recording(self):
        found = track_credits(REC_1["id"], [], [self.producer, self.release_bass])
        self.assertEqual(found, [])

    def test_two_tracklists_that_disagree_are_both_named(self):
        other = copy.deepcopy(self.listed)
        other["revision_id"] = "sha256:" + "11" * 32
        other["refs"][1] = ref("track", REC_3)   # this source puts recording 3 first
        found = track_credits(REC_3["id"], [self.listed, other], [self.producer])
        self.assertEqual(len(found), 1)
        self.assertIn(self.listed["revision_id"], found[0].why)
        self.assertIn(other["revision_id"], found[0].why)


if __name__ == "__main__":
    unittest.main()
