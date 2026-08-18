import io
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

APP_PATH = Path(__file__).parents[1] / "nextcloud_sync" / "rootfs" / "app"
sys.path.insert(0, str(APP_PATH))

from config import DaemonConfig, NextcloudConfig, SyncJob
from sync import SyncError, SyncRunner


class SyncRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.local = self.root / "local"
        self.local.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _config(self, max_retries: int = 0, timeout: int = 10) -> DaemonConfig:
        return DaemonConfig(
            nextcloud=NextcloudConfig("https://cloud.example.com", "ha", "top-secret"),
            sync_interval=300,
            timeout=timeout,
            max_retries=max_retries,
            max_parallel_jobs=2,
            mqtt_enabled=False,
            mqtt_discovery_prefix="homeassistant",
            syncs=[],
        )

    def _job(self) -> SyncJob:
        return SyncJob(
            id="test",
            name="Test",
            enabled=True,
            local=self.local,
            remote="/Test",
            interval=300,
            upload_limit=10,
            download_limit=20,
            exclude=["*.tmp"],
        )

    def _script(self, body: str) -> Path:
        path = self.root / "fake-nextcloudcmd"
        path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_passes_password_on_stdin_and_builds_expected_command(self) -> None:
        capture = self.root / "capture.json"
        executable = self._script(
            "python3 -c 'import json, os, sys; "
            "json.dump({\"args\": sys.argv[1:], \"stdin\": sys.stdin.read()}, "
            "open(os.environ[\"CAPTURE\"], \"w\"))' \"$@\"\n"
        )
        old_capture = os.environ.get("CAPTURE")
        os.environ["CAPTURE"] = str(capture)
        try:
            runner = SyncRunner(
                self._config(),
                threading.Event(),
                state_dir=self.root / "state",
                executable=str(executable),
            )
            runner.run_job(self._job())
        finally:
            if old_capture is None:
                os.environ.pop("CAPTURE", None)
            else:
                os.environ["CAPTURE"] = old_capture

        result = json.loads(capture.read_text(encoding="utf-8"))
        self.assertEqual(result["stdin"], "top-secret\n")
        self.assertIn("ha", result["args"])
        self.assertNotIn("top-secret", result["args"])
        self.assertIn("--path", result["args"])
        self.assertIn("--uplimit", result["args"])
        self.assertIn("--downlimit", result["args"])
        retry_option = result["args"].index("--max-sync-retries")
        self.assertEqual(result["args"][retry_option + 1], "3")

    def test_redacts_password_from_error(self) -> None:
        executable = self._script('printf "failure top-secret\\n"\nexit 4\n')
        runner = SyncRunner(
            self._config(),
            threading.Event(),
            state_dir=self.root / "state",
            executable=str(executable),
        )

        with self.assertRaises(SyncError) as raised:
            runner.run_job(self._job())

        self.assertEqual(raised.exception.exit_code, 4)
        self.assertNotIn("top-secret", str(raised.exception))
        self.assertIn("***", str(raised.exception))

    def test_extracts_blacklisted_file_and_retry_delay(self) -> None:
        warning = (
            '08-18 09:57:03:014 [ warning nextcloud.sync.propagator ]: '
            'Could not complete propagation of "Automatic_backup.tar" by '
            "OCC::PropagateIgnoreJob(0x1234) with status "
            "OCC::SyncFileItem::BlacklistedError and error: "
            '"Connection closed (skipped due to earlier error, trying again in '
            '20 hour(s))"'
        )
        executable = self._script(
            "printf '%05000d\\n' 0\n"
            f"printf '%s\\n' '{warning}'\n"
            "exit 4\n"
        )
        runner = SyncRunner(
            self._config(),
            threading.Event(),
            state_dir=self.root / "state",
            executable=str(executable),
        )

        with self.assertRaises(SyncError) as raised:
            runner.run_job(self._job())

        self.assertEqual(raised.exception.exit_code, 4)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.retry_after_seconds, 20 * 60 * 60)
        self.assertIn("Automatic_backup.tar", str(raised.exception))
        self.assertNotIn("0000000000", str(raised.exception))

    def test_does_not_retry_non_retryable_error(self) -> None:
        runner = SyncRunner(
            self._config(max_retries=2),
            threading.Event(),
            state_dir=self.root / "state",
        )
        error = SyncError("temporarily blocked", retryable=False)

        with (
            patch.object(runner, "_run_once", side_effect=error) as run_once,
            self.assertRaisesRegex(SyncError, "temporarily blocked"),
        ):
            runner.run_job(self._job())

        run_once.assert_called_once()

    def test_treats_help_output_as_failure_even_with_zero_exit_code(self) -> None:
        executable = self._script('printf "Usage: nextcloudcmd [OPTION] source server\\n"\n')
        runner = SyncRunner(
            self._config(),
            threading.Event(),
            state_dir=self.root / "state",
            executable=str(executable),
        )

        with self.assertRaisesRegex(SyncError, "rejected its command-line options"):
            runner.run_job(self._job())

    def test_stops_process_after_timeout(self) -> None:
        executable = self._script("sleep 30\n")
        runner = SyncRunner(
            self._config(timeout=1),
            threading.Event(),
            state_dir=self.root / "state",
            executable=str(executable),
        )

        started = time.monotonic()
        with self.assertRaisesRegex(SyncError, "timed out"):
            runner.run_job(self._job())

        self.assertLess(time.monotonic() - started, 5)

    def test_accepts_process_completion_during_final_timeout_wait(self) -> None:
        stop_event = Mock()
        stop_event.is_set.return_value = False
        stop_event.wait.return_value = False
        process = Mock()
        process.stdin = io.StringIO()
        process.stdout = io.StringIO()
        process.returncode = 0
        process.poll.side_effect = [None, 0]
        runner = SyncRunner(
            self._config(timeout=1),
            stop_event,
            state_dir=self.root / "state",
        )

        with (
            patch("sync.subprocess.Popen", return_value=process),
            patch("sync.time.monotonic", side_effect=[0.0, 1.0]),
        ):
            runner.run_job(self._job())

    def test_rejects_path_replaced_by_symlink(self) -> None:
        job = self._job()
        replacement = self.root / "replacement"
        replacement.mkdir()
        job.local.rmdir()
        job.local.symlink_to(replacement, target_is_directory=True)
        runner = SyncRunner(
            self._config(),
            threading.Event(),
            state_dir=self.root / "state",
            executable=str(self._script("exit 0\n")),
        )

        with self.assertRaisesRegex(SyncError, "symbolic link"):
            runner.run_job(job)


if __name__ == "__main__":
    unittest.main()
