import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('wnp_host.store'), 'WNP core must be implemented')
        from wnp_host.store import Store
        self.Store = Store
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store.initialize(self.root, 'example', 'node-one')

    def test_init_and_restart_keep_stream(self):
        n = self.store.node()
        self.assertEqual(self.store.snapshot()['sequence'], 0)
        self.assertEqual(self.Store(self.root).node()['stream'], n['stream'])

    def test_events_are_contiguous_and_snapshot_replayable(self):
        self.store.append('run.started', patch={'state': 'running', 'run':'r1'})
        self.store.append('task.submitted', patch={'state':'waiting','task':'shot-002'})
        self.assertEqual([e['sequence'] for e in self.store.events()], [1,2])
        self.assertEqual(self.store.snapshot()['task'], 'shot-002')
        (self.root/'.wnp/state.json').unlink()
        self.store.recover()
        self.assertEqual(self.store.snapshot()['sequence'],2)
        self.assertEqual(self.store.snapshot()['task'],'shot-002')

    def test_idempotency_and_conflict(self):
        from wnp_host.model import WNPError
        a=self.store.append('run.started', patch={'state':'running'}, key='producer:1')
        b=self.store.append('run.started', patch={'state':'running'}, key='producer:1')
        self.assertEqual(a,b)
        with self.assertRaises(WNPError):
            self.store.append('run.started', patch={'state':'failed'}, key='producer:1')
        self.assertEqual(len(self.store.events()),1)

    def test_metrics_do_not_change_journal_cursor(self):
        self.store.observe({'metrics':{'gpu_utilization_percent':17}})
        self.store.observe({'metrics':{'gpu_utilization_percent':63}})
        self.assertEqual(self.store.snapshot()['sequence'],0)
        self.assertEqual(self.store.events(),[])
        self.assertEqual(self.store.snapshot()['observations']['metrics']['gpu_utilization_percent'],63)

    def test_sync_fixed_watermark(self):
        for i in range(5): self.store.append('test.event', data={'i':i})
        stream=self.store.node()['stream']
        page=self.store.sync(stream,0,limit=2)
        self.store.append('test.event',data={'i':5})
        p2=self.store.sync(stream,page['next_after'],limit=9,until=page['head'])
        self.assertEqual(p2['head'],5)
        self.assertEqual(p2['next_after'],5)
        self.assertFalse(p2['more'])
        self.assertEqual(p2['snapshot']['sequence'],5)

    def test_empty_sync_does_not_jump_cursor(self):
        stream=self.store.node()['stream']
        p=self.store.sync(stream,0)
        self.assertEqual(p['next_after'],0)
        self.assertFalse(p['more'])

    def test_changed_stream_and_ahead_cursor_rejected(self):
        from wnp_host.model import WNPError
        import uuid
        with self.assertRaises(WNPError):self.store.sync(str(uuid.uuid4()),0)
        with self.assertRaises(WNPError):self.store.sync(self.store.node()['stream'],1)

    def test_torn_tail_is_not_served_and_recovery_preserves_evidence(self):
        from wnp_host.model import WNPError
        self.store.append('test.event')
        with (self.root/'.wnp/events.ndjson').open('ab') as f:f.write(b'{"sequence":2')
        with self.assertRaises(WNPError):self.store.events()
        report=self.store.recover()
        self.assertTrue(report['tail_quarantined'])
        self.assertEqual(len(self.store.events()),1)
        self.assertTrue(list((self.root/'.wnp/recovery').glob('*.tail')))

    def test_complete_corruption_is_not_discarded(self):
        from wnp_host.model import WNPError
        path=self.root/'.wnp/events.ndjson'
        path.write_bytes(b'bad-json\n')
        with self.assertRaises(WNPError):self.store.recover()
        self.assertEqual(path.read_bytes(),b'bad-json\n')

    def test_snapshot_ahead_is_integrity_error(self):
        from wnp_host.model import WNPError
        path=self.root/'.wnp/state.json'
        obj=json.loads(path.read_text());obj['sequence']=10
        path.write_text(json.dumps(obj))
        with self.assertRaises(WNPError):self.store.snapshot()

    def test_evidence_registered_and_hash_checked(self):
        from wnp_host.model import WNPError
        ref=self.store.register_evidence(b'approved redacted report')
        with self.assertRaises(WNPError):self.store.evidence(ref['path'])
        self.store.append('verification.passed',evidence=[ref])
        self.assertEqual(self.store.evidence(ref['path']),b'approved redacted report')
        (self.root/'.wnp'/ref['path']).write_bytes(b'corrupt')
        with self.assertRaises(WNPError):self.store.evidence(ref['path'])

    def test_evidence_traversal_denied(self):
        from wnp_host.model import WNPError
        for p in ['../wnp.json','evidence/../node.json','C:/secrets','/etc/passwd','evidence/a%2fb','evidence/a\\b']:
            with self.subTest(p=p),self.assertRaises(WNPError):self.store.evidence(p)

    def test_evidence_symlink_denied(self):
        from wnp_host.model import WNPError
        ref=self.store.register_evidence(b'x')
        self.store.append('verification.passed',evidence=[ref])
        p=self.root/'.wnp'/ref['path'];p.unlink()
        other=self.root/'outside.txt';other.write_bytes(b'x')
        try:p.symlink_to(other)
        except (OSError,NotImplementedError):self.skipTest('symlink unavailable')
        with self.assertRaises(WNPError):self.store.evidence(ref['path'])

    def test_validation_rejects_nan_and_bad_progress(self):
        from wnp_host.model import WNPError
        for patch in [{'progress':{'current':4,'total':3,'unit':'frames'}},{'state':'made-up'}]:
            with self.assertRaises(WNPError):self.store.append('task.state',patch=patch)
        with self.assertRaises(WNPError):self.store.append('test.event',data={'x':float('nan')})

    def test_unknown_extension_roundtrips(self):
        self.store.append('custom.fact',patch={'extensions':{'future':{'value':True}}})
        self.assertTrue(self.store.snapshot()['extensions']['future']['value'])

    def test_explicit_null_clears_task(self):
        self.store.append('run.started',patch={'task':'shot-001'})
        self.store.append('run.state',patch={'task':None})
        self.assertIsNone(self.store.snapshot()['task'])

if __name__=='__main__':unittest.main()
