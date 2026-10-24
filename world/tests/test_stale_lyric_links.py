"""Lyric links over time (ADR 0113): the `stale_lyric_links` view and a
Work's lyric documents with their `derived_from` chain.

Run from the repository root:
python3 -m unittest world.tests.test_stale_lyric_links.
Only the standard library; every record is synthetic (INV-23).
"""

import unittest

from world.documents import (
    DOCUMENT_SOURCE,
    NOTES,
    TIMED_LYRICS,
    UNDERLAY,
    lyric_documents,
    stale_lyric_links,
)

MELODY = ("archive.claim/melody", "2")
OPERATOR = "urn:uuid:00000000-0000-4000-8000-00000000aaaa"
BANDMATE = "urn:uuid:00000000-0000-4000-8000-00000000bbbb"
WORK = "urn:uuid:00000000-0000-4000-8000-00000000cccc"
OTHER_WORK = "urn:uuid:00000000-0000-4000-8000-00000000dddd"


def oid(n: int) -> str:
    return f"urn:uuid:00000000-0000-4000-8000-{n:012x}"


def rev(n: int) -> str:
    return "sha256:" + f"{n:064x}"


class Archive:
    """Committed records built one at a time, each a new revision number."""

    def __init__(self):
        self.records: list[dict] = []

    def add(self, object_n: int, contract, refs=(), agent=OPERATOR, supersedes=None, payload=None,
            shape="claim") -> dict:
        n = len(self.records) + 1
        record = {"object_id": oid(object_n), "revision_id": rev(n), "shape": shape, "agent": agent,
                  "at": f"2026-10-20T10:{n:02d}:00Z", "payload": payload or {}, "contract": contract,
                  "refs": [{"role": role, "id": target["object_id"], "revision_id": target["revision_id"]}
                           for role, target in refs],
                  "supersedes": [] if supersedes is None else
                  [{"role": "supersedes", "id": supersedes["object_id"], "revision_id": supersedes["revision_id"]}]}
        self.records.append(record)
        return record

    def revise(self, old: dict, refs=(), agent=None) -> dict:
        return self.add(int(old["object_id"][-12:], 16), old["contract"], refs, agent or old["agent"], supersedes=old)

    def document(self, object_n: int) -> dict:
        return self.add(object_n, None, shape="artifact")


class StaleLyricLinks(unittest.TestCase):
    def setUp(self):
        self.a = Archive()
        self.doc = self.a.document(1)
        self.notes = self.a.add(2, NOTES)
        self.lyrics = self.a.add(3, TIMED_LYRICS, [("document", self.doc), ("notes", self.notes)])

    def stale(self):
        return stale_lyric_links(self.a.records)

    def test_nothing_stale_at_first(self):
        self.assertEqual(self.stale(), [])

    def test_a_newer_notes_revision_leaves_the_lyrics_stale(self):
        newer = self.a.revise(self.notes)
        self.assertEqual(self.stale(), [{
            "claim": self.lyrics["revision_id"], "object_id": self.lyrics["object_id"],
            "contract": list(TIMED_LYRICS), "role": "notes", "pinned": self.notes["revision_id"],
            "superseded_by": newer["revision_id"], "why": "a newer revision of the same claim"}])

    def test_re_asserting_the_lyrics_clears_it(self):
        newer = self.a.revise(self.notes)
        self.a.revise(self.lyrics, [("document", self.doc), ("notes", newer)])
        self.assertEqual(self.stale(), [])

    def test_a_correction_by_the_same_asserter_supersedes(self):
        fixed = self.a.add(4, NOTES, [("corrects", self.notes)])
        [entry] = self.stale()
        self.assertEqual((entry["superseded_by"], entry["why"]), (fixed["revision_id"], "corrected by its own asserter"))

    def test_a_correction_by_someone_else_does_not(self):
        self.a.add(4, NOTES, [("corrects", self.notes)], agent=BANDMATE)
        self.assertEqual(self.stale(), [])

    def test_the_latest_superseding_revision_is_reported(self):
        newer = self.a.revise(self.notes)
        latest = self.a.add(4, NOTES, [("corrects", newer)])
        [entry] = self.stale()
        self.assertEqual(entry["superseded_by"], latest["revision_id"])
        self.assertEqual(entry["why"], "a newer revision of the same claim")

    def test_a_bandmates_lyrics_leave_once_corrected_onto_the_new_notes(self):
        theirs = self.a.add(5, TIMED_LYRICS, [("document", self.doc), ("notes", self.notes)], agent=BANDMATE)
        newer = self.a.revise(self.notes)
        mine = self.a.revise(self.lyrics, [("document", self.doc), ("notes", newer)])
        self.assertEqual([e["claim"] for e in self.stale()], [theirs["revision_id"]])
        self.a.add(6, TIMED_LYRICS, [("document", self.doc), ("notes", newer), ("corrects", theirs)])
        self.assertEqual(self.stale(), [])
        self.assertNotIn(mine["revision_id"], [e["claim"] for e in self.stale()])

    def test_a_correction_still_on_the_old_notes_does_not_clear_it(self):
        theirs = self.a.add(5, TIMED_LYRICS, [("document", self.doc), ("notes", self.notes)], agent=BANDMATE)
        newer = self.a.revise(self.notes)
        self.a.revise(self.lyrics, [("document", self.doc), ("notes", newer)])
        fix = self.a.add(6, TIMED_LYRICS, [("document", self.doc), ("notes", self.notes), ("corrects", theirs)])
        self.assertEqual(sorted(e["claim"] for e in self.stale()), sorted([theirs["revision_id"], fix["revision_id"]]))

    def test_a_correction_pinning_a_later_revision_clears_it(self):
        theirs = self.a.add(5, TIMED_LYRICS, [("document", self.doc), ("notes", self.notes)], agent=BANDMATE)
        newer = self.a.revise(self.notes)
        latest = self.a.revise(newer)
        self.a.revise(self.lyrics, [("document", self.doc), ("notes", latest)])
        self.a.add(6, TIMED_LYRICS, [("document", self.doc), ("notes", latest), ("corrects", theirs)])
        self.assertEqual(self.stale(), [])

    def test_an_underlay_on_a_superseded_melody(self):
        melody = self.a.add(7, MELODY)
        underlay = self.a.add(8, UNDERLAY, [("melody", melody), ("document", self.doc)])
        finished = self.a.revise(melody)
        self.assertEqual([(e["claim"], e["role"], e["superseded_by"]) for e in self.stale()],
                         [(underlay["revision_id"], "melody", finished["revision_id"])])

    def test_a_document_derived_by_its_source_asserter_supersedes(self):
        self.a.add(9, DOCUMENT_SOURCE, [("document", self.doc)], payload={"media_type": "text/plain"})
        edited = self.a.document(10)
        self.a.add(11, DOCUMENT_SOURCE, [("document", edited), ("derived_from", self.doc)],
                   payload={"media_type": "text/plain"})
        [entry] = self.stale()
        self.assertEqual((entry["role"], entry["pinned"], entry["superseded_by"]),
                         ("document", self.doc["revision_id"], edited["revision_id"]))
        self.assertEqual(entry["why"], "a document derived from it by the same asserter")

    def test_a_document_derived_by_someone_else_does_not(self):
        self.a.add(9, DOCUMENT_SOURCE, [("document", self.doc)], payload={"media_type": "text/plain"})
        edited = self.a.document(10)
        self.a.add(11, DOCUMENT_SOURCE, [("document", edited), ("derived_from", self.doc)], agent=BANDMATE,
                   payload={"media_type": "text/plain"})
        self.assertEqual(self.stale(), [])

    def test_a_superseded_lyric_claim_is_not_listed(self):
        self.a.revise(self.notes)
        self.a.add(12, TIMED_LYRICS, [("document", self.doc), ("notes", self.notes), ("corrects", self.lyrics)])
        # The operator's own correction supersedes their lyrics; the correction is judged instead.
        self.assertEqual([e["claim"] for e in self.stale()], [self.a.records[-1]["revision_id"]])


