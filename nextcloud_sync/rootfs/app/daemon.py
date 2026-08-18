from __future__ import annotations

import logging
import os
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


class NextcloudDaemon:
    def __init__(self, options_path: str = "/data/options.json") -> None:
        self._config = load_config(options_path)
        self._stop_event = threading.Event()
        self._runner = SyncRunner(self._config, self._stop_event)
        self._statuses = StatusStore()
        self._mqtt = MqttStatusPublisher(
            enabled=self._config.mqtt_enabled,
            discovery_prefix=self._config.mqtt_discovery_prefix,
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
            state = self._statuses.update(
                job.id,
                state="warning",
                duration_seconds=duration,
                exit_code=warning.exit_code,
                last_error=str(warning),
            )
            retry_time = ""
            if warning.retry_after_seconds is not None:
                estimated_retry = datetime.now(timezone.utc) + timedelta(
                    seconds=warning.retry_after_seconds
                )
                retry_time = (
                    f"; estimated file retry at {estimated_retry.isoformat(timespec='seconds')}"
                )
            LOGGER.warning("[%s] sync warning: %s%s", job.name, warning, retry_time)
        except SyncError as err:
            duration = round(time.monotonic() - started_monotonic, 3)
            previous = self._statuses.get(job.id)
            state = self._statuses.update(
                job.id,
                state="error",
                duration_seconds=duration,
                exit_code=err.exit_code,
                consecutive_failures=int(previous.get("consecutive_failures", 0)) + 1,
                last_error=str(err),
            )
            LOGGER.error("[%s] sync failed: %s", job.name, err)
        else:
            duration = round(time.monotonic() - started_monotonic, 3)
            state = self._statuses.update(
                job.id,
                state="success",
                last_success=_utc_now(),
                duration_seconds=duration,
                exit_code=0,
                consecutive_failures=0,
                last_error=None,
            )

        state = self._statuses.update(
            job.id,
            next_run=(datetime.now(timezone.utc) + timedelta(seconds=job.interval)).isoformat(),
        )
        self._mqtt.publish_state(job.id, state)

    def _install_signal_handlers(self) -> None:
        def stop(_signum: int, _frame: object) -> None:
            LOGGER.info("shutdown requested")
            self._stop_event.set()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    options_path = os.environ.get("OPTIONS_PATH", "/data/options.json")
    try:
        daemon = NextcloudDaemon(options_path=options_path)
        daemon.run_forever()
    except (OSError, RuntimeError, ValueError) as err:
        LOGGER.critical("fatal error: %s", err)
        raise SystemExit(1) from err
