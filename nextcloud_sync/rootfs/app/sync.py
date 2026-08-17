from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from config import DaemonConfig, SyncJob


LOGGER = logging.getLogger("nextcloud-sync")


MAX_ERROR_LENGTH = 4000


class SyncError(RuntimeError):
    def __init__(self, message: str, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class SyncCancelled(SyncError):
    pass


class SyncRunner:
    def __init__(
        self,
        config: DaemonConfig,
        stop_event: threading.Event,
        state_dir: Path = Path("/data/jobs"),
        executable: str = "nextcloudcmd",
    ) -> None:
        self._config = config
        self._stop_event = stop_event
        self._state_dir = state_dir
        self._executable = executable
        self._state_dir.mkdir(parents=True, exist_ok=True)

    def run_job(self, job: SyncJob) -> None:
        job_state = self._state_dir / job.id
        job_state.mkdir(parents=True, exist_ok=True)
        attempts = self._config.max_retries + 1
        last_error: SyncError | None = None
        for attempt in range(1, attempts + 1):
            if self._stop_event.is_set():
                raise SyncCancelled("sync cancelled during shutdown")
            try:
                self._run_once(job, job_state)
                return
            except SyncCancelled:
                raise
            except SyncError as err:
                last_error = err
                LOGGER.warning("[%s] attempt %s/%s failed: %s", job.name, attempt, attempts, err)
                if attempt < attempts:
                    if self._stop_event.wait(min(60, 5 * (2 ** (attempt - 1)))):
                        raise SyncCancelled("sync cancelled during shutdown") from err

        assert last_error is not None
        raise last_error

    def _run_once(self, job: SyncJob, job_state: Path) -> None:
        _validate_job_path(job)
        cmd = [
            self._executable,
            "--max-sync-retries",
            "3",
            "--user",
            self._config.nextcloud.username,
            "--path",
            job.remote,
        ]

        exclude_file = _write_exclude_file(job.exclude, job_state)
        if exclude_file:
            cmd.extend(["--exclude", str(exclude_file)])
        if job.upload_limit:
            cmd.extend(["--uplimit", str(job.upload_limit)])
        if job.download_limit:
            cmd.extend(["--downlimit", str(job.download_limit)])

        cmd.extend([str(job.local), self._config.nextcloud.url])

        LOGGER.info("[%s] starting bidirectional sync", job.name)
        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except OSError as err:
            raise SyncError(f"could not start nextcloudcmd: {err}") from err

        output_lines: deque[str] = deque(maxlen=200)
        assert process.stdout is not None

        def read_output() -> None:
            output_lines.extend(process.stdout)

        output_reader = threading.Thread(target=read_output, daemon=True)
        output_reader.start()

        assert process.stdin is not None
        try:
            process.stdin.write(self._config.nextcloud.password + "\n")
            process.stdin.close()
        except OSError:
            pass

        deadline = time.monotonic() + self._config.timeout
        while process.poll() is None:
            if self._stop_event.wait(0.25):
                _stop_process(process)
                output_reader.join(timeout=2)
                _close_process_streams(process)
                raise SyncCancelled("sync cancelled during shutdown")
            if process.poll() is not None:
                break
            if time.monotonic() >= deadline:
                _stop_process(process)
                output_reader.join(timeout=2)
                _close_process_streams(process)
                raise SyncError(
                    f"sync timed out after {self._config.timeout} seconds",
                    exit_code=process.returncode,
                )

        output_reader.join(timeout=2)
        details = _sanitize_output("".join(output_lines), self._config.nextcloud.password)
        _close_process_streams(process)

        if "Usage: nextcloudcmd" in details:
            raise SyncError("nextcloudcmd rejected its command-line options")
        if process.returncode != 0:
            message = details or "nextcloudcmd returned no error details"
            raise SyncError(message, exit_code=process.returncode)

        if details:
            LOGGER.debug("[%s] nextcloudcmd output: %s", job.name, details)
        LOGGER.info("[%s] sync finished", job.name)


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _close_process_streams(process: subprocess.Popen[str]) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    if process.stdout is not None and not process.stdout.closed:
        process.stdout.close()


def _sanitize_output(value: str, password: str) -> str:
    cleaned = value.replace(password, "***") if password else value
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return "\n".join(lines)[-MAX_ERROR_LENGTH:]


def _validate_job_path(job: SyncJob) -> None:
    try:
        current = job.local.resolve(strict=True)
    except OSError as err:
        raise SyncError(f"local path is unavailable: {err}") from err
    if current != job.local or not current.is_dir():
        raise SyncError("local path changed or became a symbolic link after startup")


def _write_exclude_file(excludes: list[str], job_state: Path) -> Path | None:
    if not excludes:
        return None

    # nextcloudcmd anchors a file named sync-exclude.lst at the sync root.
    exclude_file = job_state / "sync-exclude.lst"
    exclude_file.write_text("\n".join(excludes) + "\n", encoding="utf-8")
    return exclude_file
