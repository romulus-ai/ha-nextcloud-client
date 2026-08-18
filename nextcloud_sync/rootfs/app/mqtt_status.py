from __future__ import annotations

import json
import logging
import os
import ssl
import threading
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from config import SyncJob

LOGGER = logging.getLogger("nextcloud-mqtt")
BASE_TOPIC = "nextcloud_sync"
COMPONENTS = ("status", "problem", "last_success", "consecutive_failures")


class MqttStatusPublisher:
    def __init__(
        self,
        enabled: bool,
        discovery_prefix: str,
        known_jobs_path: Path = Path("/data/mqtt_jobs.json"),
    ) -> None:
        self._enabled = enabled
        self._discovery_prefix = discovery_prefix
        self._known_jobs_path = known_jobs_path
        self._client: Any = None
        self._jobs: dict[str, SyncJob] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._connected = threading.Event()

    def start(self, jobs: list[SyncJob], states: dict[str, dict[str, Any]]) -> None:
        self._jobs = {job.id: job for job in jobs}
        self._states = states.copy()
        if not self._enabled:
            LOGGER.info("MQTT status publishing is disabled; removing retained discovery entries")

        try:
            service = _load_mqtt_service()
            from paho.mqtt import client as mqtt

            try:
                client = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION2,
                    client_id="nextcloud-sync-addon",
                )
            except (AttributeError, TypeError):
                client = mqtt.Client(client_id="nextcloud-sync-addon")

            client.username_pw_set(service.get("username"), service.get("password"))
            client.will_set(f"{BASE_TOPIC}/availability", "offline", retain=True)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect

            if service.get("ssl"):
                client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

            self._client = client
            client.reconnect_delay_set(min_delay=1, max_delay=60)
            client.connect_async(str(service["host"]), int(service.get("port", 1883)), 60)
            client.loop_start()
            if not self._connected.wait(timeout=10):
                LOGGER.warning("MQTT connection timed out; sensors will be unavailable")
        except Exception as err:  # noqa: BLE001 - MQTT is optional.
            LOGGER.warning("MQTT is unavailable: %s", err)
            self._client = None

    def stop(self) -> None:
        if self._client is None:
            return
        if self._connected.is_set():
            self._client.publish(f"{BASE_TOPIC}/availability", "offline", retain=True)
        self._client.disconnect()
        self._client.loop_stop()
        self._connected.clear()

    def publish_state(self, job_id: str, state: dict[str, Any]) -> None:
        self._states[job_id] = state.copy()
        if not self._enabled or self._client is None or not self._connected.is_set():
            return
        self._publish_json(f"{BASE_TOPIC}/{job_id}/state", state, retain=True)

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, reason_code: Any, *_args: Any) -> None:
        if reason_code != 0:
            LOGGER.warning("MQTT connection failed with reason %s", reason_code)
            return
        self._connected.set()
        if not self._enabled:
            self._remove_stale_discovery(remove_all=True)
            self._write_known_jobs([])
            client.publish(f"{BASE_TOPIC}/availability", "offline", retain=True)
            LOGGER.info("retained MQTT discovery entries removed")
            return

        client.publish(f"{BASE_TOPIC}/availability", "online", retain=True)
        self._remove_stale_discovery()
        for job in self._jobs.values():
            self._publish_discovery(job)
            state = self._states.get(job.id)
            if state:
                self._publish_json(f"{BASE_TOPIC}/{job.id}/state", state, retain=True)
        self._write_known_jobs()
        LOGGER.info("MQTT status sensors published")

    def _on_disconnect(self, _client: Any, _userdata: Any, *_args: Any) -> None:
        self._connected.clear()

    def _publish_discovery(self, job: SyncJob) -> None:
        state_topic = f"{BASE_TOPIC}/{job.id}/state"
        availability_topic = f"{BASE_TOPIC}/availability"
        device = {
            "identifiers": [f"nextcloud_sync_{job.id}"],
            "name": f"Nextcloud Sync {job.name}",
            "manufacturer": "romulus-ai",
            "model": "Nextcloud Sync Job",
            "sw_version": os.environ.get("BUILD_VERSION", "0.1.2"),
        }
        origin = {
            "name": "Nextcloud Sync add-on",
            "support_url": "https://github.com/romulus-ai/ha-nextcloud-client/issues",
        }
        common = {
            "availability_topic": availability_topic,
            "device": device,
            "origin": origin,
            "state_topic": state_topic,
        }

        payloads = {
            "status": {
                **common,
                "name": "Status",
                "unique_id": f"nextcloud_sync_{job.id}_status",
                "icon": "mdi:cloud-sync",
                "value_template": (
                    "{{ 'Problem' if value_json.state == 'error' else "
                    "'warning' if value_json.state == 'warning' else 'OK' }}"
                ),
                "json_attributes_topic": state_topic,
            },
            "problem": {
                **common,
                "name": "Problem",
                "unique_id": f"nextcloud_sync_{job.id}_problem",
                "device_class": "problem",
                "value_template": "{{ 'ON' if value_json.state == 'error' else 'OFF' }}",
            },
            "last_success": {
                **common,
                "name": "Last successful sync",
                "unique_id": f"nextcloud_sync_{job.id}_last_success",
                "device_class": "timestamp",
                "entity_category": "diagnostic",
                "value_template": "{{ value_json.last_success or '' }}",
            },
            "consecutive_failures": {
                **common,
                "name": "Consecutive failures",
                "unique_id": f"nextcloud_sync_{job.id}_consecutive_failures",
                "entity_category": "diagnostic",
                "icon": "mdi:alert-circle-outline",
                "value_template": "{{ value_json.consecutive_failures }}",
            },
        }

        for component, payload in payloads.items():
            domain = "binary_sensor" if component == "problem" else "sensor"
            topic = f"{self._discovery_prefix}/{domain}/nextcloud_sync_{job.id}_{component}/config"
            self._publish_json(topic, payload, retain=True)

    def _remove_stale_discovery(self, remove_all: bool = False) -> None:
        try:
            stored = json.loads(self._known_jobs_path.read_text(encoding="utf-8"))
            if isinstance(stored, list):
                previous_prefix = self._discovery_prefix
                previous_jobs = set(stored)
            else:
                previous_prefix = str(stored["prefix"])
                previous_jobs = set(stored["jobs"])
        except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError, TypeError):
            previous_prefix = self._discovery_prefix
            previous_jobs = set()

        stale_jobs = (
            previous_jobs
            if remove_all or previous_prefix != self._discovery_prefix
            else previous_jobs - self._jobs.keys()
        )
        for job_id in stale_jobs:
            for component in COMPONENTS:
                domain = "binary_sensor" if component == "problem" else "sensor"
                topic = f"{previous_prefix}/{domain}/nextcloud_sync_{job_id}_{component}/config"
                self._client.publish(topic, "", retain=True)

    def _write_known_jobs(self, job_ids: list[str] | None = None) -> None:
        jobs = sorted(self._jobs) if job_ids is None else sorted(job_ids)
        temporary = self._known_jobs_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"jobs": jobs, "prefix": self._discovery_prefix}) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self._known_jobs_path)

    def _publish_json(self, topic: str, payload: dict[str, Any], retain: bool) -> None:
        self._client.publish(
            topic,
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            retain=retain,
        )


def _load_mqtt_service() -> dict[str, Any]:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is not available")
    request = Request(
        "http://supervisor/services/mqtt",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(request, timeout=10) as response:
        payload = json.load(response)
    if payload.get("result") != "ok" or not payload.get("data", {}).get("host"):
        raise RuntimeError("no MQTT service is configured")
    return payload["data"]