class LyricDocuments(unittest.TestCase):
    def test_most_recent_first_with_the_chain(self):
        a = Archive()
        first = a.document(1)
        a.add(2, DOCUMENT_SOURCE, [("document", first), ("work", {"object_id": WORK, "revision_id": rev(900)})],
              payload={"media_type": "text/plain", "kinds": ["lyrics"], "retrieved_at": "2026-01-02"})
        second = a.document(3)
        a.add(4, DOCUMENT_SOURCE, [("document", second), ("derived_from", first),
                                   ("work", {"object_id": WORK, "revision_id": rev(900)})],
              payload={"media_type": "text/plain", "kinds": ["lyrics"], "retrieved_at": "2026-10-20T10:00:00Z"})
        third = a.document(5)
        a.add(6, DOCUMENT_SOURCE, [("document", third), ("derived_from", second),
                                   ("work", {"object_id": WORK, "revision_id": rev(900)})],
              payload={"media_type": "text/plain", "kinds": ["lyrics"], "retrieved_at": "2026-10-20T11:00:00Z"})
        chords = a.document(7)
        a.add(8, DOCUMENT_SOURCE, [("document", chords), ("work", {"object_id": WORK, "revision_id": rev(900)})],
              payload={"media_type": "text/plain", "kinds": ["chords"]})
        elsewhere = a.document(9)
        a.add(10, DOCUMENT_SOURCE, [("document", elsewhere),
                                    ("work", {"object_id": OTHER_WORK, "revision_id": rev(901)})],
              payload={"media_type": "text/plain", "kinds": ["lyrics"]})
        found = lyric_documents(WORK, a.records)
        self.assertEqual([e["document"]["revision_id"] for e in found],
                         [third["revision_id"], second["revision_id"], first["revision_id"]])
        self.assertEqual(found[0]["chain"], [second["revision_id"], first["revision_id"]])
        self.assertEqual(found[0]["derived_from"], [second["revision_id"]])
        self.assertEqual(found[2]["chain"], [])
        self.assertEqual(found[0]["agents"], [OPERATOR])
        self.assertEqual(found[0]["media_type"], "text/plain")

    def test_a_revised_source_claim_counts_once(self):
        a = Archive()
        doc = a.document(1)
        work = ("work", {"object_id": WORK, "revision_id": rev(900)})
        old = a.add(2, DOCUMENT_SOURCE, [("document", doc), work], payload={"media_type": "text/plain"})
        new = a.add(2, DOCUMENT_SOURCE, [("document", doc), work], supersedes=old,
                    payload={"media_type": "text/plain", "kinds": ["lyrics"]})
        [entry] = lyric_documents(WORK, a.records)
        self.assertEqual(entry["sources"], [new["revision_id"]])
        self.assertIsNone(entry["retrieved_at"])


if __name__ == "__main__":
    unittest.main()
