"""A stem is a stem (ADR 0090's amendment): equivalence records and stand-ins.

The extractors here are real programs run by the real runner. `sep` stands
in for a separator: its `voice` output is a file whose bits depend on the
device it was made on, and the software it reports comes from the
environment, so a test can change either. `notes-basic-pitch` stands in for
the downstream measure, writing notes in the real extractor's format; with
BANK_TEST_SHIFT set it moves the notes of anything made on the gpu, so the
two sides disagree. Every record is made by the real writer
(`extract.py equivalence`), never by hand, except where a test damages one.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import bank
import equivalence
import extract
import remote

SEP = r'''
import json, os, sys
request = json.load(open(sys.argv[1]))
device = request["parameters"]["apply"]["device"]
out = request["output_dir"]
source = open(request["input"]["path"], "rb").read()
with open(os.path.join(out, "voice.json"), "w") as handle:
    json.dump({"device": device, "source": len(source)}, handle)
log = os.environ.get("BANK_TEST_LOG")
if log:
    open(log, "a").write("sep:" + device + "\n")
json.dump({"contract": "archive-extractor-result/1",
           "software": [{"name": "lib", "version": os.environ.get("BANK_TEST_LIB", "1")},
                        {"name": "other", "version": os.environ.get("BANK_TEST_OTHER", "1")}],
           "grids": {}, "outputs": {"voice": {}}}, open(os.path.join(out, "result.json"), "w"))
'''

NOTES = r'''
import json, os, sys
request = json.load(open(sys.argv[1]))
out = request["output_dir"]
text = open(request["input"]["path"]).read()
shift = 0.2 if os.environ.get("BANK_TEST_SHIFT") and '"gpu"' in text else 0.0
empty = bool(os.environ.get("BANK_TEST_EMPTY"))
notes = [] if empty else [[0.5 + shift, 0.9 + shift, 60], [1.0 + shift, 1.4 + shift, 62], [2.0, 2.5, 64]]
json.dump({"columns": ["onset_s", "offset_s", "midi"], "notes": notes}, open(os.path.join(out, "notes.json"), "w"))
log = os.environ.get("BANK_TEST_LOG")
if log:
    open(log, "a").write(request["extractor"]["id"] + "\n")
json.dump({"contract": "archive-extractor-result/1", "software": [{"name": "test", "version": "1"}],
           "grids": {}, "outputs": {"notes": {}}}, open(os.path.join(out, "result.json"), "w"))
'''

INTERCHANGEABLE = {"parameter": "apply.device", "values": ["cpu", "gpu"], "software": ["lib"],
                   "agree": [{"measure": "note-f1", "outputs": ["voice"], "at_least": "0.98"}]}


def sep_manifest(**changes) -> dict:
    declared = {"contract": "archive-extractor/1", "id": "sep", "version": "1",
                "command": ["${python}", "program.py"], "accepts": ["audio/*"], "needs": [],
                "parameters": {"apply": {"device": "cpu", "shifts": 0}},
                "interchangeable": INTERCHANGEABLE,
                "outputs": {"voice": {"kind": "file", "media_type": "application/json", "suffix": ".json"}}}
    declared.update(changes)
    return declared


def notes_manifest(extractor_id: str = "notes-basic-pitch", prefers: list | None = None) -> dict:
    declared = {"contract": "archive-extractor/1", "id": extractor_id, "version": "1",
                "command": ["${python}", "program.py"], "accepts": ["audio/*", "application/*"], "needs": [],
                "parameters": {}, "outputs": {"notes": {"kind": "file", "media_type": "application/json",
                                                        "suffix": ".json"}}}
    if prefers is not None:
        declared["prefers"] = prefers
    return declared


class Fixture:
    def __init__(self, test: unittest.TestCase):
        self.dir = Path(tempfile.mkdtemp(prefix="equivalence-"))
        test.addCleanup(shutil.rmtree, self.dir, True)
        self.extractors = self.dir / "extractors"
        self.bank = self.dir / "bank"
        self.content = self.dir / "take.wav"
        self.content.write_bytes(b"RIFF not really audio, but the test extractors do not decode it")
        self.digest = bank.file_digest(self.content)
        self.log = self.dir / "runs.log"
        self.add("sep", SEP, sep_manifest())
        self.add("notes-basic-pitch", NOTES, notes_manifest())
        self.env = patch.dict("os.environ", {"BANK_TEST_LOG": str(self.log)})
        self.env.start()
        test.addCleanup(self.env.stop)

    def add(self, extractor_id: str, program: str, manifest: dict) -> None:
        directory = self.extractors / extractor_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "program.py").write_text(program)
        (directory / "extractor.json").write_text(json.dumps(manifest))

    def config(self, device: str) -> Path:
        path = self.dir / f"{device}.json"
        path.write_text(json.dumps({"parameters": {"sep": {"apply": {"device": device, "shifts": 0}}}}))
        return path

    def catalog(self) -> bank.Catalog:
        return bank.Catalog.load([self.extractors])

    def reader(self, device: str = "cpu", exact: bool = False) -> bank.Bank:
        return bank.Bank(self.bank, self.catalog(), {"sep": {"apply": {"device": device, "shifts": 0}}}, exact)

    def call(self, *argv: str) -> tuple[int, dict]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = extract.main(list(argv))
        return code, json.loads(out.getvalue() or err.getvalue())

    def run(self, target: str, *extra: str) -> tuple[int, dict]:
        return self.call("run", target, "--content", str(self.content), "--bank", str(self.bank),
                         "--extractors", str(self.extractors), *extra)

    def measure(self, against: str = "gpu", *extra: str) -> tuple[int, dict]:
        return self.call("equivalence", "sep", "--content", str(self.content), "--against", against,
                         "--bank", str(self.bank), "--extractors", str(self.extractors), *extra)

    def entry_of(self, device: str) -> bank.Entry | None:
        return self.reader(device, exact=True).entry(self.digest, "sep")

    def drop(self, device: str) -> None:
        """Delete one device's entry: the bank is regenerable."""
        entry = self.entry_of(device)
        assert entry is not None
        shutil.rmtree(entry.path)

    def records(self) -> list[Path]:
        return sorted((self.bank / bank.EQUIVALENCE).rglob("*.json"))

    def runs(self) -> list[str]:
        return self.log.read_text().split() if self.log.exists() else []


