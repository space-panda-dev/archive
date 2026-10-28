"""Archive upgrade public package command, with synthetic real Door archives."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from enrich import adapter
from migration import upgrade

ROOT = Path(__file__).resolve().parents[1]


class PublicReleaseMetadata(unittest.TestCase):
    def test_deployment_descriptor_matches_complete_published_release(self):
        pins, digest = upgrade.release('0.0.12')
        self.assertEqual(json.loads((ROOT / 'deployment/release.json').read_bytes()),
                         {'targetSpecification': '0.0.12', 'targetReleaseDigest': digest, 'targetContracts': pins})


class UpgradePackage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from world.tests.test_conformance import integration_binaries
        cls.door, cls.archive = integration_binaries()

    def setUp(self):
        from fixture.build import Builder
        from world.tests.test_conformance import remove_tree
        self.tmp = Path(tempfile.mkdtemp(prefix='upgrade-package-')).resolve()
        self.addCleanup(remove_tree, self.tmp)
        self.git = os.path.realpath(shutil.which('git'))
        self.annex = os.path.realpath(shutil.which('git-annex'))
        self.env = dict(os.environ, ARCHIVE_GIT_EXECUTABLE=self.git, ARCHIVE_GIT_ANNEX_EXECUTABLE=self.annex,
                        ARCHIVE_UPGRADE_ARCHIVE=self.archive, ARCHIVE_UPGRADE_DOOR=self.door)
        self.root = self.tmp / 'archive'
        builder = Builder(self.root, {'operator': 'synthetic-operator', 'steps': []}, self.tmp,
                          self.door, self.archive, self.git, self.annex, self.env)
        builder.build(); self.operator = builder.operator_id
        self.config = self.tmp / 'private.json'
        self.config.write_text(json.dumps({'schemaVersion': 1, 'archive': str(self.root), 'operator': self.operator}))
        # Exercise a detached package tree with precisely the files in upgradeFor.
        self.package = self.tmp / 'package'; self.package.mkdir()
        (self.package / 'migration').mkdir(); (self.package / 'enrich').mkdir()
        shutil.copy2(ROOT / 'migration/upgrade.py', self.package / 'migration/upgrade.py')
        shutil.copy2(ROOT / 'enrich/adapter.py', self.package / 'enrich/adapter.py')
        shutil.copytree(ROOT / 'spec', self.package / 'spec')
        (self.package / 'bin').mkdir()
        self.executable = self.package / 'bin/archive-upgrade'
        # Absolute interpreter, package entrypoint and pinned real binaries.
        import shlex
        self.executable.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' +
                                   shlex.quote(str(self.package / 'migration/upgrade.py')) + ' "$@"\n')
        self.executable.chmod(0o755)

    def git_run(self, *args):
        return subprocess.check_output([self.git, *args], cwd=self.root, env=self.env, text=True).strip()

    def earlier(self, version='0.0.11'):
        # The real Door's UpgradeChecks uses this same synthetic fixture seam:
        # re-finalize fresh bootstrap records under an earlier published release.
        manifest = json.loads((self.root / 'archive.json').read_bytes())
        manifest['spec'] = 'archive/' + version
        (self.root / 'archive.json').write_bytes(adapter.canonical(manifest))
        self.git_run('rm', '-r', '-q', 'spec/0.0.12')
        shutil.copytree(ROOT / 'spec' / version, self.root / 'spec' / version)
        (self.root / 'spec' / version / 'contracts.json').unlink()
        catalog = json.loads((ROOT / 'spec' / version / 'contracts.json').read_bytes())
        keep = {c['digest'].removeprefix('sha256:') for c in catalog['contracts']}
        for path in (self.root / 'contracts').glob('*.json'):
            if path.stem.removeprefix('sha256-') not in keep:
                self.git_run('rm', '-q', str(path.relative_to(self.root)))
        records = [(p, json.loads(p.read_bytes())) for p in (self.root / 'records').rglob('*.json')]
        records.sort(key=lambda pair: pair[1]['shape'] != 'entity')
        changed = {}
        for path, record in records:
            self.git_run('rm', '-q', str(path.relative_to(self.root)))
            old = record.pop('revision_id'); at = record.pop('at')
            record['spec'] = 'archive/' + version
            for ref in record['refs']:
                ref['revision_id'] = changed.get(ref['revision_id'], ref['revision_id'])
            revision = adapter.revision_of(record); changed[old] = revision
            digest = revision.removeprefix('sha256:')
            target = self.root / f'records/{digest[:2]}/{digest[2:4]}/sha256-{digest}.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(adapter.canonical({**record, 'at': at, 'revision_id': revision}))
        self.git_run('add', 'archive.json', 'spec', 'records')
        self.git_run('-c', 'user.name=Archive tests', '-c', 'user.email=tests@example.invalid',
                     'commit', '-q', '--amend', '--no-edit')

    def tree(self):
        return {str(p.relative_to(self.root)): os.readlink(p) if p.is_symlink() else adapter.digest_of(p.read_bytes())
                for p in self.root.rglob('*') if p.is_file() or p.is_symlink()}

    def assert_unchanged(self, before):
        after = self.tree()
        self.assertEqual([], [key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)])

    def cli(self, action='plan', plan=None, env=None):
        args = [str(self.executable), action, '--config', str(self.config), '--target', '0.0.12', '--json']
        if plan:
            args += ['--plan-digest', plan['planDigest'], '--source-fingerprint', plan['sourceFingerprint']]
        result = subprocess.run(args, cwd=self.tmp, env=env or self.env, text=True, capture_output=True)
        self.assertTrue(result.stdout.strip(), result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_real_upgrade_read_only_plan_exact_commit_and_quiet_retry(self):
        self.earlier()
        before = self.tree()
        code, plan = self.cli()
        self.assertEqual(code, 0, plan)
        self.assertEqual(plan['status'], 'ready')
        self.assertEqual(plan['currentSpecification'], '0.0.11')
        self.assertEqual(plan['targetSpecification'], '0.0.12')
        self.assert_unchanged(before)
        self.assertTrue(all(p['required'] and p['status'] == 'ok' for p in plan['prerequisites']))
        self.assertEqual(plan['targetContracts'], upgrade.release('0.0.12')[0])
        code, receipt = self.cli('apply', plan)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['durableMutation'], 'committed')
        for field in upgrade.FIELDS:
            self.assertEqual(plan[field], receipt[field])
        committed = self.tree()
        code, retry = self.cli('apply', plan)
        self.assertEqual(code, 0, retry)
        self.assertEqual(retry['status'], 'noop')
        self.assertEqual(retry['durableMutation'], 'none')
        self.assert_unchanged(committed)
        code, noop = self.cli()
        self.assertEqual(code, 0, noop)
        self.assertEqual(noop['status'], 'noop')

    def test_packaged_release_public_cli_uses_pinned_runtime(self):
        self.earlier()
        package = os.environ.get('ARCHIVE_UPGRADE_TEST_PACKAGE')
        if package:
            self.executable = Path(package) / 'bin/archive-upgrade'
            self.assertTrue(self.executable.is_file())
            self.env.update(ARCHIVE_UPGRADE_ARCHIVE='/nonexistent/unpinned-archive',
                            ARCHIVE_UPGRADE_DOOR='/nonexistent/unpinned-door')
        before = self.tree()
        code, plan = self.cli()
        self.assertEqual(code, 0, plan)
        self.assertEqual(plan['status'], 'ready')
        self.assert_unchanged(before)
        code, receipt = self.cli('apply', plan)
        self.assertEqual(code, 0, receipt)
        self.assertEqual((receipt['status'], receipt['durableMutation']), ('completed', 'committed'))
        committed = self.tree()
        code, retry = self.cli('apply', plan)
        self.assertEqual(code, 0, retry)
        self.assertEqual((retry['status'], retry['durableMutation']), ('noop', 'none'))
        self.assert_unchanged(committed)

    def test_stale_config_head_and_wrong_pin_refuse_before_writes(self):
        self.earlier()
        _, plan = self.cli()
        self.git_run('-c', 'user.name=Archive tests', '-c', 'user.email=tests@example.invalid',
                     'commit', '-q', '--allow-empty', '-m', 'Synthetic concurrent writer')
        before = self.tree()
        code, receipt = self.cli('apply', plan)
        self.assertEqual(code, 1)
        self.assertEqual((receipt['status'], receipt['durableMutation']), ('refused', 'none'))
        self.assert_unchanged(before)
        _, fresh = self.cli()
        fresh['planDigest'] = 'a' * 64
        code, receipt = self.cli('apply', fresh)
        self.assertEqual(code, 1)
        self.assertEqual(receipt['durableMutation'], 'none')
        config = json.loads(self.config.read_text()); config['operator'] = adapter.object_id(
            'urn:uuid:00000000-0000-4000-8000-000000000000', 'test', 'other')
        self.config.write_text(json.dumps(config))
        code, receipt = self.cli('apply', plan)
        self.assertEqual(code, 1)
        self.assertEqual(receipt['durableMutation'], 'none')
        self.assert_unchanged(before)

    def test_noop_checks_exact_installed_release_and_config_is_semantic(self):
        _, plan = self.cli()
        self.assertEqual(plan['status'], 'noop')
        self.config.write_text(json.dumps(json.loads(self.config.read_text()), indent=4))
        _, same = self.cli()
        self.assertEqual(plan, same)
        target = self.root / 'spec/0.0.12/archive-spec.md'
        target.write_bytes(target.read_bytes() + b'\n')
        before = self.tree()
        code, blocked = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(blocked['status'], 'blocked')
        self.assert_unchanged(before)

    def test_unrelated_admission_between_final_check_and_door_is_atomically_refused(self):
        self.earlier()
        _, plan = self.cli()
        reader = adapter.ArchiveReader(self.root, self.archive, self.env)
        name = 'synthetic-concurrent-person'
        oid = adapter.object_id(reader.archive_id, 'archive.core/entity', name)
        draft = {'spec': reader.spec, 'object_id': oid, 'shape': 'entity',
                 'by': 'pkg:generic/upgrade-race-test@1', 'agent': self.operator,
                 'payload': {'kind': 'person', 'label': 'Synthetic concurrent person'}, 'refs': [], 'supersedes': []}
        race = self.tmp / 'concurrent-proposal'; race.mkdir(); (race / 'records').mkdir()
        (race / 'records/person.json').write_bytes(adapter.canonical(draft))
        manifest = {'protocol': 'archive-admission/1', 'records': ['records/person.json'],
                    'identities': [{'object_id': oid, 'namespace': 'archive.core/entity', 'name': name}],
                    'expected_heads': [{'object_id': oid, 'revision_id': None}], 'contents': []}
        (race / 'manifest.json').write_bytes(adapter.canonical(manifest))
        import shlex
        wrapper = self.tmp / 'racing-door'
        wrapper.write_text('#!/bin/sh\n' +
            'for arg in "$@"; do if test "$arg" = --dry-run; then exec ' + shlex.quote(self.door) + ' "$@"; fi; done\n' +
            shlex.quote(self.door) + ' admit --proposal ' + shlex.quote(str(race)) + ' >/dev/null || exit $?\n' +
            'exec ' + shlex.quote(self.door) + ' "$@"\n')
        wrapper.chmod(0o755)
        before = self.git_run('rev-parse', 'HEAD')
        code, receipt = self.cli('apply', plan, env=dict(self.env, ARCHIVE_UPGRADE_DOOR=str(wrapper)))
        self.assertEqual(code, 1, receipt)
        self.assertEqual((receipt['status'], receipt['durableMutation']), ('refused', 'none'))
        self.assertIn('snapshot', receipt['detail'])
        self.assertNotEqual(before, self.git_run('rev-parse', 'HEAD'))
        after = adapter.ArchiveReader(self.root, self.archive, self.env)
        self.assertIn(oid, after.heads)  # Anchor: the competing real writer did commit.
        self.assertEqual(after.spec, 'archive/0.0.11')
        self.assertFalse((self.root / 'spec/0.0.12').exists())
        upgrade_id = adapter.object_id(reader.archive_id, upgrade.CONTRACT,
            'upgrade-0-0-12-' + plan['sourceFingerprint'])
        self.assertNotIn(upgrade_id, after.heads)

    def test_emit_real_wire_samples(self):
        self.earlier()
        code, plan = self.cli()
        self.assertEqual(code, 0, plan)
        print('ARCHIVE_UPGRADE_SAMPLE ready-plan ' + json.dumps(plan))
        code, receipt = self.cli('apply', plan)
        self.assertEqual(code, 0, receipt)
        print('ARCHIVE_UPGRADE_SAMPLE completed-receipt ' + json.dumps(receipt))
        code, retry = self.cli('apply', plan)
        self.assertEqual(code, 0, retry)
        print('ARCHIVE_UPGRADE_SAMPLE retry-receipt ' + json.dumps(retry))
        config = json.loads(self.config.read_text())
        config['operator'] = 'urn:uuid:00000000-0000-4000-8000-000000000000'
        self.config.write_text(json.dumps(config))
        code, blocked = self.cli()
        self.assertEqual(code, 1, blocked)
        print('ARCHIVE_UPGRADE_SAMPLE blocked-plan ' + json.dumps(blocked))

    def test_post_commit_verification_failure_reports_committed(self):
        self.earlier()
        _, plan = self.cli()
        import shlex
        counter = self.tmp / 'exports'
        failing = self.tmp / 'failing-archive'
        failing.write_text('#!/bin/sh\n' +
            'if test -f ' + shlex.quote(str(counter)) + '; then echo "synthetic post-commit read failure" >&2; exit 1; fi\n' +
            'touch ' + shlex.quote(str(counter)) + '\nexec ' + shlex.quote(self.archive) + ' "$@"\n')
        failing.chmod(0o755)
        code, receipt = self.cli('apply', plan, env=dict(self.env, ARCHIVE_UPGRADE_ARCHIVE=str(failing)))
        self.assertEqual(code, 1)
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['durableMutation'], 'committed')
        self.assertEqual(adapter.ArchiveReader(self.root, self.archive, self.env).spec, 'archive/0.0.12')

    def test_lost_commit_response_reports_unknown_then_exact_retry_succeeds(self):
        self.earlier()
        _, plan = self.cli()
        import shlex
        failing = self.tmp / 'lost-door-response'
        failing.write_text('#!/bin/sh\n' + shlex.quote(self.door) + ' "$@" || exit $?\n' +
                           'for arg in "$@"; do if test "$arg" = --dry-run; then exit 0; fi; done\n' +
                           'echo "synthetic lost committed response" >&2; exit 1\n')
        failing.chmod(0o755)
        code, receipt = self.cli('apply', plan, env=dict(self.env, ARCHIVE_UPGRADE_DOOR=str(failing)))
        self.assertEqual(code, 1)
        self.assertEqual((receipt['status'], receipt['durableMutation']), ('failed', 'unknown'))
        before = self.tree()
        code, retry = self.cli('apply', plan)
        self.assertEqual(code, 0, retry)
        self.assertEqual((retry['status'], retry['durableMutation']), ('noop', 'none'))
        self.assert_unchanged(before)

    def test_authoritative_door_refusal_is_blocked_not_ready(self):
        self.earlier()
        refusing = self.tmp / 'refusing-door'
        refusing.write_text('#!/bin/sh\necho "synthetic Door refusal" >&2\nexit 1\n'); refusing.chmod(0o755)
        before = self.tree()
        code, plan = self.cli(env=dict(self.env, ARCHIVE_UPGRADE_DOOR=str(refusing)))
        self.assertEqual(code, 1)
        self.assertEqual(plan['status'], 'blocked')
        self.assertEqual(plan['prerequisites'][-1]['status'], 'missing')
        self.assert_unchanged(before)


if __name__ == '__main__':
    unittest.main()
