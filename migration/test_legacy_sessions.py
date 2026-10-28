"""Legacy grouping, ambiguity and real Door provenance proof (ADR 0124)."""
import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from enrich import adapter
from migration.legacy_sessions import BackfillError, CONTRACT, FORMAT, PARTS, plan, write_plan

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ID = 'urn:uuid:3edd5cbf-d5f0-4e7d-b719-3cdd76150f61'
OPERATOR = adapter.object_id(ARCHIVE_ID, 'archive.core/entity', 'operator')


def snapshot():
    # Nonalphabetic rollover order is deliberately authoritative.
    return {'format': FORMAT, 'groups': [{'id': 'group-a', 'label': 'Legacy label 2001', 'sources': [
        {'id': 'mix', 'primary': True, 'files': [{'path': 'rollover-10.wav', 'digest': 'sha256:'+'b'*64},
                                               {'path': 'rollover-2.wav', 'digest': 'sha256:'+'a'*64}]},
        {'id': 'room', 'primary': False, 'files': [{'path': 'room.wav', 'digest': 'sha256:'+'c'*64}]}]}]}


def setup(document=None):
    raw = adapter.canonical(document or snapshot())
    records = []
    def record(name, shape, payload):
        d = {'spec': 'archive/0.0.12', 'object_id': adapter.object_id(ARCHIVE_ID, 'test', name),
             'shape': shape, 'payload': payload, 'refs': [], 'supersedes': [],
             'by': 'pkg:generic/test@1', 'agent': OPERATOR}
        d['revision_id'] = adapter.revision_of(d)
        records.append(d)
        return d
    operator = record('person', 'entity', {'kind': 'person'})
    operator['object_id'] = OPERATOR
    for letter in 'abc':
        record(letter, 'artifact', {'content': {'digest': 'sha256:'+letter*64, 'size': 10}})
    source = record('snapshot', 'artifact', {'content': {'digest': adapter.digest_of(raw), 'size': len(raw)}})
    catalog = json.loads((ROOT/'spec/0.0.12/contracts.json').read_text())
    bundles = [json.loads((ROOT/('spec/contracts/'+c['digest'].replace(':','-')+'.json')).read_text()) for c in catalog['contracts']]
    reader = adapter.ArchiveReader.from_export(Path('/tmp'), {'archive_id': ARCHIVE_ID, 'spec': 'archive/0.0.12',
                                                            'records': records, 'contracts': bundles})
    return raw, {'object_id': source['object_id'], 'revision_id': source['revision_id']}, reader