class Measures(unittest.TestCase):
    def test_the_measures_implemented_are_the_measures_a_manifest_may_name(self) -> None:
        self.assertEqual(set(equivalence.MEASURES), set(bank.MEASURES))

    def test_note_agreement(self) -> None:
        notes = [(0.5, 60), (1.0, 62), (2.0, 64)]
        self.assertEqual(equivalence.note_agreement(notes, notes)[0], 1.0)
        self.assertEqual(equivalence.note_agreement(notes, [(t + 0.049, m) for t, m in notes])[0], 1.0)
        self.assertEqual(equivalence.note_agreement(notes, [(t + 0.051, m) for t, m in notes])[0], 0.0)
        self.assertEqual(equivalence.note_agreement(notes, [(t, m + 1) for t, m in notes])[0], 0.0)
        # One note may pair once: two estimates on one reference note count one.
        value, detail = equivalence.note_agreement([(0.5, 60)], [(0.5, 60), (0.51, 60)])
        self.assertEqual((round(value, 4), detail["matched"]), (round(2 / 3, 4), 1))
        # Nothing to compare is no evidence, not agreement.
        self.assertIsNone(equivalence.note_agreement([], [])[0])
        self.assertEqual(equivalence.note_agreement(notes, [])[0], 0.0)

    def test_a_measure_is_decided_over_every_input_and_unmeasured_fails(self) -> None:
        agree = [{"measure": "note-f1", "outputs": ["voice"], "at_least": "0.98"}]

        def f1(matched: int, first: int, second: int) -> dict:
            value = 2 * matched / (first + second) if first + second else None
            return {"measured": [{"measure": "note-f1", "output": "voice", "value": value,
                                  "detail": {"matched": matched, "notes": [first, second]}}]}

        self.assertFalse(bank.agreement_passes(agree, [f1(0, 0, 0)]))  # nothing to compare is no evidence
        self.assertTrue(bank.agreement_passes(agree, [f1(0, 0, 0), f1(50, 50, 50)]))
        # One extra note of a short clip fails it alone, not among all the notes.
        self.assertFalse(bank.agreement_passes(agree, [f1(16, 16, 17)]))
        self.assertTrue(bank.agreement_passes(agree, [f1(16, 16, 17), f1(150, 150, 150)]))
        self.assertAlmostEqual(bank.decisions(agree, [f1(16, 16, 17), f1(150, 150, 150)])[0]["value"],
                               332 / 333)
        voicing = [{"measure": "voicing-agreement", "outputs": ["voice"], "at_least": "0.99"}]
        rows = lambda agreeing, either: {"measured": [{"measure": "voicing-agreement", "output": "voice",
                                                       "value": agreeing / either,
                                                       "detail": {"agreeing": agreeing, "voiced_in_either": either}}]}
        self.assertFalse(bank.agreement_passes(voicing, [rows(98, 100)]))
        self.assertTrue(bank.agreement_passes(voicing, [rows(98, 100), rows(1000, 1000)]))
        # The pitch difference is the worst input's.
        at_most = [{"measure": "f0-cents-p99", "outputs": ["voice"], "at_most": "5"}]
        cents = lambda v: {"measured": [{"measure": "f0-cents-p99", "output": "voice", "value": v, "detail": {}}]}
        self.assertTrue(bank.agreement_passes(at_most, [cents(5), cents(0.1)]))
        self.assertFalse(bank.agreement_passes(at_most, [cents(5.01), cents(0.1)]))
        # A detail without what decides it is refused, not read as passing or failing.
        broken = [{"measured": [{"measure": "note-f1", "output": "voice", "value": 1.0, "detail": {}}]}]
        with self.assertRaisesRegex(bank.BankError, "lacks what decides it"):
            bank.agreement_passes(agree, broken)


