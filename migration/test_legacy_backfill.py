"""Public retained-package CLI against real Door and synthetic scratch evidence."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from enrich import adapter
from migration import legacy_backfill as command
from migration import legacy_sessions as sessions
from migration.test_legacy_sessions import setup, OPERATOR

ROOT = Path(__file__).resolve().parents[1]


class AtomicPlan(unittest.TestCase):
    def test_new_source_is_atomic_and_audio_guards_survive(self):
        raw, source, reader = setup()
        source_record = reader.by_revision.pop(source['revision_id'])
        del reader.heads[source_record['object_id']]
        class DryStaging:
            key_for = adapter.Staging.key_for
            def remote_name(self, root, create):
                self.assert_create = create
                return 'synthetic-staging'
        staging = DryStaging()
        result, _ = command.build_plan(raw, reader, OPERATOR, staging)
        self.assertFalse(staging.assert_create)
        self.assertEqual(len(result['records']), 5)
        self.assertEqual(result['records'][0]['by'], command.BY)
        self.assertEqual(len(result['manifest']['expected_reference_heads']), 3)
        self.assertEqual(result['records'][-1]['refs'][0]['revision_id'], result['revision_ids'][0])


class PublicDoor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from world.tests.test_conformance import integration_binaries
        cls.door, cls.archive = integration_binaries()

    def setUp(self):
        from fixture.build import Builder
        from world.tests.test_conformance import remove_tree
        self.tmp = Path(tempfile.mkdtemp(prefix='legacy-command-')).resolve()
        self.addCleanup(remove_tree, self.tmp)
        self.git = os.path.realpath(shutil.which('git'))
        self.annex = os.path.realpath(shutil.which('git-annex'))
        self.env = dict(os.environ, ARCHIVE_GIT_EXECUTABLE=self.git, ARCHIVE_GIT_ANNEX_EXECUTABLE=self.annex)
        self.content = self.tmp / 'content'; self.content.mkdir()
        data = b'synthetic scratch recording'
        (self.content / 'audio.bin').write_bytes(data)
        self.document = {'format': sessions.FORMAT, 'groups': [{'id': 'g', 'label': 'Synthetic group',
                         'sources': [{'id': 'mix', 'primary': True,
                                      'files': [{'path': 'audio.bin', 'digest': adapter.digest_of(data)}]}]}]}
        plan = {'operator': 'synthetic-operator', 'steps': [{'name': 'audio', 'command': 'admit', 'records': [
            {'key': 'audio', 'namespace': 'archive.core/artifact', 'name': 'audio', 'shape': 'artifact', 'content': 'audio.bin'}]}]}
        self.root = self.tmp / 'archive'
        self.builder = Builder(self.root, plan, self.content, self.door, self.archive, self.git, self.annex, self.env)
        self.builder.build()
        self.snapshot = self.tmp / 'snapshot.json'
        self.snapshot.write_bytes(adapter.canonical(self.document))
        self.staging = self.tmp / 'new-staging'
        self.output = self.tmp / 'proposal'

    def cli(self, apply=False, output=None, extra=(), forbid_staging=False):
        invocation = [sys.executable, '-m', 'migration.legacy_backfill']
        if forbid_staging:
            script = ("from enrich import adapter; import runpy; "
                      "def_bomb = lambda *a, **k: (_ for _ in ()).throw(AssertionError('staging called')); "
                      "adapter.Staging.stage_file = def_bomb; adapter.Staging.remote_name = def_bomb; "
                      "runpy.run_module('migration.legacy_backfill', run_name='__main__')")
            invocation = [sys.executable, '-c', script]
        return subprocess.run([*invocation, str(self.snapshot),
            '--archive', str(self.root), '--operator', self.builder.operator_id, '--staging', str(self.staging),
            '--output', str(output or self.output), '--archive-command', self.archive, '--door', self.door,
            *(['--apply'] if apply else []), *extra], cwd=ROOT, env=self.env, capture_output=True, text=True)

    def tree(self, root):
        # Include index/config/refs/object bytes, symlink targets and missing directories.
        if not root.exists(): return None
        return {str(p.relative_to(root)): ('link', os.readlink(p)) if p.is_symlink()
                else ('file', adapter.digest_of(p.read_bytes()))
                for p in root.rglob('*') if p.is_symlink() or p.is_file()}

    def head(self):
        return subprocess.check_output([self.git, 'rev-parse', 'HEAD'], cwd=self.root, text=True).strip()

    def test_public_atomic_dry_run_apply_retry_and_second_plan(self):
        before = self.tree(self.root)
        dry = self.cli()
        self.assertEqual(dry.returncode, 0, dry.stderr)
        predicted = json.loads(dry.stdout)
        self.assertEqual(predicted['records'], 4)
        self.assertFalse(predicted['content_verified'])
        self.assertEqual(before, self.tree(self.root))
        self.assertIsNone(self.tree(self.staging))
        applied = self.cli(True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)['revision_ids'], predicted['revision_ids'])
        # Atomic migration_source reference pins the source created in this batch.
        reader = adapter.ArchiveReader(self.root, self.archive, self.env)
        mapping = next(r for r in reader.records if reader.cites(r, sessions.CONTRACT))
        source = next(r for r in mapping['refs'] if r['role'] == 'migration_source')
        self.assertEqual(source['revision_id'], predicted['revision_ids'][0])
        before_retry, staging_before, head = self.tree(self.root), self.tree(self.staging), self.head()
        # Bomb the boundary: an unchanged retry must not even inspect staging remotes.
        retry = self.cli(True, forbid_staging=True)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertTrue(json.loads(retry.stdout)['retry'])
        self.assertEqual(self.tree(self.root), before_retry)
        self.assertEqual(self.tree(self.staging), staging_before)
        self.assertEqual(self.head(), head)
        second = self.cli(output=self.tmp / 'second')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)['records'], 0)
        self.assertEqual(self.head(), head)

    def test_missing_records_report_partial_and_valid_group_still_applies(self):
        missing = copy.deepcopy(self.document['groups'][0]); missing['id'] = 'missing'
        missing['sources'][0]['files'][0]['digest'] = 'sha256:' + 'f' * 64
        self.document['groups'].append(missing)
        self.snapshot.write_bytes(adapter.canonical(self.document))
        dry = self.cli()
        self.assertEqual(dry.returncode, 2, dry.stderr)
        self.assertEqual(json.loads(dry.stdout)['records'], 4)
        report = json.loads((self.output / 'inventory.json').read_bytes())
        self.assertEqual(report['groups'][1]['issues'], ['missing_artifact'])
        self.assertIn('physical', report['missing_artifact_meaning'])
        applied = self.cli(True)
        self.assertEqual(applied.returncode, 2, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)['status'], 'committed')

    def test_changed_snapshot_and_tampered_proposal_refuse_without_staging(self):
        self.assertEqual(self.cli().returncode, 0)
        before = self.tree(self.root)
        original = self.snapshot.read_bytes(); self.snapshot.write_bytes(original + b' ')
        refused = self.cli(True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('context changed', refused.stderr)
        self.snapshot.write_bytes(original)
        (self.output / 'records/0000.json').write_bytes(b'{}')
        refused = self.cli(True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('proposal changed', refused.stderr)
        self.assertEqual(before, self.tree(self.root))
        self.assertFalse(self.staging.exists())

    def test_stale_audio_and_reused_source_heads_refuse_before_staging(self):
        self.assertEqual(self.cli().returncode, 0)
        self.builder.step(2, {'name': 'new audio head', 'command': 'admit', 'records': [
            {'key': 'audio-v2', 'revises': 'audio', 'shape': 'artifact', 'content': 'audio.bin'}]})
        before = self.tree(self.root)
        refused = self.cli(True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(before, self.tree(self.root))
        self.assertFalse(self.staging.exists())

    def test_exact_existing_source_reused_and_stale_head_refuses(self):
        staging = adapter.Staging(self.tmp / 'source-staging', self.git, self.annex, self.env,
                                  remote='source-staging')
        raw = self.snapshot.read_bytes()
        digest = adapter.digest_of(raw)
        key = staging.stage_file(self.snapshot, digest, len(raw), 'snapshot.json')
        remote = staging.remote_name(self.root, create=True)
        self.builder.contents['snapshot.json'] = {'digest': digest, 'size': len(raw),
                                                'annex_key': key, 'staging_remote': remote}
        self.builder.step(2, {'name': 'source', 'command': 'admit', 'records': [
            {'key': 'source', 'namespace': 'archive.core/artifact', 'name': 'source',
             'shape': 'artifact', 'content': 'snapshot.json'}]})
        dry = self.cli()
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(json.loads(dry.stdout)['records'], 3)
        self.builder.step(3, {'name': 'source revision', 'command': 'admit', 'records': [
            {'key': 'source-v2', 'revises': 'source', 'shape': 'artifact', 'content': 'snapshot.json'}]})
        before = self.tree(self.root)
        refused = self.cli(True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(before, self.tree(self.root))
        self.assertFalse(self.staging.exists())


if __name__ == '__main__':
    unittest.main()
