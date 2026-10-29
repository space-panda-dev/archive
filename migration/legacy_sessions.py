"""Read-only legacy session inventory and Door proposal planner (ADR 0124).

The source is an admitted JSON snapshot with explicit groups and file order;
no recorder grammar, timestamps, directory order, or display-label parsing.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from enrich import adapter

FORMAT = 'archive.legacy-sessions/1'
CONTRACT = ('archive.migration/session_translation', '1')
PARTS = ('archive.claim/session_parts', '1')
BY = 'pkg:generic/archive-migration@0.1.0'
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


class BackfillError(ValueError):
    """Input cannot safely produce any proposal."""


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BackfillError('duplicate JSON key in source snapshot')
        result[key] = value
    return result


def _bad_constant(value):
    raise BackfillError('nonfinite JSON value in source snapshot')


def _pin(record):
    return {'object_id': record['object_id'], 'revision_id': record['revision_id']}


def _ref(role, pin):
    return {'role': role, 'id': pin['object_id'], 'revision_id': pin['revision_id']}


def plan(raw: bytes, source: dict, reader: adapter.ArchiveReader, operator: str) -> dict:
    """Resolve exact current Artifact pins, inventory conflicts, draft absent groups.

    ``source`` pins the admitted Artifact containing ``raw``. Unresolved groups
    have no drafts. All other groups form a single atomic proposal. An unchanged
    rerun emits no records, even after a translated session has been renamed.
    """
    source_record = reader.by_revision.get(source.get('revision_id'))
    if (not source_record or source_record['object_id'] != source.get('object_id')
            or source_record['shape'] != 'artifact'
            or source_record['payload']['content']['digest'] != adapter.digest_of(raw)
            or source_record['payload']['content']['size'] != len(raw)):
        raise BackfillError('source snapshot must pin the admitted Artifact with these exact bytes')
    if reader.heads.get(source['object_id']) != source_record:
        raise BackfillError('source snapshot is not its current head')
    person = reader.head(operator)
    if person['shape'] != 'entity' or person['payload'].get('kind') != 'person':
        raise BackfillError('operator must identify a person Entity; Door checks active authority')
    try:
        document = json.loads(raw, object_pairs_hook=_unique, parse_constant=_bad_constant)
    except BackfillError:
        raise
    except (ValueError, UnicodeError) as exc:
        raise BackfillError('source snapshot is not JSON') from exc
    if not isinstance(document, dict) or document.get('format') != FORMAT or not isinstance(document.get('groups'), list):
        raise BackfillError('unsupported source snapshot format')
    # Structural failures are distinct from per-group ambiguity. Refuse the
    # entire malformed document, rather than interpreting a partial snapshot.
    groups = document['groups']
    for group in groups:
        if (not isinstance(group, dict) or not isinstance(group.get('id'), str) or not group['id']
                or not isinstance(group.get('label'), str) or not group['label']
                or not isinstance(group.get('sources'), list) or not group['sources']):
            raise BackfillError('each group needs id, label and nonempty explicit sources')
        for stream in group['sources']:
            if (not isinstance(stream, dict) or not isinstance(stream.get('id'), str) or not stream['id']
                    or not isinstance(stream.get('primary'), bool)
                    or not isinstance(stream.get('files'), list) or not stream['files']):
                raise BackfillError('each source needs id, primary boolean and ordered files')
            for file in stream['files']:
                if (not isinstance(file, dict) or not isinstance(file.get('path'), str) or not file['path']
                        or not isinstance(file.get('digest'), str) or not DIGEST.fullmatch(file['digest'])):
                    raise BackfillError('each file needs a path and sha256 digest')
    counts = Counter(f['digest'] for g in groups for s in g['sources'] for f in s['files'])
    ids = Counter(g['id'] for g in groups)
    artifacts = defaultdict(list)
    existing = defaultdict(list)
    for record in reader.heads.values():
        if record['shape'] == 'artifact':
            artifacts[record['payload']['content']['digest']].append(record)
        if record['shape'] == 'claim' and reader.cites(record, PARTS):
            sessions = [r['id'] for r in record.get('refs', []) if r['role'] == 'session']
            if len(sessions) != 1:
                raise BackfillError('existing session_parts has no unique session')
            existing[sessions[0]].append([(r['id'], r['revision_id']) for r in record['refs'] if r['role'] == 'part'])
    drafts, identities, report = [], [], []
    guards = {source['object_id']: source['revision_id']}

    def add(namespace, name, shape, payload, refs, contracts=()):
        oid = adapter.object_id(reader.archive_id, namespace, name)
        if oid in reader.heads:
            raise BackfillError('allocated output identity already exists; review inventory conflict')
        draft = {'spec': reader.spec, 'object_id': oid, 'shape': shape, 'by': BY,
                 'agent': operator, 'payload': payload, 'refs': refs, 'supersedes': []}
        if contracts:
            draft['contracts'] = [reader.citation(c) for c in contracts]
        drafts.append(draft)
        identities.append({'object_id': oid, 'namespace': namespace, 'name': name})
        return {'object_id': oid, 'revision_id': adapter.revision_of(draft)}

    for index, group in enumerate(groups):
        issues = []
        streams = group['sources']
        files = [f for s in streams for f in s['files']]
        if ids[group['id']] != 1:
            issues.append('duplicate_group_id')
        if len({s['id'] for s in streams}) != len(streams):
            issues.append('duplicate_source_id')
        if sum(s['primary'] for s in streams) != 1:
            issues.append('ambiguous_primary_source')
        if len({f['path'] for f in files}) != len(files):
            issues.append('duplicate_file_path')
        if any(counts[f['digest']] != 1 for f in files):
            issues.append('duplicate_content_membership')
        if any(not artifacts[f['digest']] for f in files):
            issues.append('missing_artifact')
        if any(len(artifacts[f['digest']]) > 1 for f in files):
            issues.append('ambiguous_artifact_digest')
        row = {'id': group['id'], 'label': group['label'], 'pointer': f'/groups/{index}',
               'sources': streams, 'issues': issues}
        report.append(row)
        if issues:
            row['status'] = 'unresolved'
            continue
        pins = {f['digest']: _pin(artifacts[f['digest']][0]) for f in files}
        ordered = [[(pins[f['digest']]['object_id'], pins[f['digest']]['revision_id'])
                    for f in s['files']] for s in streams]
        members = {item[0] for stream in ordered for item in stream}
        primary = next(s for s in streams if s['primary'])
        name = 'session-' + primary['files'][0]['digest'].removeprefix('sha256:')
        session_id = adapter.object_id(reader.archive_id, 'archive.audio/session', name)
        overlaps = {session: parts for session, parts in existing.items()
                    if members.intersection(item[0] for stream in parts for item in stream)}
        row['sources'] = [{'id': s['id'], 'primary': s['primary'],
                           'files': [{**f, **pins[f['digest']]} for f in s['files']]} for s in streams]
        if overlaps:
            if (len(overlaps) == 1 and session_id in overlaps
                    and sorted(next(iter(overlaps.values()))) == sorted(ordered)):
                row.update(status='already_grouped', session=next(iter(overlaps)))
            else:
                row.update(status='unresolved', issues=['conflicting_existing_grouping'])
            continue
        if session_id in reader.heads:
            row.update(status='unresolved', issues=['existing_session_without_matching_parts'])
            continue
        entity = add('archive.audio/session', name, 'entity',
                     {'kind': 'recording_session', 'label': group['label']}, [])
        part_pins = []
        for stream in streams:
            token = adapter.digest_of(stream['id'].encode()).removeprefix('sha256:')
            part_pins.append(add(PARTS[0], name + '-legacy-' + token, 'claim', {},
                                 [_ref('session', entity)] + [_ref('part', pins[f['digest']]) for f in stream['files']],
                                 [PARTS]))
        mapping_name = adapter.digest_of(adapter.canonical([source, row['pointer']])).removeprefix('sha256:')
        translation = add(CONTRACT[0], 'legacy-' + mapping_name, 'claim',
                          {'rule': 'legacy-session-grouping/1', 'source_pointer': row['pointer'],
                           'source_id': group['id'], 'primary_source': primary['id'],
                           'sources': [{'id': s['id'], 'parts_revision': p['revision_id']} for s, p in zip(streams, part_pins)]},
                          [_ref('migration_source', source), _ref('translated_session', entity)]
                          + [_ref('translated_parts', p) for p in part_pins], [CONTRACT])
        guards.update({p['object_id']: p['revision_id'] for p in pins.values()})
        row.update(status='planned', session=entity, parts=part_pins, translation=translation)
    manifest = {'protocol': 'archive-admission/1', 'records': [f'records/{i:04d}.json' for i in range(len(drafts))],
                'identities': identities, 'contents': [],
                'expected_heads': [{'object_id': d['object_id'], 'revision_id': None} for d in drafts],
                'expected_reference_heads': [{'object_id': oid, 'revision_id': rev} for oid, rev in sorted(guards.items())]}
    return {'manifest': manifest, 'records': drafts, 'revision_ids': [adapter.revision_of(d) for d in drafts],
            'report': {'format': 'archive.legacy-session-inventory/1', 'source': source, 'groups': report,
                       'unresolved': sum(r['status'] == 'unresolved' for r in report),
                       'existing_sessions': [{'session': _pin(reader.head(session)),
                                              'sources': [[{'object_id': oid, 'revision_id': rev}
                                                           for oid, rev in stream] for stream in parts]}
                                             for session, parts in sorted(existing.items())]}}


def write_plan(result: dict, directory: Path):
    """Write a new operational proposal directory; never admit or mutate."""
    directory.mkdir(exist_ok=False)
    (directory / 'records').mkdir()
    for name, record in zip(result['manifest']['records'], result['records']):
        (directory / name).write_bytes(adapter.canonical(record))
    for name, value in [('manifest.json', result['manifest']), ('planned-revisions.json', result['revision_ids']),
                        ('inventory.json', result['report'])]:
        (directory / name).write_bytes(adapter.canonical(value))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--archive-command', default='archive')
    parser.add_argument('--source-object', required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--operator', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        reader = adapter.ArchiveReader(args.archive, args.archive_command)
        result = plan(args.snapshot.read_bytes(), {'object_id': args.source_object, 'revision_id': args.source_revision},
                      reader, args.operator)
        write_plan(result, args.output)
    except (BackfillError, adapter.AdapterError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps({'records': len(result['records']), 'unresolved': result['report']['unresolved']}))
    return 2 if result['report']['unresolved'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
