import json,tempfile,unittest
from pathlib import Path
from wnp_host.store import Store
try:import jsonschema
except ImportError:jsonschema=None
class SchemaTests(unittest.TestCase):
    @unittest.skipIf(jsonschema is None,'Optional jsonschema not installed; host internal validators still run')
    def test_real_generated_documents_match_published_schemas(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as t:
            s=Store.initialize(Path(t),'example','node');ref=s.register_evidence(b'sanitized')
            e=s.append('state.changed',patch={'state':'waiting'},evidence=[ref]);s.observe({'worker':{'state':'stale'}})
            objects={'project':s.project(),'node':s.node(),'event':e,'state':s.snapshot(),'evidence':ref,'sync':s.sync(s.node()['stream'],0),'error':{'protocol':'wnp/1','error':{'code':'not_found','message':'Not found','retryable':False}}}
            for name,obj in objects.items():
                with self.subTest(document=name):
                    schema=json.loads((root/'schemas'/f'{name}.schema.json').read_text())
                    jsonschema.Draft202012Validator.check_schema(schema)
                    jsonschema.Draft202012Validator(schema,format_checker=jsonschema.FormatChecker()).validate(obj)
