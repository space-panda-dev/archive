"""Retained dry-run/apply package for explicit legacy session snapshots.

The output is operational. Door alone admits the source evidence and grouping.
Dry-run never stages bytes, registers remotes, or changes the archive.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil

from enrich import adapter
from migration import legacy_sessions as sessions

BY = 'pkg:generic/archive-legacy-backfill@1'
NAMESPACE = 'archive.core/artifact'


def preflight(reader, operator):
    """Check installed published pins and the current initialized person."""
    catalog = json.loads((Path(__file__).resolve().parents[1] / 'spec/0.0.12/contracts.json').read_bytes())
    published = {(c['id'], c['version']): c['digest'] for c in catalog['contracts']}
    for contract in (sessions.CONTRACT, sessions.PARTS):
        if reader.installed.get(contract) != published[contract]:
            raise sessions.BackfillError('required 0.0.12 contract pins are absent; upgrade via FLEET before planning')
    person = reader.head(operator)
    appointed = [ref['id'] for r in reader.records
                 if reader.cites(r, ('archive.transition/operator_initialization', '1'))
                 for ref in r.get('refs', []) if ref['role'] == 'appoints']
    if (appointed != [operator] or person['shape'] != 'entity'
            or person['payload'].get('kind') != 'person'):
        raise sessions.BackfillError('operator must be the active initialized person Entity')


def build_plan(raw, reader, operator, staging):
    """Compose source and grouping as one atomic batch, keeping audio guards."""
    digest = adapter.digest_of(raw)
    matches = [r for r in reader.heads.values() if r['shape'] == 'artifact'
               and r['payload']['content'] == {'digest': digest, 'size': len(raw)}]
    if len(matches) > 1:
        raise sessions.BackfillError('source snapshot resolves to ambiguous current Artifacts')
    name = 'legacy-sessions-' + digest.removeprefix('sha256:')
    filename = name + '.json'
    source = None
    view = reader
    if matches:
        source_pin = sessions._pin(matches[0])
    else:
        oid = adapter.object_id(reader.archive_id, NAMESPACE, name)
        if oid in reader.heads:
            raise sessions.BackfillError('source identity already exists with different bytes; review source conflict')
        source = {'spec': reader.spec, 'object_id': oid, 'shape': 'artifact', 'by': BY,
                  'agent': operator, 'payload': {'content': {'digest': digest, 'size': len(raw)}},
                  'refs': [], 'supersedes': []}
        source_pin = {'object_id': oid, 'revision_id': adapter.revision_of(source)}
        view = copy.copy(reader)
        predicted = {**source, 'revision_id': source_pin['revision_id']}
        view.heads = {**reader.heads, oid: predicted}
        view.by_revision = {**reader.by_revision, source_pin['revision_id']: predicted}
    result = sessions.plan(raw, source_pin, view, operator)
    if source is not None and result['records']:
        manifest = result['manifest']
        result['records'].insert(0, source)
        result['revision_ids'].insert(0, source_pin['revision_id'])
        manifest['records'] = [f'records/{i:04d}.json' for i in range(len(result['records']))]
        manifest['identities'].insert(0, {'object_id': source_pin['object_id'], 'namespace': NAMESPACE, 'name': name})
        manifest['expected_heads'].insert(0, {'object_id': source_pin['object_id'], 'revision_id': None})
        manifest['expected_reference_heads'] = [p for p in manifest['expected_reference_heads']
                                                if p['object_id'] != source_pin['object_id']]
        manifest['contents'] = [{'digest': digest, 'size': len(raw),
                                 'annex_key': staging.key_for(digest, len(raw), filename),
                                 'staging_remote': staging.remote_name(reader.root, create=False)}]
    result['report']['source_status'] = ('reused' if source is None else
                                         'planned' if result['records'] else 'not_needed_no_group_changes')
    result['report']['content_availability'] = 'Artifact records resolved only; physical audio byte availability is unknown'
    result['report']['missing_artifact_meaning'] = 'No current Artifact record resolves the digest; this is not a physical byte check'
    return result, filename


def door_call(door, root, output, revisions, dry):
    args = [door, 'admit', '--proposal', str(output)] + (['--dry-run'] if dry else [])
    answer = json.loads(adapter._run(args, root, 'Door admission'))
    if (answer.get('status') != ('predicted' if dry else 'committed')
            or answer.get('revision_ids') != revisions
            or (dry and answer.get('content_verified') is not False)):
        raise sessions.BackfillError('Door did not return the exact retained planned revisions and expected verification status')
    return answer


def retained_digest(output, manifest):
    files = ['manifest.json', 'planned-revisions.json', 'inventory.json', *manifest['records']]
    return adapter.digest_of(adapter.canonical({p: adapter.digest_of((output / p).read_bytes()) for p in files}))


def _outside(path, root):
    return path != root and root not in path.parents


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--operator', required=True)
    parser.add_argument('--staging', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--archive-command', default='archive')
    parser.add_argument('--door', default='door')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='default: retain and validate a proposal without changing Archive')
    mode.add_argument('--apply', action='store_true', help='apply the identical previously retained dry-run proposal')
    args = parser.parse_args(argv)
    try:
        root, output, staging_path = args.archive.resolve(), args.output.resolve(), args.staging.resolve()
        snapshot = args.snapshot.resolve()
        if (not _outside(output, root) or not _outside(staging_path, root)
                or not _outside(output, staging_path) or not _outside(staging_path, output)
                or not _outside(snapshot, output) or not _outside(snapshot, staging_path)):
            raise sessions.BackfillError('output and staging must be separate directories outside Archive and the source snapshot')
        raw = snapshot.read_bytes()
        reader = adapter.ArchiveReader(root, args.archive_command)
        preflight(reader, args.operator)
        git = os.path.realpath(shutil.which('git') or 'git')
        annex = os.path.realpath(shutil.which('git-annex') or 'git-annex')
        staging = adapter.Staging(staging_path, git, annex)
        context = {'format': 'archive.legacy-backfill-package/1', 'archive_id': reader.archive_id,
                   'archive': str(root), 'operator': args.operator, 'snapshot_digest': adapter.digest_of(raw),
                   'snapshot_size': len(raw), 'staging': str(staging_path)}
        if args.apply:
            retained = json.loads((output / 'context.json').read_bytes())
            if any(retained.get(k) != v for k, v in context.items()):
                raise sessions.BackfillError('retained dry-run context changed; create and review a new output package')
            manifest = json.loads((output / 'manifest.json').read_bytes())
            if any(Path(p).is_absolute() or '..' in Path(p).parts for p in manifest['records']):
                raise sessions.BackfillError('retained record path escapes package')
            if retained_digest(output, manifest) != retained.get('proposal_digest'):
                raise sessions.BackfillError('retained proposal changed; create and review a new output package')
            revisions = json.loads((output / 'planned-revisions.json').read_bytes())
            report = json.loads((output / 'inventory.json').read_bytes())
            if revisions:
                predicted = door_call(args.door, root, output, revisions, True)
                if not predicted.get('retry') and manifest['contents']:
                    content, = manifest['contents']
                    if staging.remote_name(root, create=False) != content['staging_remote']:
                        raise sessions.BackfillError('staging route changed since retained dry-run')
                    filename = retained['filename']
                    key = staging.stage_file(snapshot, context['snapshot_digest'], len(raw), filename)
                    if key != content['annex_key']:
                        raise sessions.BackfillError('staged key differs from retained proposal')
                    staging.remote_name(root, create=True)
                answer = door_call(args.door, root, output, revisions, False)
            else:
                answer = {'status': 'unchanged', 'revision_ids': [], 'retry': False}
        else:
            result, filename = build_plan(raw, reader, args.operator, staging)
            sessions.write_plan(result, output)
            revisions, report = result['revision_ids'], result['report']
            answer = (door_call(args.door, root, output, revisions, True) if revisions
                      else {'status': 'unchanged', 'revision_ids': [], 'content_verified': False, 'retry': False})
            context.update(filename=filename, proposal_digest=retained_digest(output, result['manifest']))
            (output / 'context.json').write_bytes(adapter.canonical(context))
        print(json.dumps({**answer, 'records': len(revisions), 'unresolved': report['unresolved']}))
        return 2 if report['unresolved'] else 0
    except (sessions.BackfillError, adapter.AdapterError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
