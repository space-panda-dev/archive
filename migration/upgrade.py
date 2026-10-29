#!/usr/bin/env python3
"""Read-only upgrade planning and source-bound application through Door (ADR 0126)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from enrich import adapter

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = 'archive.transition/specification_upgrade'
BY = 'pkg:generic/archive-upgrade@1'
HEX = re.compile('[0-9a-f]{64}')
FIELDS = ('planDigest', 'sourceFingerprint', 'semanticConfigDigest', 'targetReleaseDigest', 'targetContracts', 'targetSpecification')
CHECKS = ('configuration', 'target-release', 'archive-export', 'operator', 'clean-worktree', 'installed-release', 'door-dry-run')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def hashed(value):
    return sha(adapter.canonical(value))


def read_json(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON key')
            value[key] = item
        return value
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def configuration(path):
    value = read_json(path)
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'archive', 'operator'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or not isinstance(value['archive'], str) or not Path(value['archive']).is_absolute()
            or not isinstance(value['operator'], str)
            or not re.fullmatch(r'urn:uuid:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value['operator'])):
        raise ValueError('config must contain only schemaVersion 1, an absolute archive path, and operator person id')
    return {**value, 'archive': str(Path(value['archive']).resolve(strict=True))}


def release(target):
    if target != '0.0.12':
        raise ValueError('this command supports only target specification 0.0.12')
    catalog = read_json(ROOT / 'spec' / target / 'contracts.json')
    if catalog['spec'] != 'archive/' + target:
        raise ValueError('packaged target catalog specification differs')
    pins = sorted([{'id': c['id'], 'version': c['version'], 'digest': c['digest'].removeprefix('sha256:')}
                   for c in catalog['contracts']], key=lambda c: (c['id'], c['version']))
    if not pins or len({(c['id'], c['version']) for c in pins}) != len(pins):
        raise ValueError('packaged target catalog is empty or duplicated')
    paths = [f'spec/{target}/{name}' for name in ('VERSION', 'archive-spec.md', 'context.jsonld', 'contracts.json')]
    for pin in pins:
        if not HEX.fullmatch(pin['digest']):
            raise ValueError('invalid packaged contract digest')
        path = f"spec/contracts/sha256-{pin['digest']}.json"
        raw = (ROOT / path).read_bytes()
        if sha(raw) != pin['digest']:
            raise ValueError('packaged contract bytes do not match the catalog')
        bundle = read_json(ROOT / path)
        if (bundle['id'], bundle['version']) != (pin['id'], pin['version']):
            raise ValueError('packaged contract identity does not match the catalog')
        paths.append(path)
    files = [{'path': path, 'sha256': sha((ROOT / path).read_bytes())} for path in sorted(set(paths))]
    return pins, hashed({'specification': 'archive/' + target, 'files': files})


class DoorStale(adapter.AdapterError):
    """Door's locked source/head guard refused before durable mutation."""


