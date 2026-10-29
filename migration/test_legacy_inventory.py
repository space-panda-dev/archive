import base64
import json
import tempfile
import unittest
from pathlib import Path
from migration.legacy_inventory import extract,scan


def rows(records):
    return [({'path':f'{i}.json','offset':0,'length':len(raw),'sha256':'sha256:'+str(i)*64},raw)
            for i,r in enumerate(records) for raw in [json.dumps(r).encode()]]


def evidence():
    records=[]
    for name,hexval in [('z','b'),('a','a')]:
        records += [{'schema':'artifact','artifact_id':name,'source_record_id':'s-'+name,'content_hash_sha256':hexval*64},
                    {'schema':'source_observation','id':'o-'+name,'source_record_id':'s-'+name,'relative_path':name+'.wav'}]
    records.append({'schema':'session','schema_version':'1','id':'g','session_id':'g','title':'Explicit label',
                    'source_artifact_ids':['z','a'],'first_content_hash_sha256':'b'*64})
    return records


class ExtractLegacy(unittest.TestCase):
    def test_explicit_order_and_exact_source_bytes_survive(self):
        source=rows(evidence());snapshot,inventory=extract(source)
        self.assertEqual(inventory['groups_extracted'],1)
        [group]=snapshot['groups']
        self.assertEqual([f['path'] for f in group['sources'][0]['files']],['z.wav','a.wav'])
        self.assertEqual(base64.b64decode(group['legacy_records'][0]['bytes_base64']),source[-1][1])
        self.assertEqual(group['legacy_records'][0]['locator'],source[-1][0])

    def test_no_session_rows_and_cached_guesses_stay_blocked(self):
        records=evidence()[:-1]+[{'schema':'session_identity_sidecar','parts':[{'artifact_id':'z'}],'title':'Guess'}]
        snapshot,inventory=extract(rows(records))
        self.assertEqual(snapshot['groups'],[])
        self.assertEqual(inventory['unresolved'][0]['issues'],['no_explicit_session_records'])
        self.assertEqual(inventory['artifacts_without_grouping'],['a','z'])
        self.assertEqual(inventory['ignored_derived_sidecars'],1)

    def test_missing_content_conflicting_anchor_and_revision_history_require_review(self):
        for change in ['missing','anchor','history']:
            records=evidence()
            if change=='missing':records[0].pop('content_hash_sha256')
            if change=='anchor':records[-1]['first_content_hash_sha256']='c'*64
            if change=='history':records.append({'schema':'record_revision','subject_schema':'session','subject':{'id':'g'}})
            snapshot,inventory=extract(rows(records))
            self.assertEqual(snapshot['groups'],[])
            self.assertTrue(inventory['unresolved'])

    def test_persisted_sidecar_membership_and_derived_provenance_survive(self):
        records=evidence()[:-1]
        parts=[{'artifact_id':'z','part_index':0},{'artifact_id':'a','part_index':1}]
        for index,member,digest in [(0,'z','b'*64),(1,'a','a'*64)]:
            records.append({'schema':'session_identity_sidecar','schema_version':'1','artifact_id':member,
                            'part_count':2,'part_index':index,'parts':parts,'title':'Derived title',
                            'tool_name':'legacy-reader','tool_version':'1', 'source_content_hash_sha256':digest})
        snapshot,inventory=extract(rows(records),include_sidecars=True)
        self.assertEqual(inventory['groups_extracted'],1)
        [group]=snapshot['groups']
        self.assertEqual(group['legacy_provenance'],'persisted_derived_sidecar')
        self.assertEqual([f['path'] for f in group['sources'][0]['files']],['z.wav','a.wav'])
        self.assertEqual(len([e for e in group['legacy_records'] if b'session_identity_sidecar' in base64.b64decode(e['bytes_base64'])]),2)
        records[-1]['parts']=list(reversed(parts))
        snapshot,inventory=extract(rows(records),include_sidecars=True)
        self.assertTrue(inventory['unresolved'])

    def test_overlapping_persisted_groups_keep_all_evidence_for_review(self):
        records=evidence()
        records.append({'schema':'session','schema_version':'1','id':'g2','session_id':'g2',
                        'title':'Another grouping','source_artifact_ids':['a'],
                        'first_content_hash_sha256':'a'*64})
        snapshot,inventory=extract(rows(records))
        self.assertEqual(snapshot['groups'],[])
        self.assertEqual(len(snapshot['unresolved_groups']),2)
        self.assertEqual(inventory['artifacts_with_grouping'],[])
        self.assertEqual(inventory['artifacts_without_grouping'],['a','z'])
        self.assertEqual(inventory['unresolved'][0]['conflicts_with'],['g2'])

    def test_scanner_reads_jsonl_offsets_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=b''.join(json.dumps(x).encode()+b'\n' for x in evidence())
            (root/'records.jsonl').write_bytes(raw)
            snapshot,inventory=scan(root)
            [group]=snapshot['groups'];locator=group['legacy_records'][0]['locator']
            self.assertGreater(locator['offset'],0)
            self.assertEqual(base64.b64decode(group['legacy_records'][0]['bytes_base64']),raw[locator['offset']:locator['offset']+locator['length']])
            (root/'link').symlink_to(root/'records.jsonl')
            with self.assertRaises(ValueError):scan(root)


if __name__=='__main__':unittest.main()
