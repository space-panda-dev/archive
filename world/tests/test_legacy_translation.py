"""World refuses malformed legacy provenance just as Door does."""
import copy
import unittest
from world.tests.plan0010 import drafted_export
from world.world import build_projection,ProjectionError


class LegacyProvenance(unittest.TestCase):
    def test_valid_mapping_projects_and_bad_targets_operator_and_payload_refuse(self):
        source=drafted_export()
        build_projection(source)
        for mutation in ['session','parts','operator','payload']:
            export=copy.deepcopy(source)
            mapping=next(r for r in export['records'] if any(c['id']=='archive.migration/session_translation' for c in r.get('contracts',[])))
            artifact=next(r for r in export['records'] if r['shape']=='artifact')
            if mutation in {'session','parts'}:
                for ref in mapping['refs']:
                    if ref['role']=='translated_'+mutation:
                        ref.update(id=artifact['object_id'],revision_id=artifact['revision_id'])
            elif mutation=='operator':
                guest=next(r for r in export['records'] if r['shape']=='entity' and r['payload'].get('kind')=='person' and r['object_id']!=mapping['agent'])
                mapping['agent']=guest['object_id']
            else:mapping['payload']['rule']='guessed-from-label'
            with self.subTest(mutation=mutation),self.assertRaises(ProjectionError):build_projection(export)
