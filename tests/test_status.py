import json
import sys
import tempfile
import unittest
from pathlib import Path

APP_PATH = Path(__file__).parents[1] / "nextcloud_sync" / "rootfs" / "app"
sys.path.insert(0, str(APP_PATH))

from config import SyncJob
from status import StatusStore


class StatusStoreTest(unittest.TestCase):
    def test_status_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            job = SyncJob("test", "Test", True, directory, "/Test", 300, 0, 0, [])
            store = StatusStore(directory / "status")
            store.ensure(job)
            store.update(
                "test",
                state="idle",
                last_success="2026-01-01T00:00:00+00:00",
                acknowledged_issue_id="issue-id",
                acknowledged_at="2026-01-02T00:00:00+00:00",
                last_acknowledged_issue="connection failed",
            )

            restored = StatusStore(directory / "status").ensure(job)

            self.assertEqual(restored["state"], "idle")
            self.assertEqual(restored["last_success"], "2026-01-01T00:00:00+00:00")
            self.assertEqual(restored["acknowledged_issue_id"], "issue-id")
            self.assertEqual(restored["last_acknowledged_issue"], "connection failed")
            parsed = json.loads((directory / "status" / "test.json").read_text(encoding="utf-8"))
            self.assertEqual(parsed["job_id"], "test")


if __name__ == "__main__":
    unittest.main()