class Manifest(unittest.TestCase):
    def refused(self, manifest: dict, message: str) -> None:
        with self.assertRaisesRegex(bank.BankError, message):
            bank.check_manifest(manifest, "m")

    def test_a_declaration_is_refused_unless_it_is_exactly_the_shape(self) -> None:
        bank.check_manifest(sep_manifest(), "m")
        self.refused(sep_manifest(interchangeable={**INTERCHANGEABLE, "extra": 1}), "parameter, values")
        self.refused(sep_manifest(interchangeable={**INTERCHANGEABLE, "parameter": "apply.nothing"}),
                     "must be declared")
        self.refused(sep_manifest(interchangeable={**INTERCHANGEABLE, "values": ["gpu", "tpu"]}),
                     "must be declared")
        self.refused(sep_manifest(interchangeable={**INTERCHANGEABLE, "values": ["cpu"]}), "at least two")
        self.refused(sep_manifest(interchangeable={**INTERCHANGEABLE, "software": []}), "software")
        self.refused(sep_manifest(interchangeable={**INTERCHANGEABLE, "agree": [
            {"measure": "loudness", "outputs": ["voice"], "at_least": "1"}]}), "no measure")
        self.refused(sep_manifest(interchangeable={**INTERCHANGEABLE, "agree": [
            {"measure": "note-f1", "outputs": ["nothing"], "at_least": "1"}]}), "file outputs")
        self.refused(sep_manifest(interchangeable={**INTERCHANGEABLE, "agree": [
            {"measure": "note-f1", "outputs": ["voice"], "at_least": 0.98}]}), "decimal text")
        self.refused(sep_manifest(interchangeable={**INTERCHANGEABLE, "agree": [
            {"measure": "note-f1", "outputs": ["voice"], "at_least": "1", "at_most": "2"}]}), "each agreement")
        self.refused(sep_manifest(needs=["x/y"]), "needs nothing")

    def test_the_shipped_stems_declare_cpu_and_mps_and_differ_only_in_the_device(self) -> None:
        catalog = bank.Catalog.load()
        declared = catalog["stems"].interchangeable
        self.assertEqual((declared["parameter"], declared["values"]), ("apply.device", ["cpu", "mps"]))
        self.assertEqual({item["measure"] for item in declared["agree"]}, set(bank.MEASURES))
        (value, mps), = catalog.alternatives("stems")
        cpu = catalog.parameters("stems")
        self.assertNotEqual(cpu, mps)  # anchor
        self.assertEqual((value, bank.with_parameter(mps, "apply.device", "cpu")), ("mps", cpu))
        # Configured for mps, the cpu's stems are the alternative.
        overrides = {"stems": {"apply": {**cpu["apply"], "device": "mps"}}}
        self.assertEqual([v for v, _ in catalog.alternatives("stems", overrides)], ["cpu"])


