import json
import sys
import tempfile
import unittest
from pathlib import Path


APP_PATH = Path(__file__).parents[1] / "nextcloud_sync" / "rootfs" / "app"
sys.path.insert(0, str(APP_PATH))

from config import load_config  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, syncs: list[dict[str, object]]) -> Path:
        options = {
            "nextcloud": {
                "url": "https://cloud.example.com",
                "username": "ha",
                "password": "secret",
            },
            "sync_interval": 300,
            "timeout": 600,
            "max_retries": 1,
            "max_parallel_jobs": 2,
            "mqtt_enabled": True,
            "mqtt_discovery_prefix": "homeassistant",
            "syncs": syncs,
        }
        path = self.root / "options.json"
        path.write_text(json.dumps(options), encoding="utf-8")
        return path

    def _job(self, job_id: str = "backups", local: str = "backups", remote: str = "/HA") -> dict[str, object]:
        return {
            "id": job_id,
            "name": job_id.title(),
            "enabled": True,
            "local": str(self.root / local),
            "remote": remote,
            "interval": 300,
            "upload_limit": 0,
            "download_limit": 0,
            "exclude": ["*.tmp"],
        }

    def test_loads_valid_config_and_creates_local_directory(self) -> None:
        config = load_config(str(self._write([self._job()])), allowed_roots=(self.root,))

        self.assertEqual(config.nextcloud.url, "https://cloud.example.com")
        self.assertEqual(config.syncs[0].id, "backups")
        self.assertTrue(config.syncs[0].local.is_dir())

    def test_rejects_duplicate_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate sync id"):
            load_config(
                str(self._write([self._job(), self._job(local="media", remote="/Media")])),
                allowed_roots=(self.root,),
            )

    def test_rejects_overlapping_local_paths(self) -> None:
        jobs = [self._job(), self._job("nested", "backups/nested", "/Nested")]
        with self.assertRaisesRegex(ValueError, "local path.*overlaps"):
            load_config(str(self._write(jobs)), allowed_roots=(self.root,))

    def test_rejects_path_outside_allowed_roots(self) -> None:
        job = self._job()
        job["local"] = "/tmp/not-allowed"
        with self.assertRaisesRegex(ValueError, "must be below"):
            load_config(str(self._write([job])), allowed_roots=(self.root,))

    def test_rejects_credentials_in_url(self) -> None:
        path = self._write([self._job()])
        options = json.loads(path.read_text(encoding="utf-8"))
        options["nextcloud"]["url"] = "https://user:pass@cloud.example.com"
        path.write_text(json.dumps(options), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "credentials must not"):
            load_config(str(path), allowed_roots=(self.root,))

    def test_rejects_unencrypted_server_url(self) -> None:
        path = self._write([self._job()])
        options = json.loads(path.read_text(encoding="utf-8"))
        options["nextcloud"]["url"] = "http://cloud.example.com"
        path.write_text(json.dumps(options), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "HTTPS URL"):
            load_config(str(path), allowed_roots=(self.root,))

    def test_rejects_nested_dav_endpoint(self) -> None:
        path = self._write([self._job()])
        options = json.loads(path.read_text(encoding="utf-8"))
        options["nextcloud"]["url"] = "https://cloud.example.com/remote.php/dav/files/ha"
        path.write_text(json.dumps(options), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "base URL"):
            load_config(str(path), allowed_roots=(self.root,))


if __name__ == "__main__":
    unittest.main()