def run(command, root, door_refusal=False):
    result = subprocess.run(command, cwd=root, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'), capture_output=True, text=True)
    if result.returncode:
        if door_refusal:
            try:
                refusal = json.loads(result.stderr.strip().splitlines()[-1])
            except (ValueError, IndexError):
                refusal = {}
            if refusal.get('ok') is False and refusal.get('error', {}).get('code') == 'expected_head_stale':
                raise DoorStale(refusal['error'].get('message', 'Door source guard refused'))
        raise adapter.AdapterError(result.stderr.strip() or result.stdout.strip() or 'command failed')
    return result.stdout


def git(root, *args):
    return run([os.environ.get('ARCHIVE_GIT_EXECUTABLE') or shutil.which('git') or 'git', *args], root).strip()


def operator_pin(reader, operator):
    person = reader.head(operator)
    appointed = [r['id'] for record in reader.records
                 if reader.cites(record, ('archive.transition/operator_initialization', '1'))
                 for r in record.get('refs', []) if r['role'] == 'appoints']
    if appointed != [operator] or person['shape'] != 'entity' or person['payload'].get('kind') != 'person':
        raise ValueError('configured operator is not the active initialized person')
    return {'object_id': operator, 'revision_id': person['revision_id']}


def fingerprint(reader, head, current, person, semantic, release_digest, pins):
    return hashed({'archiveId': reader.archive_id, 'head': head, 'currentSpecification': current,
                   'operator': person, 'semanticConfigDigest': semantic,
                   'targetReleaseDigest': release_digest, 'targetContracts': pins})


def installed_release(reader, root, target, pins):
    expected = {(p['id'], p['version']): 'sha256:' + p['digest'] for p in pins}
    if reader.installed != expected:
        raise ValueError('installed target contract pins differ from packaged release')
    for name in ('VERSION', 'archive-spec.md', 'context.jsonld'):
        if (root / 'spec' / target / name).read_bytes() != (ROOT / 'spec' / target / name).read_bytes():
            raise ValueError('installed target specification bytes differ from packaged release')
    for pin in pins:
        if sha((root / 'contracts' / f"sha256-{pin['digest']}.json").read_bytes()) != pin['digest']:
            raise ValueError('installed contract bytes differ from packaged release')


def proposal(reader, target, person, source, pins, previous=None, source_head=None):
    name = 'upgrade-' + target.replace('.', '-') + '-' + source
    oid = adapter.object_id(reader.archive_id, CONTRACT, name)
    citation = next(p for p in pins if p['id'] == CONTRACT and p['version'] == '1')
    draft = {'spec': 'archive/' + target, 'object_id': oid, 'shape': 'claim', 'by': BY,
             'agent': person['object_id'], 'payload': {'from': previous or reader.spec, 'to': 'archive/' + target},
             'refs': [{'role': 'authorizer', 'id': person['object_id'], 'revision_id': person['revision_id']}],
             'supersedes': [], 'contracts': [{**citation, 'digest': 'sha256:' + citation['digest']}]}
    manifest = {'protocol': 'archive-transition/1', 'expected_snapshot': source_head or reader.snapshot, 'records': ['records/upgrade.json'],
                'identities': [{'object_id': oid, 'namespace': CONTRACT, 'name': name}],
                'expected_heads': [{'object_id': oid, 'revision_id': None}, person]}
    return manifest, draft


def door(door_command, root, manifest, draft, dry):
    base = Path(tempfile.gettempdir()).resolve()
    if base == root or root in base.parents:
        base = Path('/tmp').resolve()
    if base == root or root in base.parents:
        raise ValueError('no operational temporary directory outside Archive')
    with tempfile.TemporaryDirectory(prefix='archive-upgrade-', dir=base) as directory:
        path = Path(directory); (path / 'records').mkdir()
        (path / 'manifest.json').write_bytes(adapter.canonical(manifest))
        (path / 'records/upgrade.json').write_bytes(adapter.canonical(draft))
        value = json.loads(run([door_command, 'transition', '--proposal', str(path)] + (['--dry-run'] if dry else []), root, door_refusal=True))
    if value.get('status') != ('predicted' if dry else 'committed') or value.get('revision_ids') != [adapter.revision_of(draft)]:
        raise ValueError('Door did not return the exact predicted upgrade revision')
    return value


def plan_value(target, current, semantic, release_digest, pins, source, status, checks):
    value = {'schemaVersion': 1, 'kind': 'archive-upgrade-plan', 'sourceFingerprint': source,
             'semanticConfigDigest': semantic, 'targetReleaseDigest': release_digest, 'targetContracts': pins,
             'currentSpecification': current, 'targetSpecification': target, 'status': status, 'prerequisites': checks}
    return {**value, 'planDigest': hashed(value)}


def ready_checks(noop=False):
    return [{'name': name, 'status': 'ok', 'required': True, 'detail':
             'verified installed exact release' if noop and name == 'door-dry-run' else 'verified'} for name in CHECKS]


def inspect(config_path, target, archive_command, door_command):
    """Read evidence and ask Door; all temporary output is outside Archive."""
    checks = [{'name': name, 'status': 'skipped', 'required': True, 'detail': 'not checked'} for name in CHECKS]
    semantic, release_digest, source, current, pins = '0' * 64, '0' * 64, '0' * 64, '', []
    state = {}
    def check(name, action):
        row = checks[CHECKS.index(name)]
        try:
            value = action()
        except (OSError, ValueError, KeyError, TypeError, adapter.AdapterError) as error:
            row.update(status='missing', detail=str(error))
            raise
        row.update(status='ok', detail='verified')
        return value
    try:
        config = check('configuration', lambda: configuration(config_path))
        semantic = hashed(config)
        pins, release_digest = check('target-release', lambda: release(target))
        root = Path(config['archive'])
        reader = check('archive-export', lambda: adapter.ArchiveReader(root, archive_command))
        current = reader.spec.removeprefix('archive/')
        person = check('operator', lambda: operator_pin(reader, config['operator']))
        def clean():
            if git(root, 'status', '--porcelain', '--untracked-files=normal'):
                raise ValueError('archive worktree or index has uncommitted changes')
            head = git(root, 'rev-parse', 'HEAD')
            if reader.snapshot != head:
                raise ValueError('archive HEAD changed during export')
            return head
        head = check('clean-worktree', clean)
        source = fingerprint(reader, head, current, person, semantic, release_digest, pins)
        state = {'reader': reader, 'root': root, 'person': person, 'head': head, 'config': config}
        if current == target:
            check('installed-release', lambda: installed_release(reader, root, target, pins))
            checks[-1].update(status='ok', detail='verified installed exact release')
            status = 'noop'
        else:
            # Door owns additive release checks and checks the current installed state.
            checks[CHECKS.index('installed-release')].update(status='ok', detail='verified')
            manifest, draft = proposal(reader, target, person, source, pins)
            check('door-dry-run', lambda: door(door_command, root, manifest, draft, True))
            state.update(manifest=manifest, draft=draft)
            status = 'ready'
    except (OSError, ValueError, KeyError, TypeError, adapter.AdapterError):
        status = 'blocked'
    return plan_value(target, current, semantic, release_digest, pins, source, status, checks), state


def retry_original(plan, state, expected_source, expected_plan):
    """Recover only this exact source-bound transition from immutable history."""
    reader, root, person = state['reader'], state['root'], state['person']
    name = 'upgrade-' + plan['targetSpecification'].replace('.', '-') + '-' + expected_source
    oid = adapter.object_id(reader.archive_id, CONTRACT, name)
    record = reader.head(oid)
    previous = record['payload']['from']
    revision = record['revision_id'].removeprefix('sha256:')
    path = f'records/{revision[:2]}/{revision[2:4]}/sha256-{revision}.json'
    parents = git(root, 'log', '--diff-filter=A', '--format=%P', '--', path).splitlines()
    if len(parents) != 1 or len(parents[0].split()) != 1:
        raise ValueError('cannot identify the exact upgrade source commit')
    source = fingerprint(reader, parents[0], previous.removeprefix('archive/'), person,
                         plan['semanticConfigDigest'], plan['targetReleaseDigest'], plan['targetContracts'])
    if source != expected_source:
        raise ValueError('original upgrade source/configuration fingerprint differs')
    original = plan_value(plan['targetSpecification'], previous.removeprefix('archive/'),
                          plan['semanticConfigDigest'], plan['targetReleaseDigest'], plan['targetContracts'],
                          source, 'ready', ready_checks())
    if original['planDigest'] != expected_plan:
        raise ValueError('original upgrade plan digest differs')
    manifest, draft = proposal(reader, plan['targetSpecification'], person, source, plan['targetContracts'], previous, parents[0])
    if adapter.revision_of(draft) != record['revision_id']:
        raise ValueError('committed upgrade differs from original exact proposal')
    return original, manifest, draft


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'apply'])
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--json', action='store_true', required=True)
    parser.add_argument('--plan-digest')
    parser.add_argument('--source-fingerprint')
    args = parser.parse_args(argv)
    if args.action == 'apply' and not all(HEX.fullmatch(x or '') for x in (args.plan_digest, args.source_fingerprint)):
        parser.error('apply requires lowercase SHA256 --plan-digest and --source-fingerprint')
    archive_command = os.environ.get('ARCHIVE_UPGRADE_ARCHIVE', 'archive')
    door_command = os.environ.get('ARCHIVE_UPGRADE_DOOR', 'door')
    plan, state = inspect(args.config, args.target, archive_command, door_command)
    if args.action == 'plan':
        print(json.dumps(plan)); return 1 if plan['status'] == 'blocked' else 0
    receipt = {'schemaVersion': 1, 'kind': 'archive-upgrade-receipt', **{key: plan[key] for key in FIELDS},
               'planDigest': args.plan_digest, 'sourceFingerprint': args.source_fingerprint,
               'status': 'refused', 'durableMutation': 'none', 'detail': ''}
    entered_commit = False
    try:
        if plan['status'] == 'blocked':
            raise ValueError('upgrade prerequisites blocked: ' + '; '.join(p['detail'] for p in plan['prerequisites'] if p['status'] == 'missing'))
        retry = False
        if (plan['planDigest'], plan['sourceFingerprint']) != (args.plan_digest, args.source_fingerprint):
            if plan['status'] != 'noop':
                raise ValueError('stale plan or source fingerprint; replan before applying')
            original, manifest, draft = retry_original(plan, state, args.source_fingerprint, args.plan_digest)
            answer = door(door_command, state['root'], manifest, draft, True)
            if answer.get('retry') is not True:
                raise ValueError('Door did not recognize the exact original upgrade retry')
            retry = True
        elif plan['status'] == 'ready':
            manifest, draft = state['manifest'], state['draft']
        if plan['status'] == 'noop' and not retry:
            receipt.update(status='noop', detail='installed release already matches exact packaged target')
        else:
            # A final source check precedes Door's locked expected-head checks.
            if git(state['root'], 'rev-parse', 'HEAD') != state['head']:
                raise ValueError('archive HEAD changed after planning; refusing before mutation')
            if hashed(configuration(args.config)) != plan['semanticConfigDigest']:
                raise ValueError('semantic configuration changed after planning; refusing before mutation')
            entered_commit = True
            receipt['durableMutation'] = 'unknown'
            answer = door(door_command, state['root'], manifest, draft, False)
            receipt['durableMutation'] = 'none' if answer.get('retry') else 'committed'
            verified = adapter.ArchiveReader(state['root'], archive_command)
            if verified.spec != 'archive/' + args.target:
                raise ValueError('post-upgrade specification did not match target')
            installed_release(verified, state['root'], args.target, plan['targetContracts'])
            receipt.update(status='noop' if answer.get('retry') else 'completed', detail='exact Door transition and installed release verified')
    except DoorStale as error:
        receipt.update(status='refused', durableMutation='none', detail=str(error))
    except (OSError, ValueError, KeyError, TypeError, adapter.AdapterError) as error:
        receipt.update(status='failed' if entered_commit else 'refused', detail=str(error))
    print(json.dumps(receipt))
    return 0 if receipt['status'] in ('completed', 'noop') else 1


if __name__ == '__main__':
    raise SystemExit(main())