class LegacyInventory(unittest.TestCase):
    def test_preserves_group_sources_and_rollover_order_with_exact_pins(self):
        raw, source, reader = setup()
        result = plan(raw, source, reader, OPERATOR)
        self.assertEqual(len(result['records']), 4)
        entity, mix, room, mapping = result['records']
        expected = [r for r in reader.records if r['shape'] == 'artifact' and r['payload']['content']['digest'] in ['sha256:'+'b'*64, 'sha256:'+'a'*64]]
        self.assertEqual([r['id'] for r in mix['refs'][1:]], [expected[1]['object_id'], expected[0]['object_id']])
        self.assertEqual(result['manifest']['identities'][0]['name'], 'session-'+'b'*64)
        self.assertEqual(mapping['payload']['primary_source'], 'mix')
        self.assertEqual(mapping['payload']['source_pointer'], '/groups/0')
        self.assertEqual(mapping['refs'][0]['revision_id'], source['revision_id'])
        self.assertEqual(mapping['refs'][1]['revision_id'], adapter.revision_of(entity))
        self.assertEqual([r['revision_id'] for r in mapping['refs'][2:]], [adapter.revision_of(mix), adapter.revision_of(room)])
        self.assertFalse(any(c['id'].endswith('capture_start') for d in result['records'] for c in d.get('contracts', [])))
        self.assertEqual(len(result['manifest']['expected_reference_heads']), 4)

    def test_missing_duplicate_digest_and_ambiguous_primary_remain_unresolved(self):
        for change, issue in [
            (lambda g: g['sources'][0]['files'][0].update(digest='sha256:'+'d'*64), 'missing_artifact'),
            (lambda g: g['sources'][0]['files'][0].update(digest='sha256:'+'a'*64), 'duplicate_content_membership'),
            (lambda g: g['sources'][1].update(primary=True), 'ambiguous_primary_source'),
        ]:
            doc = snapshot();change(doc['groups'][0])
            raw, source, reader = setup(doc)
            result = plan(raw, source, reader, OPERATOR)
            self.assertEqual(result['records'], [])
            self.assertIn(issue, result['report']['groups'][0]['issues'])

    def test_digest_ambiguity_is_not_first_export_wins(self):
        raw, source, reader = setup()
        extra = copy.deepcopy(reader.records[1]);extra['object_id'] = adapter.object_id(ARCHIVE_ID, 'test', 'duplicate')
        extra['revision_id'] = adapter.revision_of(extra)
        reader.heads[extra['object_id']] = extra
        self.assertIn('ambiguous_artifact_digest', plan(raw, source, reader, OPERATOR)['report']['groups'][0]['issues'])

    def test_named_native_and_backfilled_sessions_are_quiet_and_conflicts_refuse(self):
        raw, source, reader = setup()
        first = plan(raw, source, reader, OPERATOR)
        for d in first['records']:
            d = {**d, 'revision_id': adapter.revision_of(d)}
            reader.heads[d['object_id']] = d
            reader.by_revision[d['revision_id']] = d
        reader.heads[first['records'][0]['object_id']]['payload']['label'] = 'Named native session'
        self.assertEqual(plan(raw, source, reader, OPERATOR)['records'], [])
        parts = next(r for r in reader.heads.values() if any(c['id'] == PARTS[0] for c in r.get('contracts', [])))
        parts['refs'][1:] = list(reversed(parts['refs'][1:]))
        result = plan(raw, source, reader, OPERATOR)
        self.assertEqual(result['records'], [])
        self.assertEqual(result['report']['groups'][0]['issues'], ['conflicting_existing_grouping'])

    def test_existing_parts_with_different_revision_pins_require_review(self):
        raw, source, reader = setup()
        first = plan(raw, source, reader, OPERATOR)
        for draft in first['records']:
            record = {**draft, 'revision_id': adapter.revision_of(draft)}
            reader.heads[record['object_id']] = record
        parts = next(r for r in reader.heads.values() if any(c['id'] == PARTS[0] for c in r.get('contracts', [])))
        parts['refs'][1]['revision_id'] = 'sha256:'+'f'*64
        result = plan(raw, source, reader, OPERATOR)
        self.assertEqual(result['records'], [])
        self.assertIn('conflicting_existing_grouping', result['report']['groups'][0]['issues'])
        self.assertEqual(result['report']['existing_sessions'][0]['sources'][0][0]['revision_id'], 'sha256:'+'f'*64)

    def test_source_bytes_mismatch_and_duplicate_json_keys_refuse(self):
        raw, source, reader = setup()
        with self.assertRaises(BackfillError): plan(raw+b' ', source, reader, OPERATOR)
        d = reader.by_revision[source['revision_id']]
        bad = b'{"format":"x","format":"y"}'
        d['payload']['content'] = {'digest': adapter.digest_of(bad), 'size': len(bad)}
        with self.assertRaisesRegex(BackfillError, 'duplicate'): plan(bad, source, reader, OPERATOR)

    def test_membership_repeated_across_groups_and_primary_identity_conflict_refuse(self):
        document = snapshot()
        document['groups'].append(copy.deepcopy(document['groups'][0]))
        document['groups'][1]['id'] = 'other-group'
        raw, source, reader = setup(document)
        result = plan(raw, source, reader, OPERATOR)
        self.assertEqual(result['records'], [])
        self.assertEqual(result['report']['unresolved'], 2)
        raw, source, reader = setup()
        first = plan(raw, source, reader, OPERATOR)
        for draft in first['records']:
            record = {**draft, 'revision_id': adapter.revision_of(draft)}
            reader.heads[record['object_id']] = record
        # Same parts with a different session identity is conflicting evidence.
        entity = first['records'][0]
        different = adapter.object_id(ARCHIVE_ID, 'test', 'other-session')
        reader.heads[different] = {**entity, 'object_id': different, 'revision_id': 'sha256:'+'f'*64}
        for record in reader.heads.values():
            for ref in record.get('refs', []):
                if ref['role'] == 'session': ref['id'] = different
        result = plan(raw, source, reader, OPERATOR)
        self.assertEqual(result['records'], [])
        self.assertIn('conflicting_existing_grouping', result['report']['groups'][0]['issues'])

    def test_planning_is_deterministic_and_existing_output_directory_refuses(self):
        raw, source, reader = setup()
        one = plan(raw, source, reader, OPERATOR)
        self.assertEqual(one, plan(raw, source, reader, OPERATOR))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'proposal';write_plan(one, out)
            with self.assertRaises(FileExistsError): write_plan(one, out)


