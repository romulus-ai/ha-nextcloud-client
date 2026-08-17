import json
import sys
import tempfile
import unittest
from pathlib import Path

APP_PATH = Path(__file__).parents[1] / "nextcloud_sync" / "rootfs" / "app"
sys.path.insert(0, str(APP_PATH))

from config import SyncJob
from mqtt_status import MqttStatusPublisher


class FakeMqttClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, bool]] = []

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self.messages.append((topic, payload, retain))


class MqttStatusPublisherTest(unittest.TestCase):
    def test_old_paho_callback_publishes_discovery_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = SyncJob("backup", "Backup", True, root, "/Backup", 300, 0, 0, [])
            state = {
                "job_id": "backup",
                "state": "error",
                "last_success": None,
                "consecutive_failures": 1,
            }
            publisher = MqttStatusPublisher(
                enabled=True,
                discovery_prefix="homeassistant",
                known_jobs_path=root / "mqtt_jobs.json",
            )
            client = FakeMqttClient()
            publisher._client = client
            publisher._jobs = {job.id: job}
            publisher._states = {job.id: state}

            publisher._on_connect(client, None, None, 0)

            topics = [message[0] for message in client.messages]
            self.assertIn("nextcloud_sync/availability", topics)
            self.assertIn("nextcloud_sync/backup/state", topics)
            self.assertEqual(sum(topic.endswith("/config") for topic in topics), 4)
            problem = next(
                json.loads(payload)
                for topic, payload, _retain in client.messages
                if "binary_sensor" in topic
            )
            self.assertEqual(problem["device_class"], "problem")
            self.assertEqual(problem["unique_id"], "nextcloud_sync_backup_problem")

    def test_disabled_mqtt_removes_previous_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            known_jobs = root / "mqtt_jobs.json"
            known_jobs.write_text(
                json.dumps({"jobs": ["old"], "prefix": "homeassistant"}),
                encoding="utf-8",
            )
            publisher = MqttStatusPublisher(False, "homeassistant", known_jobs)
            client = FakeMqttClient()
            publisher._client = client

            publisher._on_connect(client, None, None, 0)

            removed = [topic for topic, payload, _retain in client.messages if payload == ""]
            self.assertEqual(len(removed), 4)
            stored = json.loads(known_jobs.read_text(encoding="utf-8"))
            self.assertEqual(stored["jobs"], [])


if __name__ == "__main__":
    unittest.main()
