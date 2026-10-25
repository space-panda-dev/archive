"""Extract persisted V2 grouping from retained records and optional UI sidecars.

Preserve source_artifact_ids order; never call a recorder grammar or use
timestamps, labels, filename sorting or calendar guesses. Explicit sidecar
parts are translated verbatim only when requested; their derived origin stays.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import stat
from collections import defaultdict
from pathlib import Path

from enrich import adapter
from migration.census import _load_json, _read_manifest_bound
from migration.source_manifest import Limits, open_root_without_symlinks, source_manifest, manifest_fingerprint
FORMAT = 'archive.legacy-sessions/1'


class BackfillError(ValueError):
    """The source cannot safely supply an explicit grouping."""


def extract(rows, *, include_sidecars=False):
    """Translate exact (locator, bytes) rows, retaining the evidence in snapshot.

    Revision histories are explicitly unsupported rather than resolved by file
    order. The caller can review them separately; their affected groups remain
    blocked. Unknown source schemas do not become inferred sessions.
    """
    by_schema = defaultdict(list)
    for locator, raw in rows:
        try:
            record = _load_json(raw.decode('utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise BackfillError('invalid or ambiguous source JSON at '+locator['path']) from exc
        if not isinstance(record, dict):
            raise BackfillError('source record is not an object at '+locator['path'])
        evidence = {'locator': locator, 'bytes_base64': base64.b64encode(raw).decode('ascii')}
        by_schema[record.get('schema')].append((record, evidence))
    artifacts, observations = defaultdict(list), defaultdict(list)
    histories = set()
    for record, evidence in by_schema['artifact']:
        artifacts[record.get('artifact_id') or record.get('id')].append((record, evidence))
    for record, evidence in by_schema['source_observation']:
        observations[record.get('source_record_id')].append((record, evidence))
    for record, evidence in by_schema['record_revision']:
        if record.get('subject_schema') in {'session', 'artifact', 'source_observation'}:
            subject = record.get('subject')
            if not isinstance(subject, dict) or not isinstance(subject.get('id'), str):
                raise BackfillError('malformed source revision subject')
            histories.add((record['subject_schema'], subject['id']))
    groups, unresolved, grouped = [], [], set()
    sessions = list(by_schema['session'])
    sidecar_groups = defaultdict(list)
    if include_sidecars:
        for record, evidence in by_schema['session_identity_sidecar']:
            parts = record.get('parts')
            if (record.get('schema_version') != '1' or not isinstance(parts, list) or not parts
                    or type(record.get('part_count')) is not int or record['part_count'] != len(parts)
                    or not isinstance(record.get('title'),str) or not record['title']
                    or any(not isinstance(p, dict) or not isinstance(p.get('artifact_id'), str) for p in parts)
                    or [p.get('part_index') for p in parts] != list(range(len(parts)))):
                unresolved.append({'locator':evidence['locator'], 'issues':['invalid_persisted_sidecar_order']})
                continue
            members = tuple(p['artifact_id'] for p in parts)
            sidecar_groups[members].append((record,evidence))
        for members, copies in sidecar_groups.items():
            issues=[]
            if len(set(members)) != len(members):issues.append('duplicate_sidecar_members')
            if len({r.get('title') for r,_ in copies}) != 1:issues.append('conflicting_sidecar_titles')
            for r,e in copies:
                own=r.get('artifact_id')
                if own not in members or r.get('part_index') != members.index(own):
                    issues.append('sidecar_subject_disagrees_with_parts')
                if len(artifacts[own]) != 1 or r.get('source_content_hash_sha256') != artifacts[own][0][0].get('content_hash_sha256'):
                    issues.append('sidecar_content_digest_mismatch')
                if not isinstance(r.get('tool_name'), str) or not isinstance(r.get('tool_version'), str):
                    issues.append('missing_sidecar_algorithm_provenance')
            if issues:
                unresolved.append({'locator':copies[0][1]['locator'],'issues':sorted(set(issues))});continue
            equivalent=[(r,e) for r,e in sessions if isinstance(r.get('source_artifact_ids'),list)
                        and tuple(r['source_artifact_ids']) == members]
            if len(equivalent)==1:
                equivalent[0][0].setdefault('_sidecars',[]).extend(e for _,e in copies)
                continue
            first=artifacts[members[0]][0][0] if len(artifacts[members[0]])==1 else {}
            sid='sidecar-'+adapter.digest_of(adapter.canonical(list(members))).removeprefix('sha256:')
            record={'schema':'session','schema_version':'1','session_id':sid,'title':copies[0][0].get('title'),
                    'source_artifact_ids':list(members),'first_content_hash_sha256':first.get('content_hash_sha256'),
                    '_sidecars':[e for _,e in copies], '_derived':True}
            sessions.append((record,copies[0][1]))
    ids = [r.get('session_id') or r.get('id') for r, _ in sessions]
    for record, evidence in sessions:
        sid = record.get('session_id') or record.get('id')
        reasons, files, records = [], [], [evidence]
        records.extend(e for e in record.get('_sidecars',[]) if e not in records)
        members = record.get('source_artifact_ids')
        if not isinstance(sid, str) or not sid or ids.count(sid) != 1:
            reasons.append('missing_or_duplicate_session_id')
        if ('session', sid) in histories:
            reasons.append('session_revision_history_requires_review')
        if record.get('schema_version') != '1':
            reasons.append('unsupported_session_version')
        if not isinstance(members, list) or not members or any(not isinstance(x, str) or not x for x in members):
            reasons.append('missing_explicit_ordered_members');members=[]
        if len(set(members)) != len(members):
            reasons.append('duplicate_session_members')
        if not isinstance(record.get('title'), str) or not record['title']:
            reasons.append('missing_asserted_title')
        for member in members:
            if len(artifacts[member]) != 1:
                reasons.append('missing_or_ambiguous_artifact');continue
            artifact, artifact_evidence = artifacts[member][0]
            digest = artifact.get('content_hash_sha256')
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(c not in '0123456789abcdef' for c in digest)):
                reasons.append('missing_or_invalid_content_digest');continue
            if ('artifact', member) in histories:
                reasons.append('artifact_revision_history_requires_review')
            source_id = artifact.get('source_record_id')
            if len(observations[source_id]) != 1:
                reasons.append('missing_or_ambiguous_source_observation');continue
            observation, observation_evidence = observations[source_id][0]
            path = observation.get('relative_path')
            if not isinstance(path, str) or not path:
                reasons.append('missing_original_relative_path');continue
            observation_id = observation.get('id')
            if ('source_observation', observation_id) in histories:
                reasons.append('source_observation_revision_history_requires_review')
            files.append({'path': path, 'digest': 'sha256:'+digest})
            records.extend([artifact_evidence, observation_evidence])
        if files and record.get('first_content_hash_sha256') != files[0]['digest'].removeprefix('sha256:'):
            reasons.append('first_content_digest_mismatch')
        if reasons:
            unresolved.append({'id':sid,'locator':evidence['locator'],'issues':sorted(set(reasons))});continue
        groups.append({'id':sid,'label':record['title'], 'sources':[{'id':'legacy','primary':True,'files':files}],
                       'legacy_records':records, 'legacy_provenance':
                       'persisted_derived_sidecar' if record.get('_derived') else 'explicit_session_record'})
        grouped.update(members)
    if not sessions:
        unresolved.append({'issues':['no_explicit_session_records']})
    for kind, sid in sorted(histories, key=str):
        if kind == 'session' and sid not in ids:
            unresolved.append({'id':sid,'issues':['session_revision_history_requires_review']})
    # Conflicting persisted memberships never select one source over another.
    memberships=defaultdict(list)
    for group in groups:
        for file in group['sources'][0]['files']:memberships[file['digest']].append(group['id'])
    conflicting={gid for ids in memberships.values() if len(ids)>1 for gid in ids}
    blocked_groups=[g for g in groups if g['id'] in conflicting]
    for group in blocked_groups:
        digests=[f['digest'] for f in group['sources'][0]['files']]
        unresolved.append({'id':group['id'],'issues':['conflicting_persisted_grouping'],
                           'sidecar_locators':[e['locator'] for e in group['legacy_records']
                                               if e['locator'].get('root')=='sidecars'],
                           'member_artifact_ids':next((r['source_artifact_ids'] for r,_ in sessions
                                                      if (r.get('session_id') or r.get('id'))==group['id']),[]),
                           'conflicts_with':sorted({gid for digest in digests for gid in memberships[digest]
                                                    if gid!=group['id']}),
                           'overlap_digests':[d for d in digests if len(memberships[d])>1]})
    groups=[g for g in groups if g['id'] not in conflicting]
    grouped={r for g in groups for r in next((x.get('source_artifact_ids',[]) for x,_ in sessions if (x.get('session_id') or x.get('id'))==g['id']),[])}
    snapshot = {'format':FORMAT,'groups':groups,'unresolved_groups':blocked_groups}
    inventory = {'format':'archive.legacy-source-inventory/1','session_records':len(by_schema['session']), 'persisted_sidecar_groups':len(sidecar_groups),
                 'artifact_records':len(by_schema['artifact']), 'groups_extracted':len(groups),
                 'unresolved':unresolved,
                 'artifacts_without_grouping':sorted(str(x) for x in artifacts if x not in grouped),
                 'artifacts_with_grouping':sorted(grouped),
                 'sidecar_records':len(by_schema['session_identity_sidecar']),
                 'ignored_derived_sidecars':0 if include_sidecars else len(by_schema['session_identity_sidecar'])}
    uncovered=set(inventory['artifacts_without_grouping'])
    declared={str(aid):rows[0][0].get('media_type') if len(rows)==1 else None for aid,rows in artifacts.items()}
    inventory['uncovered_audio_artifacts']=sorted(aid for aid in uncovered if isinstance(declared.get(aid),str) and declared[aid].lower().startswith('audio/'))
    inventory['uncovered_declared_non_audio_artifacts']=sorted(aid for aid in uncovered if isinstance(declared.get(aid),str) and '/' in declared[aid] and not declared[aid].lower().startswith('audio/'))
    inventory['uncovered_unknown_media_artifacts']=sorted(uncovered-set(inventory['uncovered_audio_artifacts'])-set(inventory['uncovered_declared_non_audio_artifacts']))
    return snapshot, inventory


def sidecar_rows(root: Path):
    """Read only persisted JSON sidecars; never hash or copy audio caches."""
    paths=sorted(p.relative_to(root).as_posix() for p in root.glob('*/*.session.json'))
    fd,_=open_root_without_symlinks(root)
    rows=[]
    try:
        for relative in paths:
            parent,name=relative.split('/')
            directory=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            try:
                file=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
                try:
                    before=os.fstat(file)
                    if not stat.S_ISREG(before.st_mode) or before.st_size>128*1024*1024:
                        raise BackfillError('sidecar is not a bounded regular file')
                    chunks=[]
                    while chunk:=os.read(file,1024*1024):
                        chunks.append(chunk)
                        if sum(map(len,chunks))>128*1024*1024:raise BackfillError('sidecar size limit')
                    raw=b''.join(chunks);after=os.fstat(file)
                    if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                        raise BackfillError('sidecar changed during read')
                finally:os.close(file)
            finally:os.close(directory)
            rows.append(({'path':relative,'root':'sidecars','offset':0,'length':len(raw),'sha256':adapter.digest_of(raw)},raw))
        if paths!=sorted(p.relative_to(root).as_posix() for p in root.glob('*/*.session.json')):
            raise BackfillError('sidecar set changed during read')
    finally:os.close(fd)
    return rows


def scan(root: Path, sidecars: Path | None = None):
    manifest = source_manifest(root, Limits(max_file_bytes=128*1024*1024))
    if manifest['status'] != 'complete':
        raise BackfillError('source manifest incomplete: '+str(manifest.get('issues')))
    fd, _ = open_root_without_symlinks(root)
    rows=[]
    try:
        for entry in manifest['files']:
            path=entry['path']
            if not path.endswith(('.json','.jsonl')):continue
            raw = _read_manifest_bound(fd,path,entry)
            chunks=raw.splitlines(keepends=True) if path.endswith('.jsonl') else [raw]
            offset=0
            for chunk in chunks:
                if chunk.strip():
                    rows.append(({'path':path,'offset':offset,'length':len(chunk),'sha256':adapter.digest_of(chunk)},chunk))
                offset+=len(chunk)
    finally:
        os.close(fd)
    if sidecars is not None:rows.extend(sidecar_rows(sidecars))
    snapshot, inventory=extract(rows,include_sidecars=sidecars is not None)
    snapshot['source_manifest']=manifest
    snapshot['sidecar_capture']='per-file-stable-non-atomic' if sidecars is not None else 'not-read'
    inventory['source_manifest_fingerprint']=manifest_fingerprint(manifest)
    return snapshot,inventory


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('records',type=Path,help='retained frozen disposable V2 records tree')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sidecars',type=Path,help='persisted legacy UI session sidecars; preserve derived provenance')
    args=parser.parse_args(argv)
    try:
        snapshot,inventory=scan(args.records,args.sidecars)
        args.output.mkdir(exist_ok=False)
        (args.output/'snapshot.json').write_bytes(adapter.canonical(snapshot))
        (args.output/'inventory.json').write_bytes(adapter.canonical(inventory))
    except (BackfillError,OSError,ValueError) as exc:
        parser.exit(1,str(exc)+'\n')
    print(json.dumps({k:inventory[k] for k in ['session_records','artifact_records','groups_extracted']}))
    return 2 if inventory['unresolved'] or inventory['artifacts_without_grouping'] else 0


if __name__=='__main__':raise SystemExit(main())
