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
from sync import SyncError


class NextcloudDaemonTest(unittest.TestCase):
    def test_defers_next_run_for_blacklisted_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = SyncJob("backup", "Backup", True, root, "/Backup", 300, 0, 0, [])
            error = SyncError(
                "temporarily blocked",
                exit_code=4,
                retryable=False,
                retry_after_seconds=20 * 60 * 60,
            )
            daemon = NextcloudDaemon.__new__(NextcloudDaemon)
            daemon._runner = Mock()
            daemon._runner.run_job.side_effect = error
            daemon._statuses = StatusStore(root / "status")
            daemon._statuses.ensure(job)
            daemon._mqtt = Mock()
            now = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)

            with (
                patch("daemon.datetime") as mocked_datetime,
                patch("daemon.time.monotonic", side_effect=[10.0, 11.0]),
            ):
                mocked_datetime.now.return_value = now
                delay = daemon._run_job(job)

            state = daemon._statuses.get(job.id)
            self.assertEqual(delay, 20 * 60 * 60)
            self.assertEqual(state["state"], "error")
            self.assertEqual(state["exit_code"], 4)
            self.assertEqual(state["last_error"], "temporarily blocked")
            self.assertEqual(
                state["next_run"],
                (now + timedelta(hours=20)).isoformat(),
            )


if __name__ == "__main__":
    unittest.main()
