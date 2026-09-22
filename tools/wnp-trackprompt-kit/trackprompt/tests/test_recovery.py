import importlib.util
import tempfile
import unittest
from pathlib import Path

class RecoveryTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(importlib.util.find_spec('recovery_policy'),'Recovery policy must be implemented')
    def decision(self,**kw):
        from recovery_policy import Observation,decide
        return decide(Observation(**kw))
    def test_verified_raw_never_regenerated(self):
        d=self.decision(raw='verified',provider='absent',submission='accepted',owner_held=True)
        self.assertEqual(d.action,'reuse_raw');self.assertFalse(d.may_submit)
    def test_raw_complete_before_marker_only_verifies(self):
        self.assertEqual(self.decision(raw='complete_unverified',owner_held=True).action,'verify_raw')
    def test_next_shot_queued_is_adopted(self):
        self.assertEqual(self.decision(provider='queued',submission='accepted',owner_held=True).action,'adopt')
    def test_next_shot_history_outputs_recovered(self):
        self.assertEqual(self.decision(provider='completed',submission='accepted',owner_held=True).action,'recover_outputs')
    def test_absent_history_after_submit_is_not_permission_to_resubmit(self):
        d=self.decision(provider='absent',submission='sending',owner_held=True)
        self.assertEqual(d.action,'hold_ambiguous');self.assertFalse(d.may_submit)
    def test_unreachable_provider_does_not_mean_absent(self):
        self.assertEqual(self.decision(provider='unreachable',submission='prepared',owner_held=True).action,'wait_provider')
    def test_only_provably_never_sent_can_submit_once(self):
        d=self.decision(provider='absent',submission='prepared',owner_held=True)
        self.assertEqual(d.action,'submit_once');self.assertTrue(d.may_submit)
    def test_no_owner_no_submission(self):
        self.assertFalse(self.decision(provider='absent',submission='prepared',owner_held=False).may_submit)
    def test_conflicting_artifact_identity_stops(self):
        d=self.decision(raw='verified',identity_match=False,owner_held=True)
        self.assertEqual(d.action,'hold_integrity')
    def test_duplicate_provider_matches_stop(self):
        self.assertEqual(self.decision(provider='running',matches=2,owner_held=True).action,'hold_duplicate')
    def test_partial_or_corrupt_frames_not_overwritten(self):
        for raw in ('partial','invalid'):
            self.assertFalse(self.decision(raw=raw,provider='absent',submission='prepared',owner_held=True).may_submit)
    def test_provider_failure_recorded_not_timeout_workaround(self):
        self.assertEqual(self.decision(provider='failed',owner_held=True).action,'failed_attempt')
    def test_129_frames_are_not_a_60_second_final(self):
        from recovery_policy import final_frames
        self.assertEqual(final_frames(60,24),1440)
        self.assertNotEqual(129,final_frames(60,24))

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('attempt_ledger'),'Attempt ledger must be implemented')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
    def test_send_started_is_durable_before_external_call(self):
        from attempt_ledger import AttemptLedger
        from wnp_host.fs import FileLock
        with FileLock(self.path/'run.lock') as lock:
            ledger=AttemptLedger(self.path/'attempts.sqlite3',lock)
            ledger.prepare('r1','shot-002','a1','a'*64,'b'*64)
            ledger.begin_send('r1','shot-002','a1')
            self.assertEqual(ledger.get('r1','shot-002','a1')['state'],'sending')
            from wnp_host.model import WNPError
            with self.assertRaises(WNPError):ledger.begin_send('r1','shot-002','a1')
    def test_accept_after_reconnect_preserves_prompt(self):
        from attempt_ledger import AttemptLedger
        from wnp_host.fs import FileLock
        with FileLock(self.path/'run.lock') as lock:
            ledger=AttemptLedger(self.path/'attempts.sqlite3',lock)
            ledger.prepare('r1','shot-002','a1','a'*64,'b'*64);ledger.begin_send('r1','shot-002','a1')
        with FileLock(self.path/'run.lock') as lock:
            ledger=AttemptLedger(self.path/'attempts.sqlite3',lock)
            ledger.accept('r1','shot-002','a1','provider-job-001')
            self.assertEqual(ledger.get('r1','shot-002','a1')['prompt_id'],'provider-job-001')
    def test_changed_workflow_cannot_reuse_attempt(self):
        from attempt_ledger import AttemptLedger
        from wnp_host.fs import FileLock
        from wnp_host.model import WNPError
        with FileLock(self.path/'run.lock') as lock:
            ledger=AttemptLedger(self.path/'attempts.sqlite3',lock)
            ledger.prepare('r1','shot-002','a1','a'*64,'b'*64)
            with self.assertRaises(WNPError):ledger.prepare('r1','shot-002','a1','c'*64,'b'*64)
    def test_second_process_owner_refused_despite_stale_time(self):
        from wnp_host.fs import FileLock
        from wnp_host.model import WNPError
        with FileLock(self.path/'run.lock'):
            with self.assertRaises(WNPError):
                with FileLock(self.path/'run.lock',timeout=0):pass
