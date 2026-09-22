"""Synthetic-only checks for the local observation outbox; no execution imports."""
from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("app.local_video.wnp_projection"),
            "The project-owned WNP projection must exist",
        )
        self.module = importlib.import_module("app.local_video.wnp_projection")
        store_module, _ = self.module.load_wnp_kit()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = store_module.Store.initialize(self.root, "trackprompt-studio", "test-node")
        self.original_stream = self.store.node()["stream"]
        self.observation = {
            "run": "synthetic-run",
            "task": "shot-001",
            "attempt": "attempt-001",
            "phase": "raw_verified",
            "workload_state": "waiting",
            "health": "ok",
            "raw_manifest_sha256": "a" * 64,
            "tasks": [{
                "task": "shot-001", "phase": "raw_verified", "attempt": "attempt-001",
                "frames_verified": 3, "expected_frames": 3, "raw_manifest_sha256": "a" * 64,
                "updated_at": "2026-09-18T09:00:00Z",
            }],
            "worker": {"id": "test-worker", "state": "stale", "pid": 123,
                       "heartbeat_at": "2026-09-18T08:00:00Z"},
            "provider": {"name": "comfyui", "state": "unknown", "job": "opaque-job",
                         "observed_at": "2026-09-18T08:01:00Z"},
            "metrics": {"ram_used_mib": 2048, "gpu": [{"index": 0, "utilization_percent": 0}]},
            "progress": {"current": 1, "total": 7, "unit": "shots"},
        }

    def test_raw_verified_never_completes_run(self):
        with self.module.WNPProjection(self.root) as projection:
            result = projection.publish("raw-source-1", self.observation)
        self.assertTrue(result.ok)
        self.assertEqual(result.sequence, 1)
        self.assertEqual(self.store.snapshot()["state"], "waiting")
        self.assertEqual(self.store.snapshot()["detail"], "render.raw_verified")
        bad = {**self.observation, "workload_state": "complete"}
        with self.module.WNPProjection(self.root) as projection:
            result = projection.publish("invalid-completion", bad)
        self.assertFalse(result.ok)
        self.assertEqual(len(self.store.events()), 1)

    def test_identical_poll_and_restart_preserve_sequence_and_stream(self):
        with self.module.WNPProjection(self.root) as projection:
            self.assertTrue(projection.publish("source-1", self.observation).ok)
        newer = copy.deepcopy(self.observation)
        newer["worker"]["state"] = "active"
        newer["metrics"]["ram_used_mib"] = 4096
        with self.module.WNPProjection(self.root) as projection:
            self.assertTrue(projection.publish("source-1", newer).ok)
            self.assertTrue(projection.publish("another-source-same-state", newer).ok)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["sequence"], 1)
        self.assertEqual(snapshot["stream"], self.original_stream)
        self.assertEqual(snapshot["observations"]["metrics"]["ram_used_mib"], 4096)
        self.assertEqual(snapshot["observations"]["worker"]["heartbeat_at"], "2026-09-18T08:00:00Z")
        self.assertEqual(snapshot["extensions"]["trackprompt"]["tasks"][0]["updated_at"], "2026-09-18T09:00:00Z")

    def test_offline_write_is_retained_and_replayed_once(self):
        execution = {"submissions": 1, "raw_frames": 3}
        before = copy.deepcopy(execution)
        with self.module.WNPProjection(self.root) as projection:
            with patch.object(projection.store, "append", side_effect=OSError("private path and token")):
                result = projection.publish("source-offline", self.observation)
            self.assertFalse(result.ok)
            self.assertEqual(result.pending, 1)
            self.assertNotIn("private", result.error)
        self.assertEqual(execution, before)
        with self.module.WNPProjection(self.root) as projection:
            self.assertTrue(projection.drain().ok)
            self.assertTrue(projection.publish("source-offline", self.observation).ok)
        self.assertEqual(len(self.store.events()), 1)

    def test_pending_transition_does_not_block_independent_observations(self):
        with self.module.WNPProjection(self.root) as projection:
            with patch.object(projection.store, "append", side_effect=OSError("offline journal")):
                result = projection.publish("source-pending", self.observation)
        self.assertFalse(result.ok)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["sequence"], 0)
        self.assertEqual(snapshot["observations"]["worker"]["state"], "stale")

    def test_pending_transitions_replay_in_source_order(self):
        second = {**self.observation, "phase": "finalizing"}
        with self.module.WNPProjection(self.root) as projection:
            with patch.object(projection.store, "append", side_effect=OSError("offline journal")):
                self.assertFalse(projection.publish("ordered-1", self.observation).ok)
                self.assertEqual(projection.publish("ordered-2", second).pending, 2)
        with self.module.WNPProjection(self.root) as projection:
            result = projection.drain()
        self.assertTrue(result.ok)
        self.assertEqual(result.pending, 0)
        self.assertEqual([event["snapshot"]["detail"] for event in self.store.events()],
                         ["render.raw_verified", "render.finalizing"])

    def test_event_commit_then_ack_failure_is_idempotent_after_restart(self):
        with self.module.WNPProjection(self.root) as projection:
            with patch.object(projection, "_ack", side_effect=OSError("disk full")):
                result = projection.publish("source-ack-failure", self.observation)
            self.assertFalse(result.ok)
            self.assertEqual(result.pending, 1)
        self.assertEqual(len(self.store.events()), 1)
        with self.module.WNPProjection(self.root) as projection:
            self.assertTrue(projection.drain().ok)
            self.assertTrue(projection.publish("source-ack-failure", self.observation).ok)
        self.assertEqual(len(self.store.events()), 1)

    def test_snapshot_write_failure_after_event_commit_replays_once(self):
        with self.module.WNPProjection(self.root) as projection:
            with patch("wnp_host.store.atomic_write", side_effect=OSError("disk full")):
                result = projection.publish("source-snapshot-failure", self.observation)
            self.assertFalse(result.ok)
        self.assertEqual(len(self.store.events()), 1)
        with self.module.WNPProjection(self.root) as projection:
            self.assertTrue(projection.drain().ok)
        self.assertEqual(self.store.snapshot()["sequence"], 1)

    def test_outbox_failure_does_not_publish(self):
        with self.module.WNPProjection(self.root) as projection:
            with patch.object(projection, "_enqueue", side_effect=OSError("private path")):
                result = projection.publish("unpersisted-source", self.observation)
            self.assertFalse(result.ok)
        self.assertEqual(self.store.events(), [])

    def test_source_identity_reuse_with_different_transition_is_rejected(self):
        with self.module.WNPProjection(self.root) as projection:
            self.assertTrue(projection.publish("source-1", self.observation).ok)
            different = {**self.observation, "phase": "finalizing"}
            result = projection.publish("source-1", different)
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "source_identity_conflict")
        self.assertEqual(len(self.store.events()), 1)

    def test_state_revisit_is_a_new_event_but_old_source_replay_is_not(self):
        second = {**self.observation, "phase": "finalizing"}
        with self.module.WNPProjection(self.root) as projection:
            self.assertTrue(projection.publish("source-1", self.observation).ok)
            self.assertTrue(projection.publish("source-2", second).ok)
            self.assertTrue(projection.publish("source-3", self.observation).ok)
            self.assertTrue(projection.publish("source-2", second).ok)
        self.assertEqual(len(self.store.events()), 3)
        self.assertEqual(self.store.snapshot()["detail"], "render.raw_verified")

    def test_second_adapter_owner_cannot_acquire_lock(self):
        with self.module.WNPProjection(self.root):
            with self.assertRaises(Exception) as caught:
                with self.module.WNPProjection(self.root):
                    self.fail("Two adapters acquired the same lock")
            self.assertEqual(getattr(caught.exception, "code", None), "temporarily_unavailable")
        with self.module.WNPProjection(self.root) as projection:
            self.assertTrue(projection.publish("after-unlock", self.observation).ok)

    def test_same_stream_rollback_cannot_silently_acknowledge_old_state(self):
        with self.module.WNPProjection(self.root) as projection:
            self.assertTrue(projection.publish("before-rollback-1", self.observation).ok)
            old_state = (self.root / ".wnp" / "state.json").read_bytes()
            old_events = (self.root / ".wnp" / "events.ndjson").read_bytes()
            self.assertTrue(projection.publish("before-rollback-2", {**self.observation, "phase": "finalizing"}).ok)
        # Synthetic coherent history rollback, while the independent ACK ledger survives.
        (self.root / ".wnp" / "state.json").write_bytes(old_state)
        (self.root / ".wnp" / "events.ndjson").write_bytes(old_events)
        with self.assertRaises(self.module.ProjectionError) as caught:
            with self.module.WNPProjection(self.root):
                self.fail("A same-stream rollback was silently accepted")
        self.assertEqual(caught.exception.code, "outbox_cursor_ahead")

    def test_transient_write_failure_does_not_duplicate_durable_event(self):
        with self.module.WNPProjection(self.root) as projection:
            with patch.object(projection.store, "observe", side_effect=OSError("disk full")):
                result = projection.publish("source-live-failure", self.observation)
            self.assertFalse(result.ok)
            self.assertEqual(result.pending, 0)
        with self.module.WNPProjection(self.root) as projection:
            self.assertTrue(projection.publish("source-live-failure", self.observation).ok)
        self.assertEqual(len(self.store.events()), 1)

    def test_unknown_and_nested_private_fields_never_reach_export_or_outbox(self):
        observation = copy.deepcopy(self.observation)
        private = "C:\\Private\\secret-prompt-and-audio.wav"
        observation.update({"prompt": private, "audio": private, "evidence": {"path": private}})
        observation["tasks"][0].update({"prompt": private, "path": private, "image": private})
        observation["worker"]["command_line"] = private
        observation["provider"]["url"] = private
        observation["metrics"]["gpu"][0]["name"] = private
        with self.module.WNPProjection(self.root) as projection:
            self.assertTrue(projection.publish(private, observation).ok)
        self.assertNotIn("Private", json.dumps(self.store.snapshot()))
        for path in (self.root / ".wnp").iterdir():
            if path.is_file():
                self.assertNotIn(b"secret-prompt", path.read_bytes())

    def test_complete_requires_verified_final_manifest(self):
        invalid = {**self.observation, "phase": "complete", "workload_state": "complete"}
        with self.module.WNPProjection(self.root) as projection:
            self.assertFalse(projection.publish("invalid-final", invalid).ok)
            valid = {**invalid, "final_verified": True, "final_manifest_sha256": "f" * 64}
            self.assertTrue(projection.publish("valid-final", valid).ok)
        self.assertEqual(self.store.snapshot()["state"], "complete")

    def test_observe_is_independent_of_durable_cursor(self):
        with self.module.WNPProjection(self.root) as projection:
            self.assertTrue(projection.observe(self.observation).ok)
        self.assertEqual(self.store.snapshot()["sequence"], 0)
        self.assertEqual(self.store.snapshot()["observations"]["progress"]["current"], 1)

    def test_nonfinite_metrics_are_rejected_without_write(self):
        observation = {**self.observation, "metrics": {"cpu_percent": float("nan")}}
        with self.module.WNPProjection(self.root) as projection:
            result = projection.publish("bad-metrics", observation)
        self.assertFalse(result.ok)
        self.assertEqual(self.store.events(), [])


if __name__ == "__main__":
    unittest.main()
