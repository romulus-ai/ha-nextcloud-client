import logging
import os
import time
from pathlib import Path

from config import DaemonConfig, SyncJob, load_config
from sync import SyncRunner, list_local_state


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("nextcloud-daemon")


class NextcloudDaemon:
    def __init__(self, options_path: str = "/data/options.json") -> None:
        self._options_path = Path(options_path)
        self._config: DaemonConfig = load_config(str(self._options_path))
        self._runner = SyncRunner(self._config)
        self._next_run = {job.name: 0.0 for job in self._config.syncs}
        self._last_state = {
            job.name: list_local_state(job.local, job.exclude)
            for job in self._config.syncs
            if job.direction in {"upload", "bidirectional"}
        }
        self._config_mtime = self._safe_mtime(self._options_path)

    def run_forever(self) -> None:
        LOGGER.info("daemon started with %s sync job(s)", len(self._config.syncs))
        while True:
            self._reload_if_needed()
            now = time.time()

            for job in self._config.syncs:
                if self._has_local_changes(job):
                    self._next_run[job.name] = min(self._next_run.get(job.name, now), now)

                if now >= self._next_run.get(job.name, 0):
                    self._run_job(job, now)

            time.sleep(5)

    def _run_job(self, job: SyncJob, now: float) -> None:
        try:
            self._runner.run_job(job)
        except Exception as err:  # pylint: disable=broad-except
            LOGGER.error("[%s] sync error: %s", job.name, err)
            self._next_run[job.name] = now + min(job.interval, 30)
            return

        self._next_run[job.name] = now + job.interval
        if job.direction in {"upload", "bidirectional"}:
            self._last_state[job.name] = list_local_state(job.local, job.exclude)

    def _has_local_changes(self, job: SyncJob) -> bool:
        if job.direction not in {"upload", "bidirectional"}:
            return False
        previous = self._last_state.get(job.name, {})
        current = list_local_state(job.local, job.exclude)
        changed = current != previous
        if changed:
            self._last_state[job.name] = current
        return changed

    def _reload_if_needed(self) -> None:
        current_mtime = self._safe_mtime(self._options_path)
        if current_mtime <= self._config_mtime:
            return

        LOGGER.info("configuration changed, reloading")
        self._config = load_config(str(self._options_path))
        self._runner = SyncRunner(self._config)
        self._next_run = {job.name: 0.0 for job in self._config.syncs}
        self._last_state = {
            job.name: list_local_state(job.local, job.exclude)
            for job in self._config.syncs
            if job.direction in {"upload", "bidirectional"}
        }
        self._config_mtime = current_mtime

    @staticmethod
    def _safe_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except FileNotFoundError:
            return 0.0


if __name__ == "__main__":
    options_path = os.environ.get("OPTIONS_PATH", "/data/options.json")
    daemon = NextcloudDaemon(options_path=options_path)
    daemon.run_forever()