class DoorBackfill(unittest.TestCase):
    def test_real_door_exact_revisions_quiet_retry_and_provenance_target_refusal(self):
        from fixture.build import Builder
        from world.tests.test_conformance import integration_binaries, remove_tree
        door, archive = integration_binaries()
        tmp = Path(tempfile.mkdtemp(prefix='legacy-proof-')).resolve()
        self.addCleanup(remove_tree, tmp)
        content = tmp/'content';content.mkdir()
        files = []
        for i in range(2):
            data = f'synthetic part {i}'.encode();name = f'part-{i}.bin'
            (content/name).write_bytes(data)
            files.append({'path': name, 'digest': adapter.digest_of(data)})
        raw = adapter.canonical({'format': FORMAT, 'groups': [{'id': 'g1', 'label': 'Existing material',
                    'sources': [{'id': 'mix', 'primary': True, 'files': list(reversed(files))}]}]})
        (content/'snapshot.json').write_bytes(raw)
        fixture_plan = {'operator': 'legacy-operator', 'steps': [{'name': 'evidence', 'command': 'admit', 'records': [
            {'key': name, 'namespace': 'archive.core/artifact', 'name': name.replace('.', '-'),
             'shape': 'artifact', 'content': name} for name in ['part-0.bin', 'part-1.bin', 'snapshot.json']]}]}
        env = dict(os.environ)
        git = os.path.realpath(shutil.which('git'));annex = os.path.realpath(shutil.which('git-annex'))
        env.update(ARCHIVE_GIT_EXECUTABLE=git, ARCHIVE_GIT_ANNEX_EXECUTABLE=annex)
        root = tmp/'archive'
        builder = Builder(root, fixture_plan, content, door, archive, git, annex, env)
        builder.build()
        reader = adapter.ArchiveReader(root, archive, env)
        oid, rev = builder.heads['snapshot.json'];source = {'object_id': oid, 'revision_id': rev}
        result = plan(raw, source, reader, builder.operator_id)
        out = tmp/'proposal'
        from migration.legacy_sessions import main
        self.assertEqual(main([str(content/'snapshot.json'), '--archive', str(root),
                               '--archive-command', archive, '--source-object', oid,
                               '--source-revision', rev, '--operator', builder.operator_id,
                               '--output', str(out)]), 0)
        def admit(path, dry=False):
            return subprocess.run([door, 'admit', '--proposal', str(path)] + (['--dry-run'] if dry else []),
                                  cwd=root, env=env, capture_output=True, text=True)
        predicted = admit(out, True)
        self.assertEqual(predicted.returncode, 0, predicted.stderr)
        self.assertEqual(json.loads(predicted.stdout)['revision_ids'], result['revision_ids'])
        # A mapping cannot relabel an Artifact as a session or as parts.
        bad = copy.deepcopy(result)
        for ref in bad['records'][-1]['refs']:
            if ref['role'] == 'translated_session': ref.update(id=oid, revision_id=rev)
        bad_out=tmp/'bad';write_plan(bad,bad_out)
        self.assertNotEqual(admit(bad_out,True).returncode,0)
        # A later source head invalidates the earlier exact-reference plan.
        builder.step(2, {'name': 'source revision', 'command': 'admit', 'records': [
            {'key': 'snapshot-v2', 'revises': 'snapshot.json', 'shape': 'artifact', 'content': 'snapshot.json'}]})
        stale = admit(out, True)
        self.assertNotEqual(stale.returncode, 0)
        oid, rev = builder.heads['snapshot-v2']
        source = {'object_id': oid, 'revision_id': rev}
        reader = adapter.ArchiveReader(root, archive, env)
        result = plan(raw, source, reader, builder.operator_id)
        out = tmp/'fresh-proposal';write_plan(result, out)
        answer = admit(out)
        self.assertEqual(answer.returncode, 0, answer.stderr)
        self.assertEqual(json.loads(answer.stdout)['revision_ids'], result['revision_ids'])
        def head(): return subprocess.check_output([git,'rev-parse','HEAD'],cwd=root,text=True).strip()
        before = head();retry = admit(out)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(before,head())
        reader = adapter.ArchiveReader(root, archive, env)
        rerun = plan(raw,source,reader,builder.operator_id)
        self.assertEqual(rerun['records'],[])
        self.assertEqual(before,head())


if __name__ == '__main__': unittest.main()
