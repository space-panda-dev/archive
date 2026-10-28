"""Explicit-region dispatch against real whole and region bank entries.

Models and the admission transport are stand-ins; bank identity, stem placement,
registered notes resolution, World validation, dispatch and rerun selection are real.
"""
from __future__ import annotations
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import adapter
import bank
import extract
import propose
import support
from test_stems import fake_python as stems_python
from test_vocal_extractor import fake_python as vocal_python


class Reader:
    def __init__(self, artifact):
        self.artifact = artifact
        self.runs = []

    def head(self, _):
        return self.artifact

    def citation(self, _):
        pass

    def refresh(self, *_):
        pass

    def equivalent_run(self, run):
        for previous in self.runs:
            if (run.method_id, run.method_version, run.software, run.used, run.parameters, run.outputs) == (
                previous.method_id, previous.method_version, previous.software, previous.used,
                previous.parameters, previous.outputs
            ):
                return {"object_id": "urn:uuid:00000000-0000-4000-8000-000000000003",
                        "revision_id": "sha256:" + "3" * 64}
        return None


@support.needs_ffmpeg
class ExplicitRegion(unittest.TestCase):
    def test_dispatch_selects_region_stem_despite_existing_whole_success_and_reruns_quietly(self):
        directory = Path(tempfile.mkdtemp(prefix="explicit-region-"))
        self.addCleanup(shutil.rmtree, directory, True)
        content = support.sine_wav(directory / "tone.wav", 16000, "8")
        root = directory / "bank"
        programs = ["--program", f"demucs-python={stems_python(directory)}",
                    "--program", f"f0-python={vocal_python(directory)}"]
        common = ["--content", str(content), "--bank", str(root), *programs]
        for over in ([], ["--region", "32000:96000:16000"]):
            for target in (["stems"], ["vocal-contour", "--from", "stems/vocals"]):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = extract.main(["run", *target, *common, *over])
                self.assertEqual(code, 0, out.getvalue())
        digest = bank.file_digest(content)
        artifact = {"shape": "artifact", "object_id": "urn:uuid:00000000-0000-4000-8000-000000000001",
                    "revision_id": "sha256:" + "1" * 64, "payload": {"content": {"digest": digest}}}
        stream = {"object_id": "urn:uuid:00000000-0000-4000-8000-000000000002",
                  "revision_id": "sha256:" + "2" * 64,
                  "refs": [{"role": "artifact", "id": artifact["object_id"], "revision_id": artifact["revision_id"]}],
                  "payload": {"stream_index": 0, "sample_rate": 16000, "sample_count": 128000}}
        args = argparse.Namespace(all=False, artifact=artifact["object_id"], stream=None, proposers="notes",
                                  bank=str(root), config=None, region=None, dry_run=False, door="unused",
                                  archive_command="unused")
        reader = Reader(artifact)

        def admit(_, run, **kwargs):
            reader.runs.append(run)
            return adapter.Admission("committed", {"object_id": "urn:uuid:00000000-0000-4000-8000-000000000003",
                                                   "revision_id": "sha256:" + "3" * 64}, [{}])

        def dispatch():
            return propose.run(args, {}, reader, None, lambda *_: [stream], lambda *_: stream)

        with patch.object(adapter, "propose", side_effect=admit) as transport:
            code, answer = dispatch()
            self.assertEqual((code, answer["status"]), (0, "committed"))
            self.assertEqual(len(answer["proposers"]), 1)  # whole success suppresses automatic fallback
            args.region = "32000:96000:16000"
            code, answer = dispatch()
            self.assertEqual((code, answer["status"]), (0, "committed"))
            self.assertEqual(len(answer["proposers"]), 1)
            run = reader.runs[-1]
            over = {"start": 32000, "end": 96000, "rate": 16000}
            selected = bank.Bank(root, bank.Catalog.load().within(digest, over))
            stems = selected.entry(digest, "stems")
            contour = selected.entry_from(digest, "stems/vocals", "vocal-contour")
            self.assertEqual(stems.region, over)
            self.assertEqual(contour.manifest["content"], stems.output("vocals")["sha256"])
            features = {f["feature"]: f for f in run.parameters["features"]}
            self.assertEqual(features["stems/vocals"]["entry"], stems.manifest["key"])
            self.assertEqual(features["vocal-contour/notes"]["entry"], contour.manifest["key"])
            self.assertNotEqual(reader.runs[0].parameters["features"], run.parameters["features"])
            notes = run.outputs[0].payload["notes"]
            self.assertTrue(notes)
            self.assertTrue(all(32000 <= n["onset_sample"] < n["offset_sample"] <= 96000 for n in notes))
            self.assertEqual(notes[0]["onset_sample"], 32000)
            code, answer = dispatch()
            self.assertEqual((code, answer["status"]), (0, "unchanged"))
            self.assertEqual(transport.call_count, 2)
            args.region = "32000:200000:16000"
            with self.assertRaisesRegex(propose.ProposeError, "within the selected stream"):
                dispatch()
            args.region = "32000:96000:44100"
            with self.assertRaisesRegex(propose.ProposeError, "sample rate"):
                dispatch()
            args.all = True
            with self.assertRaisesRegex(propose.ProposeError, "one --artifact"):
                dispatch()


if __name__ == "__main__":
    unittest.main()
