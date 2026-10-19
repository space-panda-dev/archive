"""The window matcher's corpus profile cache (profile_cache.py): what it
returns is the bits a fresh measurement makes, its key changes with
everything that decides those bits, and a damaged entry is refused."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import cover  # first: it pins BLAS to one thread before numpy loads it
import numpy
import profile_cache
import support
from support import needs_ffmpeg
from test_cover import needs_essentia

SOFTWARE = (("ffmpeg", "7.1"), ("essentia", cover.ESSENTIA_BUILD))
DIGEST = "sha256:" + "ab" * 32


def _bomb(*_, **__):
    raise AssertionError("must not be called")


class Keys(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "profile-cache-")
        self.piece = cover.Piece(Path("/nowhere.wav"), 0, 48000, 48000)

    def key(self, software=SOFTWARE, ffmpeg="ffmpeg", piece=None, digest=DIGEST) -> str:
        return profile_cache.ProfileCache(self.tmp, software, ffmpeg).key([piece or self.piece], [digest])

    def test_the_key_changes_with_everything_that_decides_the_bits(self) -> None:
        base = self.key()
        self.assertEqual(base, self.key())
        # The file's path is not in the key: content is named by its digest.
        self.assertEqual(base, self.key(piece=cover.Piece(Path("/elsewhere.wav"), 0, 48000, 48000)))
        others = [
            self.key(digest="sha256:" + "cd" * 32),
            self.key(piece=cover.Piece(self.piece.path, 1, 48000, 48000)),
            self.key(piece=cover.Piece(self.piece.path, 0, 48001, 48000)),
            self.key(piece=cover.Piece(self.piece.path, 0, 48000, 44100)),
            self.key(software=(("ffmpeg", "7.2"), SOFTWARE[1])),
            self.key(ffmpeg=str(self.tmp / "another-ffmpeg")),
        ]
        self.assertEqual(len({base, *others}), len(others) + 1)
        with patch.dict(cover.ANALYSIS, {"hop_size": 1024}):
            self.assertNotEqual(self.key(), base)
        with patch.object(profile_cache, "cpu_model", lambda: "another CPU"):
            self.assertNotEqual(self.key(), base)

    def test_the_pieces_are_keyed_in_order(self) -> None:
        cache = profile_cache.ProfileCache(self.tmp, SOFTWARE, "ffmpeg")
        other = cover.Piece(self.piece.path, 48000, 96000, 48000)
        self.assertNotEqual(cache.key([self.piece, other], [DIGEST, DIGEST]),
                            cache.key([other, self.piece], [DIGEST, DIGEST]))
        with self.assertRaises(profile_cache.CacheError):
            cache.key([self.piece], [])


class Entries(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "profile-cache-")
        self.cache = profile_cache.ProfileCache(self.tmp / "cache", SOFTWARE, "ffmpeg")
        rng = numpy.random.default_rng(7)
        self.features = cover.features_of(rng.random((40, 12), dtype=numpy.float32), "test")
        self.key = "0f" * 32

    def test_a_miss_is_none_and_a_stored_entry_returns_the_same_bits(self) -> None:
        self.assertIsNone(self.cache.get(self.key))
        self.cache.put(self.key, self.features)
        got = self.cache.get(self.key)
        self.assertIsInstance(got, cover.Features)
        self.assertEqual(got.profiles.dtype, self.features.profiles.dtype)
        self.assertEqual(got.profiles.shape, self.features.profiles.shape)
        self.assertEqual(got.profiles.tobytes(), self.features.profiles.tobytes())
        self.assertEqual(got.mean, self.features.mean)
        self.assertTrue(got.profiles.flags.writeable)
        self.assertEqual((self.cache.stored, self.cache.hits), (1, 1))

    def test_a_refusal_for_too_short_audio_is_kept_word_for_word(self) -> None:
        refusal = cover.TooShort("4800 samples make 5 frames; the alignment needs more than 9")
        self.cache.put(self.key, refusal)
        got = self.cache.get(self.key)
        self.assertIsInstance(got, cover.TooShort)
        self.assertEqual(str(got), str(refusal))

    def test_an_entry_already_there_is_kept(self) -> None:
        self.cache.put(self.key, self.features)
        other = cover.features_of(self.features.profiles[:20] + 1, "other")
        self.cache.put(self.key, other)
        self.assertEqual(self.cache.get(self.key).profiles.tobytes(), self.features.profiles.tobytes())
        self.assertEqual(self.cache.stored, 1)

    def test_an_entry_stored_by_another_run_first_is_kept(self) -> None:
        real_rename = profile_cache.os.rename

        def racing(source, target):
            # Another run's entry appears between the check and the rename.
            with patch.object(profile_cache.os, "rename", real_rename):
                profile_cache.ProfileCache(self.cache.root, SOFTWARE, "ffmpeg").put(self.key, self.features)
            return real_rename(source, target)
        other = cover.features_of(self.features.profiles[:20] + 1, "other")
        with patch.object(profile_cache.os, "rename", racing):
            self.cache.put(self.key, other)
        self.assertEqual(self.cache.get(self.key).profiles.tobytes(), self.features.profiles.tobytes())
        self.assertEqual(self.cache.stored, 0)
        self.assertEqual([p.name for p in (self.cache.root / self.key[:2]).iterdir()], [self.key])

    def test_a_root_that_cannot_hold_entries_is_refused(self) -> None:
        blocked = self.tmp / "file"
        blocked.write_text("")
        cache = profile_cache.ProfileCache(blocked, SOFTWARE, "ffmpeg")
        with self.assertRaisesRegex(profile_cache.CacheError, "cannot hold entries"):
            cache.put(self.key, self.features)

    def test_a_damaged_entry_is_refused_never_taken_for_a_miss(self) -> None:
        self.cache.put(self.key, self.features)
        where = self.cache.directory(self.key)
        data = bytearray((where / "profiles.bin").read_bytes())
        data[5] ^= 1
        (where / "profiles.bin").write_bytes(bytes(data))
        with self.assertRaisesRegex(profile_cache.CacheError, r"not the bytes it states\); delete it"):
            self.cache.get(self.key)
        (where / "profiles.bin").unlink()
        with self.assertRaisesRegex(profile_cache.CacheError, "damaged"):
            self.cache.get(self.key)
        (where / "entry.json").write_text("{")
        with self.assertRaisesRegex(profile_cache.CacheError, "damaged"):
            self.cache.get(self.key)
        (where / "entry.json").unlink()
        with self.assertRaisesRegex(profile_cache.CacheError, "damaged"):
            self.cache.get(self.key)

    def test_an_entry_for_another_key_is_refused(self) -> None:
        self.cache.put(self.key, self.features)
        other = "1e" * 32
        self.cache.directory(other).parent.mkdir(parents=True, exist_ok=True)
        self.cache.directory(self.key).rename(self.cache.directory(other))
        with self.assertRaisesRegex(profile_cache.CacheError, "not an entry of this cache for this key"):
            self.cache.get(other)

    def test_an_entry_states_what_decided_it(self) -> None:
        self.cache.put(self.key, self.features)
        entry = json.loads((self.cache.directory(self.key) / "entry.json").read_text())
        self.assertEqual(entry["basis"]["software"], [list(pair) for pair in SOFTWARE])
        self.assertEqual(entry["basis"]["analysis"], json.loads(json.dumps(cover.ANALYSIS)))
        self.assertEqual(entry["shape"], [40, 12])


@needs_essentia
@needs_ffmpeg
class MeasureCorpus(unittest.TestCase):
    """`profile_cache.measure_corpus` against `Comparer.measure_corpus` on
    real audio."""

    def setUp(self) -> None:
        self.tmp = support.scratch(self, "profile-cache-")
        rate = 48000
        self.pieces = {}
        for name, notes, seed in (("same", support.TUNE, 2), ("other", support.OTHER_TUNE, 3)):
            samples = support.tune_samples(notes, rate, seed=seed)
            path = support.write_wav(self.tmp / f"{name}.wav", samples, rate)
            self.pieces[name] = (cover.Piece(path, 0, len(samples), rate),)
        self.pieces["short"] = (cover.Piece(self.pieces["same"][0].path, 0, 4800, rate),)
        self.pieces["two"] = (cover.Piece(self.pieces["same"][0].path, 0, 96000, rate),
                              cover.Piece(self.pieces["other"][0].path, 48000, 144000, rate))
        self.digests = {key: tuple(f"sha256:{key}{i}" for i, _ in enumerate(pieces))
                        for key, pieces in self.pieces.items()}

    def cache(self) -> profile_cache.ProfileCache:
        return profile_cache.ProfileCache(self.tmp / "cache", SOFTWARE, "ffmpeg")

    def assert_same(self, got: dict, wanted: dict) -> None:
        self.assertEqual(list(got), list(wanted))
        for key in wanted:
            self.assertEqual(type(got[key]), type(wanted[key]), key)
            if isinstance(wanted[key], cover.Features):
                self.assertEqual(got[key].profiles.tobytes(), wanted[key].profiles.tobytes(), key)
                self.assertEqual(got[key].profiles.shape, wanted[key].profiles.shape, key)
                self.assertEqual(got[key].mean, wanted[key].mean, key)
            else:
                self.assertEqual(str(got[key]), str(wanted[key]), key)

    def test_read_back_is_what_a_fresh_measurement_makes_and_nothing_is_decoded(self) -> None:
        with cover.Comparer(1) as comparer:
            fresh = comparer.measure_corpus(self.pieces)
        # Anchor: the corpus holds features and a refusal, and they differ.
        self.assertIsInstance(fresh["short"], cover.TooShort)
        self.assertNotEqual(fresh["same"].profiles.tobytes(), fresh["other"].profiles.tobytes())
        decoded = []
        cache = self.cache()
        with cover.Comparer(1) as comparer:
            first = profile_cache.measure_corpus(comparer, self.pieces, self.digests, cache, decoded.extend)
            first_corpus = comparer.corpus
        self.assertEqual(decoded, sorted(self.pieces))
        self.assertEqual(cache.stored, 4)
        self.assert_same(first, fresh)
        cache = self.cache()
        with cover.Comparer(1) as comparer, patch.object(cover, "load", _bomb), \
                patch.object(cover.Comparer, "measure_corpus", _bomb):
            again = profile_cache.measure_corpus(comparer, self.pieces, self.digests, cache, _bomb)
            self.assertEqual(list(comparer.corpus), list(first_corpus))
            self.assertEqual(list(comparer.corpus), ["other", "same", "two"])
        self.assertEqual((cache.hits, cache.stored), (4, 0))
        self.assert_same(again, fresh)

    def test_hits_and_misses_together_give_the_comparer_every_entry_in_order(self) -> None:
        cache = self.cache()
        with cover.Comparer(1) as comparer:
            profile_cache.measure_corpus(comparer, {"same": self.pieces["same"]}, self.digests, cache)
        decoded = []
        for jobs in (1, 2):
            with cover.Comparer(jobs) as comparer:
                got = profile_cache.measure_corpus(comparer, self.pieces, self.digests, self.cache(),
                                                   decoded.append)
                self.assertEqual(list(comparer.corpus), ["other", "same", "two"])
                query = comparer.features(self.pieces["two"])
                distances = comparer.distances(query, sorted(comparer.corpus))
            with cover.Comparer(1) as comparer:
                fresh = comparer.measure_corpus(self.pieces)
                self.assertEqual(comparer.distances(query, sorted(comparer.corpus)), distances)
            self.assert_same(got, fresh)
        # The first pass measured what the cache lacked; the second read it all.
        self.assertEqual(decoded, [["other", "short", "two"]])

    def test_without_a_cache_everything_is_measured_as_before(self) -> None:
        decoded = []
        with cover.Comparer(1) as comparer:
            got = profile_cache.measure_corpus(comparer, self.pieces, self.digests, None, decoded.extend)
        with cover.Comparer(1) as comparer:
            fresh = comparer.measure_corpus(self.pieces)
        self.assertEqual(decoded, sorted(self.pieces))
        self.assert_same(got, fresh)
        self.assertFalse((self.tmp / "cache").exists())

    def test_a_failure_to_decode_is_not_kept(self) -> None:
        cache = self.cache()
        broken = {"gone": (cover.Piece(self.tmp / "absent.wav", 0, 48000, 48000),)}
        with cover.Comparer(1) as comparer:
            got = profile_cache.measure_corpus(comparer, broken, {"gone": ("sha256:gone",)}, cache)
        self.assertIsInstance(got["gone"], cover.CoverError)
        self.assertNotIsInstance(got["gone"], cover.TooShort)
        self.assertEqual(cache.stored, 0)
        self.assertFalse((self.tmp / "cache").exists())


if __name__ == "__main__":
    unittest.main()
