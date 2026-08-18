from __future__ import annotations

import hashlib
import logging
import os
import re
import signal
import threading
import time
from datetime import datetime, timedelta, timezone

from config import SyncJob, load_config
from mqtt_status import MqttStatusPublisher
from status import StatusStore
from sync import SyncCancelled, SyncError, SyncRunner, SyncWarning

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("nextcloud-daemon")
ISSUE_TIMESTAMP_PATTERN = re.compile(
    r"(?m)^(?:\d{4}-)?\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,:.]\d+)?\s*"
)
ISSUE_ADDRESS_PATTERN = re.compile(r"0x[0-9a-f]+", re.IGNORECASE)
ISSUE_RETRY_PATTERN = re.compile(
    r"trying again in \d+ (?:second|minute|hour|day)(?:\(s\)|s)?",
    re.IGNORECASE,
)


class NextcloudDaemon:
    def __init__(self, options_path: str = "/data/options.json") -> None:
        self._config = load_config(options_path)
        self._stop_event = threading.Event()
        self._runner = SyncRunner(self._config, self._stop_event)
        self._statuses = StatusStore()
        self._issue_lock = threading.RLock()
        self._mqtt = MqttStatusPublisher(
            enabled=self._config.mqtt_enabled,
            discovery_prefix=self._config.mqtt_discovery_prefix,
            acknowledge_issue=self._acknowledge_issue,
        )
        for job in self._config.syncs:
            self._statuses.ensure(job)
        self._workers: list[threading.Thread] = []
        self._job_slots = threading.Semaphore(self._config.max_parallel_jobs)
        self._worker_error: Exception | None = None

    def run_forever(self) -> None:
        self._install_signal_handlers()
        self._mqtt.start(self._config.syncs, self._statuses.all())
        LOGGER.info("daemon started with %s sync job(s)", len(self._config.syncs))
        try:
            self._workers = [
                threading.Thread(
                    target=self._run_job_forever,
                    args=(job,),
                    name=f"sync-{job.id}",
                )
                for job in self._config.syncs
                if job.enabled
            ]
            for worker in self._workers:
                worker.start()
            self._stop_event.wait()
        finally:
            self._stop_event.set()
            for worker in self._workers:
                worker.join(timeout=20)
            self._mqtt.stop()
            LOGGER.info("daemon stopped")
        if self._worker_error is not None:
            raise RuntimeError("a sync worker stopped unexpectedly") from self._worker_error

    def _run_job_forever(self, job: SyncJob) -> None:
        while not self._stop_event.is_set():
            try:
                with self._job_slots:
                    if self._stop_event.is_set():
                        break
                    self._run_job(job)
            except Exception as err:  # A dead worker must restart the container.
                LOGGER.exception("[%s] unexpected worker failure", job.name)
                self._worker_error = err
                self._stop_event.set()
                break
            if self._stop_event.wait(job.interval):
                break

    def _run_job(self, job: SyncJob) -> None:
        started_monotonic = time.monotonic()
        started_at = _utc_now()
        state = self._statuses.update(
            job.id,
            state="running",
            last_start=started_at,
            next_run=None,
            last_error=None,
        )
        self._mqtt.publish_state(job.id, state)

        try:
            self._runner.run_job(job)
        except SyncCancelled:
            duration = round(time.monotonic() - started_monotonic, 3)
            state = self._statuses.update(
                job.id,
                state="idle",
                duration_seconds=duration,
                exit_code=None,
                next_run=None,
                last_error="sync cancelled during shutdown",
            )
            self._mqtt.publish_state(job.id, state)
            return
        except SyncWarning as warning:
            duration = round(time.monotonic() - started_monotonic, 3)
            state = self._record_issue(job, warning, "warning", duration)
        except SyncError as err:
            duration = round(time.monotonic() - started_monotonic, 3)
            state = self._record_issue(job, err, "error", duration)
        else:
            duration = round(time.monotonic() - started_monotonic, 3)
            with self._issue_lock:
                state = self._statuses.update(
                    job.id,
                    state="success",
                    last_success=_utc_now(),
                    duration_seconds=duration,
                    exit_code=0,
                    consecutive_failures=0,
                    last_error=None,
                    active_issue_id=None,
                    active_issue_message=None,
                    acknowledged_issue_id=None,
                )

        state = self._statuses.update(
            job.id,
            next_run=(datetime.now(timezone.utc) + timedelta(seconds=job.interval)).isoformat(),
        )
        self._mqtt.publish_state(job.id, state)

    def _record_issue(
        self,
        job: SyncJob,
        issue: SyncError,
        severity: str,
        duration: float,
    ) -> dict[str, object]:
        issue_id = _issue_fingerprint(issue)
        with self._issue_lock:
            previous = self._statuses.get(job.id)
            if previous.get("acknowledged_issue_id") == issue_id:
                state = self._statuses.update(
                    job.id,
                    state="idle",
                    duration_seconds=duration,
                    exit_code=None,
                    last_error=None,
                    active_issue_id=None,
                    active_issue_message=None,
                )
                LOGGER.debug("[%s] ignored acknowledged sync issue", job.name)
                return state

            values: dict[str, object] = {
                "state": severity,
                "duration_seconds": duration,
                "exit_code": issue.exit_code,
                "last_error": str(issue),
                "active_issue_id": issue_id,
                "active_issue_message": str(issue),
                "acknowledged_issue_id": None,
            }
            if severity == "error":
                values["consecutive_failures"] = (
                    int(previous.get("consecutive_failures", 0)) + 1
                )
            state = self._statuses.update(job.id, **values)

        if isinstance(issue, SyncWarning):
            retry_time = ""
            if issue.retry_after_seconds is not None:
                estimated_retry = datetime.now(timezone.utc) + timedelta(
                    seconds=issue.retry_after_seconds
                )
                retry_time = (
                    f"; estimated file retry at {estimated_retry.isoformat(timespec='seconds')}"
                )
            LOGGER.warning("[%s] sync warning: %s%s", job.name, issue, retry_time)
        else:
            LOGGER.error("[%s] sync failed: %s", job.name, issue)
        return state

    def _acknowledge_issue(self, job_id: str) -> None:
        with self._issue_lock:
            try:
                previous = self._statuses.get(job_id)
            except KeyError:
                return
            issue_id = previous.get("active_issue_id")
            issue_message = previous.get("active_issue_message") or previous.get("last_error")
            current_state = str(previous.get("state", "idle"))
            if not issue_id and issue_message and current_state in {"warning", "error"}:
                issue = (
                    SyncWarning(str(issue_message))
                    if current_state == "warning"
                    else SyncError(str(issue_message))
                )
                issue_id = _issue_fingerprint(issue)
            if not issue_id:
                return

            state = self._statuses.update(
                job_id,
                state="idle" if current_state in {"warning", "error"} else current_state,
                exit_code=None,
                last_error=None,
                active_issue_id=None,
                active_issue_message=None,
                acknowledged_issue_id=issue_id,
                acknowledged_at=_utc_now(),
                last_acknowledged_issue=issue_message,
            )

        self._mqtt.publish_state(job_id, state)
        LOGGER.info("[%s] sync issue acknowledged", previous.get("name", job_id))

    def _install_signal_handlers(self) -> None:
        def stop(_signum: int, _frame: object) -> None:
            LOGGER.info("shutdown requested")
            self._stop_event.set()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _issue_fingerprint(issue: SyncError) -> str:
    normalized = ISSUE_TIMESTAMP_PATTERN.sub("", str(issue))
    normalized = ISSUE_ADDRESS_PATTERN.sub("0x*", normalized)
    normalized = ISSUE_RETRY_PATTERN.sub("trying again later", normalized)
    normalized = "\n".join(line.strip() for line in normalized.splitlines())
    source = f"{type(issue).__name__}\0{normalized}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    options_path = os.environ.get("OPTIONS_PATH", "/data/options.json")
    try:
        daemon = NextcloudDaemon(options_path=options_path)
        daemon.run_forever()
    except (OSError, RuntimeError, ValueError) as err:
        LOGGER.critical("fatal error: %s", err)
        raise SystemExit(1) from err
