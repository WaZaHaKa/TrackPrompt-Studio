import importlib.util
import tempfile
import unittest
from pathlib import Path
from wnp_host.store import Store

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('wnp_trackprompt'),'Adapter must be implemented')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store.initialize(Path(self.tmp.name),'trackprompt-studio','render-desktop')
    def test_verified_raw_is_not_final_task_or_run_complete(self):
        from wnp_trackprompt import publish
        o={'run':'344ab3d2444c','task':'shot-001','attempt':'attempt-001','phase':'raw_verified','workload_state':'waiting','health':'ok','raw_manifest_sha256':'a'*64}
        publish(self.store,o);publish(self.store,o)
        self.assertEqual(len(self.store.events()),1)
        self.assertNotEqual(self.store.snapshot()['state'],'complete')
        self.assertEqual(self.store.snapshot()['detail'],'render.raw_verified')
    def test_raw_verified_requires_manifest_binding(self):
        from wnp_trackprompt import publish
        from wnp_host.model import WNPError
        with self.assertRaises(WNPError):publish(self.store,{'run':'r1','task':'shot-001','attempt':'a1','phase':'raw_verified','workload_state':'waiting','health':'ok'})
    def test_completion_requires_final_validation(self):
        from wnp_trackprompt import publish
        from wnp_host.model import WNPError
        with self.assertRaises(WNPError):publish(self.store,{'run':'r1','phase':'complete','workload_state':'complete','health':'ok'})
    def test_worker_fields_are_transient(self):
        from wnp_trackprompt import publish
        o={'run':'r1','task':'shot-002','attempt':'a1','phase':'submitted','workload_state':'waiting','health':'degraded','worker':{'state':'stale'}}
        publish(self.store,o);o['worker']={'state':'active'};publish(self.store,o)
        self.assertEqual(len(self.store.events()),1)
        self.assertEqual(self.store.snapshot()['observations']['worker']['state'],'active')

class FrameTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.Image=Image;self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        for i in range(1,4):Image.new('RGB',(2,2)).save(self.root/f'frame_{i:08d}.png')
    def test_full_decode_and_hashes(self):
        from frame_check import verify_frames
        r=verify_frames(self.root,3,2,2)
        self.assertEqual(r['count'],3);self.assertEqual(len(r['manifest_sha256']),64)
        self.assertEqual(r['workflow_binding'],'not_checked')
    def test_missing_frame_rejected(self):
        from frame_check import verify_frames
        from wnp_host.model import WNPError
        (self.root/'frame_00000002.png').unlink()
        with self.assertRaises(WNPError):verify_frames(self.root,3,2,2)
    def test_bad_decode_rejected(self):
        from frame_check import verify_frames
        from wnp_host.model import WNPError
        (self.root/'frame_00000002.png').write_bytes(b'not an image')
        with self.assertRaises(WNPError):verify_frames(self.root,3,2,2)
    def test_wrong_dimensions_rejected(self):
        from frame_check import verify_frames
        from wnp_host.model import WNPError
        with self.assertRaises(WNPError):verify_frames(self.root,3,3,2)