class StandIn(unittest.TestCase):
    def passing(self, f: Fixture) -> dict:
        code, said = f.measure()
        self.assertEqual(code, 0, said)
        self.assertTrue(said["pass"])
        return said

    def test_a_passing_record_lets_the_other_device_stand_in(self) -> None:
        f = Fixture(self)
        said = self.passing(f)
        self.assertEqual(said["measures"]["note-f1 voice"], {"decided": 1.0, "at_least": "0.98", "pass": True,
                                                             "measured_on": 1, "worst_input": 1.0})
        record = json.loads(Path(said["record"]).read_text())
        cpu, gpu = f.entry_of("cpu"), f.entry_of("gpu")
        self.assertNotEqual(cpu.output("voice")["sha256"], gpu.output("voice")["sha256"])  # anchor: other bits
        self.assertEqual(record["inputs"][0]["entries"], [cpu.manifest["key"], gpu.manifest["key"]])
        self.assertEqual([s["software"] for s in record["sides"]], [[{"name": "lib", "version": "1"}]] * 2)
        f.drop("cpu")
        found = f.reader("cpu").entry(f.digest, "sep")
        self.assertEqual(found.manifest["key"], gpu.manifest["key"])
        self.assertIsNone(f.reader("cpu", exact=True).entry(f.digest, "sep"))
        # Either way round: configured for the gpu, the cpu's stands in for it.
        f.drop("gpu")
        self.assertEqual(f.run("sep", "--config", str(f.config("cpu")), "--exact")[0], 0)
        self.assertEqual(f.reader("gpu").entry(f.digest, "sep").manifest["key"], cpu.manifest["key"])

    def test_without_a_record_the_other_device_does_not_stand_in(self) -> None:
        f = Fixture(self)
        self.assertEqual(f.run("sep", "--config", str(f.config("gpu")))[0], 0)
        self.assertIsNotNone(f.entry_of("gpu"))  # anchor: the other device's entry is there
        self.assertEqual(f.records(), [])
        self.assertIsNone(f.reader("cpu").entry(f.digest, "sep"))

    def test_a_failing_record_lets_nothing_stand_in(self) -> None:
        f = Fixture(self)
        with patch.dict("os.environ", {"BANK_TEST_SHIFT": "1"}):
            code, said = f.measure()
        self.assertEqual(code, 1, said)
        self.assertFalse(said["pass"])
        self.assertAlmostEqual(said["measures"]["note-f1 voice"]["decided"], 1 / 3)
        self.assertEqual(len(f.records()), 1)  # filed all the same
        f.drop("cpu")
        self.assertIsNone(f.reader("cpu").entry(f.digest, "sep"))

    def test_nothing_to_compare_fails_the_record(self) -> None:
        f = Fixture(self)
        with patch.dict("os.environ", {"BANK_TEST_EMPTY": "1"}):
            code, said = f.measure()
        self.assertEqual(code, 1, said)
        self.assertEqual(said["measures"]["note-f1 voice"], {"decided": None, "at_least": "0.98", "pass": False,
                                                             "measured_on": 0, "worst_input": None})

    def test_a_record_for_other_software_lets_nothing_stand_in(self) -> None:
        f = Fixture(self)
        self.passing(f)
        f.drop("cpu")
        self.assertIsNotNone(f.reader("cpu").entry(f.digest, "sep"))  # anchor: it stood in
        f.drop("gpu")
        with patch.dict("os.environ", {"BANK_TEST_LIB": "2"}):
            self.assertEqual(f.run("sep", "--config", str(f.config("gpu")))[0], 0)
        self.assertIsNone(f.reader("cpu").entry(f.digest, "sep"))
        # Software the record does not name does not matter.
        f.drop("gpu")
        with patch.dict("os.environ", {"BANK_TEST_OTHER": "9"}):
            self.assertEqual(f.run("sep", "--config", str(f.config("gpu")))[0], 0)
        self.assertIsNotNone(f.reader("cpu").entry(f.digest, "sep"))

    def test_a_damaged_or_altered_record_is_refused_not_read_as_absent(self) -> None:
        f = Fixture(self)
        with patch.dict("os.environ", {"BANK_TEST_SHIFT": "1"}):
            self.assertEqual(f.measure()[0], 1)
        f.drop("cpu")
        path, = f.records()
        record = json.loads(path.read_text())
        record["pass"] = True  # what its values do not give
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(bank.BankError, "which its measured values do not give"):
            f.reader("cpu").entry(f.digest, "sep")
        path.write_text("{")
        with self.assertRaisesRegex(bank.BankError, "unreadable"):
            f.reader("cpu").entry(f.digest, "sep")
        # The runner fails the extractor rather than making it again.
        code, answer = f.run("sep")
        self.assertEqual((code, answer["extractors"]["sep"]["status"]), (1, "failed"), answer)
        self.assertIn("unreadable", answer["extractors"]["sep"]["error"])
        # Exact asks for the configured key alone, so it reads no record.
        self.assertEqual(f.run("sep", "--exact")[0], 0)

    def test_the_runner_counts_a_stand_in_as_made(self) -> None:
        f = Fixture(self)
        self.passing(f)
        f.drop("cpu")
        gpu = f.entry_of("gpu")
        with patch.object(extract, "run_program", side_effect=AssertionError("ran")):
            code, answer = f.run("sep")
        self.assertEqual(code, 0, answer)
        said = answer["extractors"]["sep"]
        self.assertEqual((said["status"], said["key"]), ("hit", gpu.manifest["key"]))
        self.assertNotEqual(said["stands_in_for"], gpu.manifest["key"])
        code, answer = f.run("sep", "--exact")
        self.assertEqual((code, answer["extractors"]["sep"]["status"]), (0, "ran"))

    def test_a_put_counts_a_stand_in_as_made(self) -> None:
        f = Fixture(self)
        self.passing(f)
        f.drop("cpu")

        class Refusing:
            def lines(self, *arguments, given=b""):
                raise AssertionError("put a task")

        answer = remote.put(f.catalog(), f.bank, Refusing(), "sep", f.content, "audio/wav", None,
                            {"sep": {"apply": {"device": "cpu", "shifts": 0}}})
        self.assertEqual((answer["status"], answer["key"]), ("hit", f.entry_of("gpu").manifest["key"]))
        with self.assertRaisesRegex(AssertionError, "put a task"):
            remote.put(f.catalog(), f.bank, Refusing(), "sep", f.content, "audio/wav", None,
                       {"sep": {"apply": {"device": "cpu", "shifts": 0}}}, exact=True)

    def test_a_measure_made_over_a_stand_in_stays_found_when_the_configured_stem_arrives(self) -> None:
        f = Fixture(self)
        f.add("m", NOTES, notes_manifest("m", prefers=[{"input": "sep/voice"}]))
        self.passing(f)
        f.drop("cpu")
        code, answer = f.run("m")
        self.assertEqual(code, 0, answer)
        gpu = f.entry_of("gpu")
        self.assertEqual(answer["inputs"]["sep/voice"]["from"]["entry"], gpu.manifest["key"])
        measured = f.reader("cpu").entry_from(f.digest, "sep/voice", "m")
        self.assertEqual(measured.manifest["content"], gpu.output("voice")["sha256"])
        # The cpu's stems arrive: its stem is not measured, so the measure over the gpu's still answers.
        self.assertEqual(f.run("sep", "--exact")[0], 0)
        cpu = f.entry_of("cpu")
        reader = f.reader("cpu")
        self.assertEqual(reader.entry(f.digest, "sep").manifest["key"], cpu.manifest["key"])
        self.assertEqual(reader.entry_from(f.digest, "sep/voice", "m").manifest["key"], measured.manifest["key"])
        self.assertEqual(reader.default_entry(f.digest, "m")[0].manifest["key"], measured.manifest["key"])
        before = f.runs()
        self.assertEqual(f.run("m")[0], 0)
        self.assertEqual(f.runs(), before)  # nothing measured twice
        # Over each entry that answers, best first, so a reader can take every measure over one.
        over = reader.measured_over(f.digest, "sep/voice", ["m"])
        self.assertEqual([(s.manifest["key"], m["m"] is not None) for s, m in over],
                         [(cpu.manifest["key"], False), (gpu.manifest["key"], True)])
        self.assertEqual(f.reader("cpu", exact=True).measured_over(f.digest, "sep/voice", ["m"])[0][0].manifest["key"],
                         cpu.manifest["key"])

    def test_a_record_compares_one_software_pairing(self) -> None:
        f = Fixture(self)
        other = f.dir / "other.wav"
        other.write_bytes(b"RIFF another take")
        with patch.dict("os.environ", {"BANK_TEST_LIB": "2"}):
            self.assertEqual(f.call("run", "sep", "--content", str(other), "--bank", str(f.bank),
                                    "--extractors", str(f.extractors), "--exact")[0], 0)
        code, said = f.call("equivalence", "sep", "--content", str(f.content), "--content", str(other),
                            "--against", "gpu", "--bank", str(f.bank), "--extractors", str(f.extractors))
        self.assertEqual(code, 2, said)
        self.assertIn("other software", said["detail"])
        self.assertEqual(f.records(), [])

    def test_against_a_value_not_declared_is_refused(self) -> None:
        f = Fixture(self)
        code, said = f.measure("tpu")
        self.assertEqual(code, 2)
        self.assertIn("not 'tpu'", said["detail"])
        code, said = f.measure("cpu")  # the configured value itself
        self.assertEqual(code, 2)


