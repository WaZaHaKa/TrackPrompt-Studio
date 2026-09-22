"""Offline fixtures for the independent Hayale WNP reader and service."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from app.local_video.comfyui import ComfyUIProviderError
from app.video_generation.jsonio import atomic_write_json, sha256_file, sha256_json

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "video-projects/hayale-flowers-promo/v2.1"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((PROJECT / "observation.py").is_file(), "Verified reader must exist")
        self.module = load("hayale_test_observation", PROJECT / "observation.py")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "runs" / "344ab3d2444c"
        self.root.mkdir(parents=True)
        self.manifest_path = Path(self.tmp.name) / "prepared" / "manifest.json"
        manifest = {"references": [], "sources": [], "installed": [], "files": [], "profiles": {"quality": [832, 480]}}
        manifest["digest"] = sha256_json(manifest)
        atomic_write_json(self.manifest_path, manifest)
        atomic_write_json(self.root / "run.json", {"profile": "quality", "preparation": manifest["digest"], "silent": False})
        self.now = datetime(2026, 9, 18, 12, tzinfo=UTC)
        self.verifier = Mock(return_value={
            "manifest_sha256": "a" * 64, "count": 129, "width": 832, "height": 480,
            "prompt_id": "fixture-prompt", "observed_at": "2026-09-18T10:00:00Z", "workflow_binding": "verified",
        })
        self.provider = Mock()
        self.provider.return_value.__enter__ = Mock(side_effect=ComfyUIProviderError("provider_unavailable", "offline"))
        self.provider.return_value.__exit__ = Mock(return_value=False)

    def reader(self, **kwargs):
        return self.module.HayaleObservationReader(
            self.root, self.manifest_path, checkpoint_verifier=self.verifier,
            provider_factory=self.provider, process_started=lambda pid: self.now - timedelta(hours=1),
            clock=lambda: self.now, **kwargs,
        )

    def raw(self, shot="shot-001"):
        path = self.root / shot / "raw" / "frame_00000001.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"synthetic-verifier-fixture")
        return path

    def test_marker_alone_never_proves_raw_or_final_completion(self):
        atomic_write_json(self.root / "shot-001" / "raw-verified.json", {"frames": 129})
        _, observation = self.reader().read()
        self.assertNotEqual(observation["phase"], "raw_verified")
        self.assertNotEqual(observation["workload_state"], "complete")
        self.verifier.assert_not_called()

    def test_verified_checkpoint_cached_only_while_all_inputs_unchanged(self):
        raw = self.raw()
        reader = self.reader()
        key, first = reader.read()
        next_key, second = reader.read()
        self.assertEqual(key, next_key)
        self.assertEqual(self.verifier.call_count, 1)
        self.assertEqual(first["tasks"][0]["phase"], "raw_verified")
        self.assertEqual(second["tasks"][0]["updated_at"], "2026-09-18T10:00:00Z")
        raw.write_bytes(b"changed-synthetic-frame")
        self.verifier.side_effect = ValueError("frame changed")
        _, third = reader.read()
        self.assertEqual(self.verifier.call_count, 2)
        self.assertNotEqual(third["tasks"][0]["phase"], "raw_verified")

    def test_provenance_change_invalidates_cached_raw(self):
        self.raw()
        receipt = self.root / "shot-001" / "submission.json"
        atomic_write_json(receipt, {"promptId": "fixture-prompt"})
        reader = self.reader()
        reader.read()
        atomic_write_json(receipt, {"promptId": "different-prompt"})
        self.verifier.side_effect = ValueError("provenance changed")
        _, observed = reader.read()
        self.assertEqual(self.verifier.call_count, 2)
        self.assertNotEqual(observed["tasks"][0]["phase"], "raw_verified")

    def test_restart_reuses_unchanged_proof_and_original_source_timestamp(self):
        self.raw()
        cache = Path(self.tmp.name) / "observer-cache.json"
        first_key, first = self.reader(cache_path=cache).read()
        self.verifier.return_value["observed_at"] = "2026-09-18T11:00:00Z"
        next_key, second = self.reader(cache_path=cache).read()
        self.assertEqual(self.verifier.call_count, 1)
        self.assertEqual(first_key, next_key)
        self.assertEqual(first, second)
        self.assertNotIn("synthetic-verifier-fixture", cache.read_text())

    def test_provider_loss_is_unknown_never_failed_or_absent(self):
        atomic_write_json(self.root / "shot-001" / "submission.json", {"promptId": "fixture-prompt"})
        _, observation = self.reader().read()
        self.assertEqual(observation["provider"]["state"], "unreachable")
        self.assertEqual(observation["phase"], "waiting_provider")
        self.assertEqual(observation["workload_state"], "waiting")
        self.assertNotIn("offline", json.dumps(observation))

    def test_worker_pid_alone_never_counts_as_active(self):
        atomic_write_json(self.root / "worker.json", {"pid": 123, "run": self.root.name, "heartbeat_at": self.now.isoformat(), "state": "active"})
        _, observed = self.reader().read()
        self.assertEqual(observed["worker"]["state"], "unknown")

    def test_bound_worker_staleness_preserves_source_time(self):
        heartbeat = (self.now - timedelta(minutes=5)).isoformat()
        atomic_write_json(self.root / "worker.json", {"pid": 123, "run": self.root.name,
            "started_at": (self.now - timedelta(hours=1)).isoformat(), "heartbeat_at": heartbeat, "state": "active"})
        _, observed = self.reader().read()
        self.assertEqual(observed["worker"]["state"], "stale")
        self.assertEqual(observed["worker"]["heartbeat_at"], heartbeat)
        self.assertNotEqual(observed["workload_state"], "failed")

    def test_clock_jump_does_not_create_workload_failure(self):
        atomic_write_json(self.root / "worker.json", {"pid": 123, "run": self.root.name,
            "started_at": (self.now - timedelta(hours=1)).isoformat(),
            "heartbeat_at": (self.now + timedelta(minutes=5)).isoformat(), "state": "active"})
        _, observed = self.reader().read()
        self.assertEqual(observed["worker"]["state"], "unknown")
        self.assertNotEqual(observed["workload_state"], "failed")

    def test_unbound_delivery_never_marks_run_complete(self):
        atomic_write_json(self.root / "delivery.json", {"report": {"frames": 1440, "size": [1920, 1080], "fps": 24, "durationSeconds": 60}, "files": []})
        _, observed = self.reader().read()
        self.assertNotEqual(observed["workload_state"], "complete")
        self.assertNotIn("final_verified", observed)

    def test_all_verified_raw_remains_finalizing(self):
        for index in range(1, 8):
            self.raw(f"shot-{index:03d}")
        _, observation = self.reader().read()
        self.assertEqual(observation["phase"], "finalizing")
        self.assertEqual(observation["workload_state"], "waiting")
        self.assertEqual(observation["progress"], {"current": 7, "total": 7, "unit": "shots"})

    def test_final_requires_bound_1440_frame_audio_report_and_honest_review_state(self):
        for index in range(1, 8):
            self.raw(f"shot-{index:03d}")
        output = self.root / "deliveries" / "fixture"
        output.mkdir(parents=True)
        preview = output / "preview.mp4"
        preview.write_bytes(b"synthetic-artifact-for-trusted-report-binding-test")
        report = {"frames": 1440, "fps": 24, "durationSeconds": 60, "size": [1920, 1080],
                  "preview": str(preview), "previewSha256": sha256_file(preview), "audioPresent": True,
                  "uniqueGenerations": 7, "logoUses": 2, "rawClipsUnchanged": True,
                  "sourceAudioUnchanged": True, "creativeReview": "pending"}
        atomic_write_json(output / "verification-report.json", report)
        atomic_write_json(output / "ffprobe-evidence.json", {"streams": [
            {"codec_type": "video", "width": 1920, "height": 1080, "nb_read_frames": "1440", "avg_frame_rate": "24/1"},
            {"codec_type": "audio", "sample_rate": "48000", "channels": 2, "duration": "60.0"},
        ], "format": {"duration": "60.0"}})
        files = [{"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size} for path in output.iterdir()]
        atomic_write_json(self.root / "delivery.json", {"report": report, "files": files})
        reader = self.reader()
        _, verified = reader.read()
        self.assertEqual(verified["phase"], "final_verified")
        self.assertEqual(verified["workload_state"], "waiting")
        preview.write_bytes(b"changed")
        _, invalid = reader.read()
        self.assertEqual(invalid["phase"], "reconciling")
        self.assertNotIn("final_verified", invalid)

    def test_provider_observations_use_guarded_inspection_and_do_not_export_history(self):
        self.raw()
        atomic_write_json(self.root / "shot-002/submission.json", {"promptId": "fixture-prompt-2"})
        client = Mock()
        self.provider.return_value.__enter__ = Mock(return_value=(client, {}))
        inspected = {"state": "frames_complete", "promptId": "fixture-prompt-2",
                     "history": {"private": "PROMPT-AND-PATH-MUST-NOT-EXPORT"}}
        with patch.object(self.module, "sibling", return_value=Mock(inspect_submission=Mock(return_value=inspected))):
            _, observed = self.reader().read()
        self.assertEqual(observed["tasks"][0]["phase"], "raw_verified")
        self.assertEqual(observed["tasks"][1]["phase"], "frames_complete")
        self.assertEqual(observed["provider"]["state"], "completed")
        self.assertNotIn("PROMPT-AND-PATH", json.dumps(observed))

    def test_revisiting_provider_state_has_new_stable_transition_identity(self):
        atomic_write_json(self.root / "shot-001/submission.json", {"promptId": "fixture-prompt"})
        cache = Path(self.tmp.name) / "cache.json"
        reader = self.reader(cache_path=cache)
        offline_key, _ = reader.read()
        self.provider.return_value.__enter__ = Mock(return_value=(Mock(), {}))
        recovery = Mock(inspect_submission=Mock(return_value={"state": "frames_complete", "promptId": "fixture-prompt"}))
        with patch.object(self.module, "sibling", return_value=recovery):
            completed_key, _ = reader.read()
        self.provider.return_value.__enter__ = Mock(side_effect=ComfyUIProviderError("provider_unavailable", "offline again"))
        again_key, observation = reader.read()
        self.assertNotEqual(offline_key, completed_key)
        self.assertNotEqual(offline_key, again_key)
        restarted_key, restarted = self.reader(cache_path=cache).read()
        self.assertEqual(restarted_key, again_key)
        self.assertEqual(restarted, observation)

    def test_transition_identity_write_failure_stops_publication_until_retry(self):
        reader = self.reader(cache_path=Path(self.tmp.name) / "cache.json")
        with patch.object(self.module, "atomic_write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                reader.read()
        first_key, _ = reader.read()
        second_key, _ = reader.read()
        self.assertEqual(first_key, second_key)

    def test_provider_failure_does_not_refresh_successful_source_observation_time(self):
        atomic_write_json(self.root / "shot-001/submission.json", {"promptId": "fixture-prompt"})
        reader = self.reader()
        self.provider.return_value.__enter__ = Mock(return_value=(Mock(), {}))
        recovery = Mock(inspect_submission=Mock(return_value={"state": "frames_complete", "promptId": "fixture-prompt"}))
        with patch.object(self.module, "sibling", return_value=recovery):
            _, successful = reader.read()
        self.now += timedelta(minutes=3)
        self.provider.return_value.__enter__ = Mock(side_effect=ComfyUIProviderError("provider_unavailable", "offline"))
        _, unavailable = reader.read()
        self.assertEqual(unavailable["provider"]["observed_at"], successful["provider"]["observed_at"])

    def test_read_only_client_rejects_mutation_before_transport(self):
        client = self.module.ReadOnlyClient("http://127.0.0.1:8189")
        with patch.object(client, "_opener") as opener:
            with self.assertRaises(ValueError):
                client._request_bytes("POST", "/prompt", body=b"{}")
        opener.open.assert_not_called()

    def test_read_only_client_allows_bounded_full_history_get(self):
        client = self.module.ReadOnlyClient("http://127.0.0.1:8189")
        with patch("app.local_video.comfyui.ComfyUIClient._request_bytes", return_value=b"{}") as transport:
            self.assertEqual(client._request_bytes("GET", "/history"), b"{}")
        transport.assert_called_once_with("GET", "/history")

    def test_identity_mismatch_is_unknown_reconciling_not_unreachable(self):
        atomic_write_json(self.root / "shot-001/submission.json", {"promptId": "fixture-prompt"})
        self.provider.return_value.__enter__ = Mock(side_effect=ValueError("wrong run"))
        _, observed = self.reader().read()
        self.assertEqual(observed["provider"]["state"], "unknown")
        self.assertEqual(observed["phase"], "reconciling")

    def test_changed_provider_instance_is_rejected_after_observer_restart(self):
        atomic_write_json(self.root / "shot-001/submission.json", {"promptId": "fixture-prompt"})
        cache = Path(self.tmp.name) / "cache.json"
        client = Mock()
        client.observation_identity = {"pid": 10, "started_at": self.now.isoformat(), "run": self.root.name,
                                       "server_record_sha256": "a" * 64}
        self.provider.return_value.__enter__ = Mock(return_value=(client, {}))
        recovery = Mock(inspect_submission=Mock(return_value={"state": "frames_complete", "promptId": "fixture-prompt"}))
        with patch.object(self.module, "sibling", return_value=recovery):
            _, first = self.reader(cache_path=cache).read()
        self.assertEqual(first["provider"]["state"], "completed")
        client.observation_identity = {**client.observation_identity, "started_at": (self.now + timedelta(minutes=1)).isoformat()}
        with patch.object(self.module, "sibling", return_value=recovery):
            _, changed = self.reader(cache_path=cache).read()
        self.assertEqual(changed["provider"]["state"], "unknown")
        self.assertEqual(changed["phase"], "reconciling")
        self.assertEqual(recovery.inspect_submission.call_count, 1)

    def test_legacy_provider_requires_audited_start_binding_before_any_get(self):
        metadata = self.root / "comfy/server-process.json"
        atomic_write_json(metadata, {"pid": 123, "endpoint": "http://127.0.0.1:8189"})
        runtime = Mock(ENDPOINT="http://127.0.0.1:8189")
        with patch.object(self.module, "sibling", return_value=runtime), patch.object(self.module, "ReadOnlyClient") as client_factory:
            with self.assertRaises(ValueError):
                with self.module.provider_connection(self.root):
                    self.fail("Unbound provider PID accepted")
        client_factory.assert_not_called()

    def test_audited_legacy_provider_binds_pid_start_record_bytes_and_run_directories(self):
        metadata = self.root / "comfy/server-process.json"
        atomic_write_json(metadata, {"pid": 123, "endpoint": "http://127.0.0.1:8189"})
        audited = {"pid": 123, "started_at": self.now.isoformat(), "run": self.root.name,
                   "server_record_sha256": sha256_file(metadata)}
        atomic_write_json(self.root / "recovery/provider-instance.json", audited)
        runtime = Mock(ENDPOINT="http://127.0.0.1:8189")
        stats = {"system": {"argv": ["--output-directory", str(self.root / "comfy/output"),
                                      "--input-directory", str(self.root / "comfy/input")]}}
        with patch.object(self.module, "sibling", return_value=runtime), patch.object(self.module, "process_started_at", return_value=self.now), patch.object(self.module, "ReadOnlyClient") as client_factory:
            client_factory.return_value._request_json.return_value = stats
            with self.module.provider_connection(self.root) as (client, observed):
                self.assertEqual(observed, stats)
                self.assertEqual(client.observation_identity, audited)
        runtime.require_listener_owner.assert_called_once_with(123)
        client_factory.return_value._request_json.assert_called_once_with("GET", "/system_stats")

    def test_reused_provider_pid_with_wrong_start_is_rejected_before_get(self):
        atomic_write_json(self.root / "comfy/server-process.json", {"pid": 123, "endpoint": "http://127.0.0.1:8189", "started_at": self.now.isoformat()})
        runtime = Mock(ENDPOINT="http://127.0.0.1:8189")
        with patch.object(self.module, "sibling", return_value=runtime), patch.object(self.module, "process_started_at", return_value=self.now + timedelta(minutes=1)), patch.object(self.module, "ReadOnlyClient") as client_factory:
            with self.assertRaises(ValueError):
                with self.module.provider_connection(self.root):
                    self.fail("Reused PID accepted")
        client_factory.assert_not_called()


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((REPO / "tools/hayale-wnp.py").is_file(), "Observer CLI must exist")
        self.cli = load("hayale_test_wnp_cli", REPO / "tools/hayale-wnp.py")

    def test_poll_failure_is_safe_and_does_not_stop_api_or_change_execution(self):
        reader, projection = Mock(), Mock()
        reader.read.side_effect = RuntimeError("private source path")
        result = self.cli.poll_once(reader, projection)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "reader_unavailable")
        projection.publish.assert_not_called()
        self.assertNotIn("private", json.dumps(result))

    def test_launcher_defaults_to_check_only_and_uses_stable_hidden_host(self):
        text = (REPO / "tools/start-hayale-wnp.ps1").read_text()
        self.assertIn("[switch]$Start", text)
        self.assertIn("--launch", text)
        self.assertNotIn("Start-Process", text)
        self.assertIn("Python312", text)
        self.assertIn("--check-only", text)
        self.assertNotIn("Stop-Process", text)
        self.assertNotIn("New-Service", text)

    @unittest.skipUnless(os.name == "nt", "Native Windows process flags")
    def test_detached_spawn_is_explicit_and_never_retries_denied_breakaway(self):
        self.assertTrue(hasattr(self.cli, "_native_detached_python"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(self.cli, "stable_python", return_value=Path("C:/fixture/Python312/python.exe")), patch.object(self.cli.subprocess, "Popen", side_effect=PermissionError("Job forbids breakaway")) as spawn:
                with self.assertRaises(PermissionError):
                    self.cli._native_detached_python(["-c", "pass"], root / "out.log", root / "err.log", cwd=root)
        self.assertEqual(spawn.call_count, 1)
        kwargs = spawn.call_args.kwargs
        expected = subprocess.CREATE_BREAKAWAY_FROM_JOB | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        self.assertEqual(kwargs["creationflags"], expected)
        self.assertTrue(kwargs["close_fds"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["startupinfo"].wShowWindow, 0)

    @unittest.skipUnless(os.name == "nt", "Native Windows Job Object acceptance")
    def test_detached_heartbeat_survives_disposable_kill_on_close_job_parent(self):
        self.assertTrue(hasattr(self.cli, "spawn_detached_python"))
        # Both subprocesses are synthetic, bounded, and unrelated to render/API workers.
        parent_source = r'''
import ctypes, importlib.util, json, os, pathlib, sys, time
from ctypes import wintypes
class Basic(ctypes.Structure):
    _fields_ = [("ProcessUserTimeLimit", ctypes.c_int64), ("JobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
class Counters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
class Extended(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Counters), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel.CreateJobObjectW.restype = wintypes.HANDLE
kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
kernel.SetInformationJobObject.restype = wintypes.BOOL
kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel.AssignProcessToJobObject.restype = wintypes.BOOL
kernel.GetCurrentProcess.restype = wintypes.HANDLE
job = kernel.CreateJobObjectW(None, None)
limits = Extended()
limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x0800
if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
    raise ctypes.WinError(ctypes.get_last_error())
if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
    raise ctypes.WinError(ctypes.get_last_error())
spec = importlib.util.spec_from_file_location("synthetic_wnp_launcher", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
root = pathlib.Path(sys.argv[2])
child = module.spawn_detached_python([str(root / "child.py"), str(os.getpid()), str(root)], root / "child.stdout", root / "child.stderr", cwd=root)
deadline = time.monotonic() + 5
while not (root / "ready.json").exists() and time.monotonic() < deadline:
    time.sleep(.02)
if not (root / "ready.json").exists():
    raise RuntimeError("Synthetic child did not become ready")
print(json.dumps({"child_pid": child.pid, "parent_pid": os.getpid(), "job_flags": limits.BasicLimitInformation.LimitFlags}), flush=True)
# Process termination closes this sole, non-inherited KILL_ON_JOB_CLOSE handle.
# An accidentally inherited-job child would be killed now.
os._exit(0)
'''
        child_source = r'''
import ctypes, json, os, pathlib, sys, time
from ctypes import wintypes
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.WaitForSingleObject.restype = wintypes.DWORD
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
kernel.IsProcessInJob.restype = wintypes.BOOL
root = pathlib.Path(sys.argv[2])
parent = kernel.OpenProcess(0x00100000, False, int(sys.argv[1]))
in_job = wintypes.BOOL()
if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(in_job)):
    raise ctypes.WinError(ctypes.get_last_error())
(root / "ready.json").write_text(json.dumps({"pid": os.getpid(), "in_job": bool(in_job.value)}))
if parent and kernel.WaitForSingleObject(parent, 8000) != 0:
    raise RuntimeError("Synthetic parent did not exit within the bound")
for index in range(3):
    with (root / "heartbeats.jsonl").open("a") as stream:
        stream.write(json.dumps({"index": index, "after_parent_exit": True, "in_job": bool(in_job.value)}) + "\n")
    time.sleep(.05)
(root / "done").write_text("bounded child stopped")
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "parent.py"
            parent.write_text(parent_source)
            (root / "child.py").write_text(child_source)
            result = subprocess.run(
                [str(self.cli.stable_python()), "-B", str(parent), str(REPO / "tools/hayale-wnp.py"), str(root)],
                cwd=root, stdin=subprocess.DEVNULL, capture_output=True, timeout=20, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["job_flags"], 0x2800)
            deadline = time.monotonic() + 5
            while not (root / "done").exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((root / "done").exists(), "Bounded child did not survive parent job closure")
            rows = [json.loads(line) for line in (root / "heartbeats.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row["after_parent_exit"] and not row["in_job"] for row in rows))
            # Prove self-exit and released handles before the temporary tree cleans up.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, receipt["child_pid"])
            if handle:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0, "Bounded fixture did not stop itself")
                finally:
                    kernel.CloseHandle(handle)
            else:
                self.assertEqual(ctypes.get_last_error(), 87, "Could not establish fixture exit")

    def test_unchanged_master_api_requires_token_and_refuses_mutations_without_binding_a_socket(self):
        from app.local_video.wnp_projection import load_wnp_kit
        load_wnp_kit()
        import wnp_host.api as api

        store = Mock()
        store.snapshot.return_value = {"protocol": "wnp/1", "state": "waiting"}
        credential = "synthetic-test-credential-32-characters"
        with patch.object(api, "BoundedServer") as server_factory:
            api.make_server(store, credential, port=9761)
        handler = server_factory.call_args.args[1]

        def response(method, authorized):
            raw = f"{method} /v1/status HTTP/1.0\r\n".encode()
            if authorized:
                raw += f"Authorization: Bearer {credential}\r\n".encode()
            request = Mock()
            request.makefile.return_value = io.BytesIO(raw + b"\r\n")
            sent = []
            request.sendall.side_effect = sent.append
            handler(request, ("127.0.0.1", 12345), Mock())
            return b"".join(sent)

        self.assertIn(b" 401 ", response("GET", False))
        self.assertIn(b" 200 ", response("GET", True))
        self.assertIn(b" 405 ", response("POST", True))
        store.append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
