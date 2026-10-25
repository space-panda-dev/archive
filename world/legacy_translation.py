"""World re-check of ADR 0121's source-to-session provenance contract."""
from __future__ import annotations
import re

CONTRACT = ('archive.migration/session_translation','1')
REVISION = re.compile(r'sha256:[0-9a-f]{64}')


def check(record, records, operator):
    """Keep the bundle's closed payload, operator and exact target meanings."""
    if operator is None or record.get('agent') != operator:
        raise ValueError('a legacy session translation requires the active operator')
    payload=record.get('payload')
    required={'rule','source_pointer','source_id','primary_source','sources'}
    def text(value):return isinstance(value,str) and 1<=len(value)<=1000
    if (not isinstance(payload,dict) or set(payload)!=required
            or payload['rule']!='legacy-session-grouping/1'
            or not isinstance(payload['source_pointer'],str)
            or re.fullmatch(r'/groups/[0-9]+',payload['source_pointer']) is None
            or not text(payload['source_id']) or not text(payload['primary_source'])
            or not isinstance(payload['sources'],list) or not payload['sources']):
        raise ValueError('invalid legacy session translation payload')
    for source in payload['sources']:
        if (not isinstance(source,dict) or set(source)!={'id','parts_revision'} or not text(source['id'])
                or not isinstance(source['parts_revision'],str) or REVISION.fullmatch(source['parts_revision']) is None):
            raise ValueError('invalid legacy session source mapping')
    roles={'migration_source':[],'translated_session':[],'translated_parts':[]}
    refs=record.get('refs')
    if not isinstance(refs,list):raise ValueError('legacy session translation needs pinned references')
    for ref in refs:
        if not isinstance(ref,dict) or set(ref)!={'role','id','revision_id'} or ref.get('role') not in roles:
            raise ValueError('invalid legacy session translation reference')
        target=records.get(ref['revision_id'])
        if target is None or target.get('object_id')!=ref['id']:
            raise ValueError('legacy session translation target revision is absent or belongs to another object')
        roles[ref['role']].append(target)
    if len(roles['migration_source'])!=1 or len(roles['translated_session'])!=1 or not roles['translated_parts']:
        raise ValueError('legacy session translation needs one source, one session and translated parts')
    if roles['migration_source'][0].get('shape')!='artifact':
        raise ValueError('legacy session migration_source must pin an Artifact')
    session=roles['translated_session'][0]
    if session.get('shape')!='entity' or session.get('payload',{}).get('kind')!='recording_session':
        raise ValueError('legacy translated_session must pin a recording_session Entity')
    for parts in roles['translated_parts']:
        if parts.get('shape')!='claim' or not any(c.get('id')=='archive.claim/session_parts' and c.get('version')=='1'
                                               for c in parts.get('contracts',[])):
            raise ValueError('legacy translated_parts must pin a session_parts version 1 Claim')