# A named parameter set (ADR 0112's CPU runtime): `fast` runs on the cpu with
# less chunk overlap, so its value brings an overlap of its own.
SETS = {"cpu": {"apply.overlap": "0.25"}, "gpu": {"apply.overlap": "0.25"}, "fast": {"apply.overlap": "0.1"}}
WITH = {**INTERCHANGEABLE, "values": ["cpu", "gpu", "fast"], "with": SETS}


def apply_of(value: str) -> dict:
    return {"device": value, "shifts": 0, "overlap": SETS[value]["apply.overlap"]}


class SetFixture(Fixture):
    def __init__(self, test: unittest.TestCase):
        super().__init__(test)
        self.add("sep", SEP, sep_manifest(parameters={"apply": apply_of("cpu")}, interchangeable=WITH))

    def config(self, device: str) -> Path:
        path = self.dir / f"{device}.json"
        path.write_text(json.dumps({"parameters": {"sep": {"apply": apply_of(device)}}}))
        return path

    def reader(self, device: str = "cpu", exact: bool = False) -> bank.Bank:
        return bank.Bank(self.bank, self.catalog(), {"sep": {"apply": apply_of(device)}}, exact)


class ParameterSets(unittest.TestCase):
    """`interchangeable.with`: one value names a whole parameter set."""

    def refused(self, declared: dict, message: str, apply: dict | None = None) -> None:
        with self.assertRaisesRegex(bank.BankError, message):
            bank.check_manifest(sep_manifest(parameters={"apply": apply or apply_of("cpu")},
                                             interchangeable=declared), "m")

    def test_a_declaration_names_the_same_declared_fields_for_every_value(self) -> None:
        bank.check_manifest(sep_manifest(parameters={"apply": apply_of("cpu")}, interchangeable=WITH), "m")
        self.refused({**WITH, "with": {"cpu": SETS["cpu"], "gpu": SETS["gpu"]}}, "for every value")
        self.refused({**WITH, "with": {**SETS, "fast": {"apply.shifts": 1}}}, "same fields")
        self.refused({**WITH, "with": {v: {"apply.segment": None} for v in SETS}}, "declared parameter fields")
        self.refused({**WITH, "with": {v: {"apply.device": v} for v in SETS}}, "other than apply.device")
        self.refused({**WITH, "with": {v: {"apply": {}} for v in SETS}}, "other than apply.device")
        # The manifest's own value must be what its `with` says.
        self.refused(WITH, "must be what", apply={**apply_of("cpu"), "overlap": "0.1"})

    def test_each_alternative_carries_the_fields_its_value_brings(self) -> None:
        catalog = SetFixture(self).catalog()
        self.assertEqual(dict(catalog.alternatives("sep")),
                         {"gpu": {"apply": apply_of("gpu")}, "fast": {"apply": apply_of("fast")}})
        configured_fast = {"sep": {"apply": apply_of("fast")}}
        self.assertEqual(dict(catalog.alternatives("sep", configured_fast)),
                         {"cpu": {"apply": apply_of("cpu")}, "gpu": {"apply": apply_of("gpu")}})
        # A value configured with fields it does not bring is no parameter set: nothing stands in.
        mixed = {"sep": {"apply": {**apply_of("fast"), "overlap": "0.25"}}}
        self.assertEqual(catalog.alternatives("sep", mixed), [])

    def test_a_passing_record_lets_a_parameter_set_stand_in_either_way(self) -> None:
        f = SetFixture(self)
        code, said = f.measure("fast")
        self.assertEqual(code, 0, said)
        self.assertEqual(said["sides"], ["cpu", "fast"])
        cpu, fast = f.entry_of("cpu"), f.entry_of("fast")
        self.assertEqual(fast.manifest["parameters"]["apply"]["overlap"], "0.1")  # provenance kept
        self.assertNotEqual(cpu.manifest["key"], fast.manifest["key"])  # anchor
        f.drop("cpu")
        self.assertEqual(f.reader("cpu").entry(f.digest, "sep").manifest["key"], fast.manifest["key"])
        self.assertIsNone(f.reader("gpu").entry(f.digest, "sep"))  # no record for gpu and fast
        self.assertEqual(f.run("sep", "--config", str(f.config("cpu")), "--exact")[0], 0)
        f.drop("fast")
        self.assertEqual(f.reader("fast").entry(f.digest, "sep").manifest["key"], cpu.manifest["key"])


if __name__ == "__main__":
    unittest.main()
