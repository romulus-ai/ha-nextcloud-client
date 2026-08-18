import json
import threading
from pathlib import Path
from typing import Any

from config import SyncJob


class StatusStore:
    def __init__(self, directory: Path = Path("/data/status")) -> None:
        self._directory = directory
        self._directory.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def ensure(self, job: SyncJob) -> dict[str, Any]:
        with self._lock:
            if job.id in self._states:
                return self._states[job.id].copy()

            path = self._path(job.id)
            state: dict[str, Any] = {}
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                pass

            state.update(
                {
                    "job_id": job.id,
                    "name": job.name,
                    "local": str(job.local),
                    "remote": job.remote,
                    "state": "disabled" if not job.enabled else state.get("state", "idle"),
                    "last_start": state.get("last_start"),
                    "last_success": state.get("last_success"),
                    "duration_seconds": state.get("duration_seconds"),
                    "exit_code": state.get("exit_code"),
                    "consecutive_failures": state.get("consecutive_failures", 0),
                    "next_run": None,
                    "last_error": state.get("last_error"),
                    "active_issue_id": state.get("active_issue_id"),
                    "active_issue_message": state.get("active_issue_message"),
                    "acknowledged_issue_id": state.get("acknowledged_issue_id"),
                    "acknowledged_at": state.get("acknowledged_at"),
                    "last_acknowledged_issue": state.get("last_acknowledged_issue"),
                }
            )
            self._states[job.id] = state
            self.write(job.id)
            return state.copy()

    def update(self, job_id: str, **values: Any) -> dict[str, Any]:
        with self._lock:
            state = self._states[job_id]
            state.update(values)
            self.write(job_id)
            return state.copy()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._states[job_id].copy()

    def all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {job_id: state.copy() for job_id, state in self._states.items()}

    def write(self, job_id: str) -> None:
        with self._lock:
            path = self._path(job_id)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(self._states[job_id], ensure_ascii=True, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)

    def _path(self, job_id: str) -> Path:
        return self._directory / f"{job_id}.json"
