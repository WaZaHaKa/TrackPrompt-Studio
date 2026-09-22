import importlib.util,json,hashlib,tempfile,unittest
from pathlib import Path
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('bundle_tools'),'Installer implementation required')
        import bundle_tools
        self.b=bundle_tools;self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.p=Path(self.t.name)
        self.bundle=self.p/'bundle';self.bundle.mkdir();(self.bundle/'file.txt').write_text('hello')
        self.manifest={'package':'master','version':'1.0.0','destination':'wnp-master-kit','files':[{'path':'file.txt','sha256':hashlib.sha256(b'hello').hexdigest(),'bytes':5}]}
        (self.bundle/'BUNDLE_MANIFEST.json').write_text(json.dumps(self.manifest));self.repo=self.p/'repo with spaces';self.repo.mkdir();(self.repo/'.git').mkdir()
    def test_dry_run_writes_nothing(self):
        self.b.install(self.bundle,self.repo,False)
        self.assertFalse((self.repo/'tools').exists())
    def test_apply_idempotent_and_keeps_existing_repo_files(self):
        (self.repo/'README.md').write_text('existing')
        self.b.install(self.bundle,self.repo,True);self.b.install(self.bundle,self.repo,True)
        self.assertEqual((self.repo/'tools/wnp-master-kit/file.txt').read_text(),'hello')
        self.assertEqual((self.repo/'README.md').read_text(),'existing')
    def test_conflict_stops_before_any_copy(self):
        dst=self.repo/'tools/wnp-master-kit';dst.mkdir(parents=True);(dst/'file.txt').write_text('local edit')
        with self.assertRaises(ValueError):self.b.install(self.bundle,self.repo,True)
        self.assertFalse((dst/'BUNDLE_MANIFEST.json').exists())
    def test_tamper_rejected(self):
        (self.bundle/'file.txt').write_text('wrong')
        with self.assertRaises(ValueError):self.b.install(self.bundle,self.repo,True)
    def test_unsafe_manifest_path_rejected(self):
        self.manifest['files'][0]['path']='../file.txt';(self.bundle/'BUNDLE_MANIFEST.json').write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):self.b.verify(self.bundle)
    def test_existing_symlink_rejected(self):
        target=self.repo/'tools';outside=self.p/'outside';outside.mkdir()
        try:target.symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('symlinks unavailable')
        with self.assertRaises(ValueError):self.b.install(self.bundle,self.repo,True)
    def test_non_repository_refused(self):
        (self.repo/'.git').rmdir()
        with self.assertRaises(ValueError):self.b.install(self.bundle,self.repo,True)
