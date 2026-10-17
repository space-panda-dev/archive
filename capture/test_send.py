"""`capture send`: a card hashed here, streamed over ssh, and imported there.

The "remote" machine is this one: a fake ssh runs the command it is given
locally, so tar, the transfer, and `capture import` against an archive made
by `archive init` and the real Door are all real.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "enrich"))

import send  # noqa: E402
import support  # noqa: E402
from grammar import ZoomH4E  # noqa: E402
from support import needs_door  # noqa: E402
from test_capture import card_file, fake_program, mic, music, snapshot  # noqa: E402


@needs_door
class Send(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "capture-send-")
        self.env = support.environment()
        self.root = self.tmp / "archive"
        completed = subprocess.run([support.ARCHIVE, "init", "--operator", "send-operator", str(self.root)],
                                           cwd=self.tmp, capture_output=True, text=True, env=self.env)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.media = self.tmp / "media"
        self.media.mkdir()
        self.inbox = self.tmp / "inbox"
        self.card = self.tmp / "card"
        self.card.mkdir()
        # The "remote" shell: ssh <host> <command> runs the command here.
        self.ssh = fake_program(self.tmp, "ssh", 'echo "$1" >> "$0.hosts"\nexec sh -c "$2"\n')
        self.config = self.write_config()

    def write_config(self, **changes: object) -> Path:
        config = {"host": "archive-machine", "inbox": str(self.inbox),
                  "import": [sys.executable, str(HERE / "capture.py"), "import", "--archive", str(self.root),
                             "--staging", str(self.tmp / "staging"), "--derived-media", str(self.media),
                             "--door", support.DOOR, "--archive-command", support.ARCHIVE,
                             "--ffmpeg", support.FFMPEG, "--ffprobe", support.FFPROBE],
                  **changes}
        path = self.tmp / "send.json"
        path.write_text(json.dumps(config))
        return path

    def run_send(self, *extra: str, ssh: str | None = None, tar: str = "tar") -> tuple[int, dict]:
        argv = [str(self.card), "--config", str(self.config), "--ssh", ssh or self.ssh, "--tar", tar, *extra]
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env), redirect_stdout(out), redirect_stderr(err):
            code = send.main(argv, (ZoomH4E(),))
        text = out.getvalue() or err.getvalue()
        return code, json.loads(text.strip().splitlines()[-1] if not out.getvalue() else text)

    def batches(self) -> list[Path]:
        return sorted(p for p in self.inbox.iterdir() if p.is_dir()) if self.inbox.exists() else []

    def test_a_card_is_sent_imported_and_sent_again_without_change(self) -> None:
        lr = card_file(self.card, "010101_120000_TrLR.WAV", music(24))
        card_file(self.card, "010101_120000_TrMic.WAV", mic(24))
        (self.card / "._010101_120000_TrLR.WAV").write_bytes(b"macOS companion")
        before = snapshot(self.card)

        code, answer = self.run_send()
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        [batch] = self.batches()
        # Exactly the listed files arrived, as the bytes hashed on the card.
        self.assertEqual(sorted(p.name for p in batch.iterdir()), ["010101_120000_TrLR.WAV", "010101_120000_TrMic.WAV"])
        self.assertEqual((batch / lr.name).read_bytes(), lr.read_bytes())
        manifest = json.loads((self.inbox / f"{batch.name}.manifest.json").read_text())
        self.assertEqual([entry["path"] for entry in manifest["files"]], sorted(p.name for p in batch.iterdir()))
        self.assertEqual(answer["chain"][0]["probe"], "committed")
        self.assertEqual(snapshot(self.card), before)
        self.assertEqual(Path(self.ssh + ".hosts").read_text().split(), ["archive-machine"] * 4)

        head = support.head(self.root)
        code, again = self.run_send()
        self.assertEqual((code, again["status"]), (0, "unchanged"), again)
        self.assertEqual(self.batches(), [batch])
        self.assertEqual(support.head(self.root), head)

    def test_a_card_the_importer_would_refuse_is_refused_before_any_transfer(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        (self.card / "notes.txt").write_text("not recorder output")
        bomb = fake_program(self.tmp, "ssh-bomb", 'touch "$0.called"; exit 9\n')
        code, answer = self.run_send(ssh=bomb)
        self.assertEqual(code, 1, answer)
        self.assertIn("no recorder grammar recognises notes.txt", answer["detail"])
        self.assertFalse(Path(bomb + ".called").exists())

    def test_bytes_changed_in_transfer_are_refused_by_the_importer(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        # A transfer that delivers a file, then damages one byte of it.
        damaging = fake_program(self.tmp, "ssh-damaging",
                                'sh -c "$2" || exit $?\n'
                                'case "$2" in tar*) f=$(ls -d "$(echo "$2" | sed "s/.* -C //; s/\'//g")"/* | head -1)\n'
                                '  printf "X" | dd of="$f" bs=1 seek=100 conv=notrunc 2>/dev/null;; esac\n')
        head = support.head(self.root)
        code, answer = self.run_send(ssh=damaging)
        self.assertEqual(code, 1, answer)
        self.assertIn("the directory is not what the manifest lists: changed 010101_120000_TrLR.WAV", answer["detail"])
        self.assertEqual(support.head(self.root), head)

    def test_a_dry_run_sends_and_predicts_but_commits_nothing(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        head = support.head(self.root)
        code, answer = self.run_send("--dry-run")
        self.assertEqual((code, answer["status"]), (0, "predicted"), answer)
        self.assertEqual(support.head(self.root), head)

    def test_a_failing_ssh_or_tar_is_a_failure(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        unreachable = fake_program(self.tmp, "ssh-unreachable", 'echo "no route to host" >&2; exit 255\n')
        code, answer = self.run_send(ssh=unreachable)
        self.assertEqual(code, 1, answer)
        self.assertIn("making the inbox directory failed (255): no route to host", answer["detail"])
        broken_tar = fake_program(self.tmp, "tar-broken", 'echo "cannot read" >&2; exit 2\n')
        code, answer = self.run_send(tar=broken_tar)
        self.assertEqual(code, 1, answer)
        self.assertIn("failed", answer["detail"])
        code, answer = self.run_send(ssh=str(self.tmp / "absent-ssh"))
        self.assertIn("ssh could not run", answer["detail"])

    def test_a_configuration_without_its_fields_is_refused(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        for changes in ({"host": ""}, {"inbox": "relative/inbox"}, {"import": []}, {"import": "capture import"}):
            self.config = self.write_config(**changes)
            code, answer = self.run_send()
            self.assertEqual(code, 1, answer)
            self.assertIn("needs a host, an absolute inbox path, and an import command", answer["detail"])
        self.config = self.tmp / "absent.json"
        code, answer = self.run_send()
        self.assertIn("could not be read", answer["detail"])

    def test_an_empty_card_is_a_failure(self) -> None:
        code, answer = self.run_send()
        self.assertEqual(code, 1, answer)
        self.assertIn("holds no file to send", answer["detail"])


if __name__ == "__main__":
    unittest.main()
