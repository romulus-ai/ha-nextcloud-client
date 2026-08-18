import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

APP_PATH = Path(__file__).parents[1] / "nextcloud_sync" / "rootfs" / "app"
sys.path.insert(0, str(APP_PATH))

from config import SyncJob
from daemon import NextcloudDaemon, _issue_fingerprint
from status import StatusStore
from sync import SyncError, SyncWarning


class NextcloudDaemonTest(unittest.TestCase):
    def test_issue_fingerprint_ignores_log_metadata(self) -> None:
        first = SyncError(
            "08-18 10:00:00:123 propagation job(0xabc123) failed, "
            "trying again in 20 hour(s)"
        )
        repeated = SyncError(
            "08-18 11:00:00:456 propagation job(0xdef456) failed, "
            "trying again in 19 hour(s)"
        )

        self.assertEqual(_issue_fingerprint(first), _issue_fingerprint(repeated))

    def test_reports_blacklisted_file_as_warning_at_regular_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = SyncJob("backup", "Backup", True, root, "/Backup", 300, 0, 0, [])
            warning = SyncWarning(
                "temporarily blocked",
                exit_code=4,
                retry_after_seconds=20 * 60 * 60,
            )
            daemon = NextcloudDaemon.__new__(NextcloudDaemon)
            daemon._runner = Mock()
            daemon._runner.run_job.side_effect = warning
            daemon._statuses = StatusStore(root / "status")
            daemon._statuses.ensure(job)
            daemon._statuses.update(job.id, consecutive_failures=2)
            daemon._mqtt = Mock()
            daemon._issue_lock = threading.RLock()
            now = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)

            with (
                self.assertLogs("nextcloud-daemon", level="WARNING") as logs,
                patch("daemon.datetime") as mocked_datetime,
                patch("daemon.time.monotonic", side_effect=[10.0, 11.0]),
            ):
                mocked_datetime.now.return_value = now
                daemon._run_job(job)

            state = daemon._statuses.get(job.id)
            self.assertEqual(state["state"], "warning")
            self.assertEqual(state["exit_code"], 4)
            self.assertEqual(state["consecutive_failures"], 2)
            self.assertEqual(state["last_error"], "temporarily blocked")
            self.assertIsNotNone(state["active_issue_id"])
            self.assertEqual(
                state["next_run"],
                (now + timedelta(seconds=job.interval)).isoformat(),
            )
            self.assertIn(
                "estimated file retry at 2026-08-19T06:00:00+00:00",
                "\n".join(logs.output),
            )
            published_state = daemon._mqtt.publish_state.call_args_list[-1].args[1]
            self.assertEqual(published_state["state"], "warning")

    def test_acknowledges_and_ignores_repeated_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = SyncJob("backup", "Backup", True, root, "/Backup", 300, 0, 0, [])
            daemon = NextcloudDaemon.__new__(NextcloudDaemon)
            daemon._runner = Mock()
            daemon._runner.run_job.side_effect = SyncWarning(
                "blocked file, trying again in 20 hour(s)",
                exit_code=4,
                retry_after_seconds=20 * 60 * 60,
            )
            daemon._statuses = StatusStore(root / "status")
            daemon._statuses.ensure(job)
            daemon._mqtt = Mock()
            daemon._issue_lock = threading.RLock()
            now = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)

            with (
                patch("daemon.datetime") as mocked_datetime,
                patch("daemon.time.monotonic", side_effect=[10.0, 11.0]),
            ):
                mocked_datetime.now.return_value = now
                daemon._run_job(job)

            first_issue_id = daemon._statuses.get(job.id)["active_issue_id"]
            daemon._acknowledge_issue(job.id)
            acknowledged = daemon._statuses.get(job.id)
            self.assertEqual(acknowledged["state"], "idle")
            self.assertIsNone(acknowledged["last_error"])
            self.assertEqual(acknowledged["acknowledged_issue_id"], first_issue_id)
            self.assertEqual(
                acknowledged["last_acknowledged_issue"],
                "blocked file, trying again in 20 hour(s)",
            )

            daemon._runner.run_job.side_effect = SyncWarning(
                "blocked file, trying again in 19 hour(s)",
                exit_code=4,
                retry_after_seconds=19 * 60 * 60,
            )
            with (
                self.assertNoLogs("nextcloud-daemon", level="WARNING"),
                patch("daemon.datetime") as mocked_datetime,
                patch("daemon.time.monotonic", side_effect=[20.0, 21.0]),
            ):
                mocked_datetime.now.return_value = now
                daemon._run_job(job)

            repeated = daemon._statuses.get(job.id)
            self.assertEqual(repeated["state"], "idle")
            self.assertIsNone(repeated["last_error"])
            self.assertEqual(repeated["acknowledged_issue_id"], first_issue_id)

            daemon._runner.run_job.side_effect = None
            with (
                patch("daemon.datetime") as mocked_datetime,
                patch("daemon.time.monotonic", side_effect=[30.0, 31.0]),
            ):
                mocked_datetime.now.return_value = now
                daemon._run_job(job)

            self.assertIsNone(daemon._statuses.get(job.id)["acknowledged_issue_id"])

    def test_acknowledges_error_until_a_different_error_occurs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = SyncJob("backup", "Backup", True, root, "/Backup", 300, 0, 0, [])
            daemon = NextcloudDaemon.__new__(NextcloudDaemon)
            daemon._runner = Mock()
            daemon._runner.run_job.side_effect = SyncError("connection failed", exit_code=5)
            daemon._statuses = StatusStore(root / "status")
            daemon._statuses.ensure(job)
            daemon._mqtt = Mock()
            daemon._issue_lock = threading.RLock()

            with self.assertLogs("nextcloud-daemon", level="ERROR"):
                daemon._run_job(job)
            self.assertEqual(daemon._statuses.get(job.id)["consecutive_failures"], 1)

            daemon._acknowledge_issue(job.id)
            acknowledged = daemon._statuses.get(job.id)
            self.assertEqual(acknowledged["state"], "idle")
            self.assertEqual(acknowledged["consecutive_failures"], 1)

            with self.assertNoLogs("nextcloud-daemon", level="WARNING"):
                daemon._run_job(job)
            repeated = daemon._statuses.get(job.id)
            self.assertEqual(repeated["state"], "idle")
            self.assertEqual(repeated["consecutive_failures"], 1)

            daemon._runner.run_job.side_effect = SyncError("permission denied", exit_code=6)
            with self.assertLogs("nextcloud-daemon", level="ERROR"):
                daemon._run_job(job)
            different = daemon._statuses.get(job.id)
            self.assertEqual(different["state"], "error")
            self.assertEqual(different["consecutive_failures"], 2)
            self.assertIsNone(different["acknowledged_issue_id"])

    def test_acknowledges_warning_saved_before_issue_ids_existed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = SyncJob("backup", "Backup", True, root, "/Backup", 300, 0, 0, [])
            daemon = NextcloudDaemon.__new__(NextcloudDaemon)
            daemon._statuses = StatusStore(root / "status")
            daemon._statuses.ensure(job)
            daemon._statuses.update(
                job.id,
                state="warning",
                last_error="legacy warning",
            )
            daemon._mqtt = Mock()
            daemon._issue_lock = threading.RLock()

            daemon._acknowledge_issue(job.id)

            state = daemon._statuses.get(job.id)
            self.assertEqual(state["state"], "idle")
            self.assertIsNotNone(state["acknowledged_issue_id"])
            self.assertEqual(state["last_acknowledged_issue"], "legacy warning")


if __name__ == "__main__":
    unittest.main()
