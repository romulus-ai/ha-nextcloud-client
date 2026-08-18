import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

APP_PATH = Path(__file__).parents[1] / "nextcloud_sync" / "rootfs" / "app"
sys.path.insert(0, str(APP_PATH))

from config import SyncJob
from daemon import NextcloudDaemon
from status import StatusStore
from sync import SyncWarning


class NextcloudDaemonTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
