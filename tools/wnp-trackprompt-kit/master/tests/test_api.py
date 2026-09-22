import importlib.util
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path

class APITests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('wnp_host.api'),'API must be implemented')
        from wnp_host.store import Store
        from wnp_host.api import make_server
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=Store.initialize(Path(self.tmp.name),'demo','test-node')
        self.server=make_server(self.s,'a'*40,port=0)
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def request(self,path,token='a'*40,method='GET'):
        req=urllib.request.Request(self.base+path,method=method,headers={'Authorization':'Bearer '+token})
        return urllib.request.urlopen(req,timeout=3)
    def test_auth_required(self):
        with self.assertRaises(urllib.error.HTTPError) as c:self.request('/v1/status','wrong')
        self.assertEqual(c.exception.code,401)
    def test_status_and_sync(self):
        import json
        with self.request('/v1/status') as r:obj=json.load(r)
        self.assertEqual(obj['sequence'],0)
        with self.request('/v1/sync?stream='+obj['stream']+'&after=0') as r:page=json.load(r)
        self.assertEqual(page['snapshot']['sequence'],0)
    def test_post_forbidden(self):
        with self.assertRaises(urllib.error.HTTPError) as c:self.request('/v1/status',method='POST')
        self.assertEqual(c.exception.code,405)
    def test_arbitrary_files_not_served(self):
        for path in ['/v1/evidence/../node.json','/v1/evidence/%252e%252e/node.json','/.wnp/node.json']:
            with self.subTest(path=path),self.assertRaises(urllib.error.HTTPError):self.request(path)
    def test_duplicate_query_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as c:self.request('/v1/sync?after=0&after=1')
        self.assertEqual(c.exception.code,400)
    def test_public_bind_rejected(self):
        from wnp_host.api import make_server
        from wnp_host.model import WNPError
        with self.assertRaises(WNPError):make_server(self.s,'a'*40,host='0.0.0.0',port=0)
    def test_unknown_route_and_errors_are_sanitized(self):
        with self.assertRaises(urllib.error.HTTPError) as c:self.request('/missing')
        text=c.exception.read().decode()
        self.assertNotIn(self.tmp.name,text)
    def test_evidence_requires_registration(self):
        import json
        ref=self.s.register_evidence(b'proof')
        self.s.append('verification.passed',evidence=[ref])
        with self.request('/v1/evidence/'+ref['path']) as r:self.assertEqual(r.read(),b'proof')
