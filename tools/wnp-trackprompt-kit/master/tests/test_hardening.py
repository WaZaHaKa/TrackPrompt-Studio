import tempfile, unittest, json, threading
from pathlib import Path
from unittest.mock import patch
from wnp_host.store import Store
from wnp_host.model import WNPError
from wnp_host.fs import FileLock

class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.s=Store.initialize(self.root,'test','node')
    def test_complete_gap_plus_torn_tail_preserves_all_bytes(self):
        e=self.s.append('test.fact');e['sequence']=3
        p=self.root/'.wnp/events.ndjson';p.write_bytes(json.dumps(e).encode()+b'\n{"partial"')
        before=p.read_bytes()
        with self.assertRaises(WNPError):self.s.recover()
        self.assertEqual(p.read_bytes(),before)
    def test_crash_after_durable_event_before_snapshot_is_recoverable(self):
        with patch('wnp_host.store.atomic_write',side_effect=OSError('simulated crash')):
            with self.assertRaises(OSError):self.s.append('run.started',patch={'state':'running'},key='once')
        self.assertEqual(self.s.snapshot()['sequence'],1)
        self.s.recover()
        self.s.append('run.started',patch={'state':'running'},key='once')
        self.assertEqual(len(self.s.events()),1)
    def test_parallel_writers_get_contiguous_sequences(self):
        errors=[]
        def write(i):
            try:self.s.append('test.fact',key=f'key-{i}')
            except Exception as e:errors.append(e)
        ts=[threading.Thread(target=write,args=(i,)) for i in range(12)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(errors,[])
        self.assertEqual([e['sequence'] for e in self.s.events()],list(range(1,13)))
    def test_live_lock_cannot_be_stolen_by_second_owner(self):
        lock=self.root/'run.lock'
        with FileLock(lock):
            with self.assertRaises(WNPError):
                with FileLock(lock,timeout=0):pass
