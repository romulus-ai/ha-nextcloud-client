from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from config import DaemonConfig, SyncJob

LOGGER = logging.getLogger("nextcloud-sync")


MAX_ERROR_LENGTH = 4000
BLACKLISTED_ERROR_PATTERN = re.compile(
    r'Could not complete propagation of "(?P<path>.+?)".*'
    r"with status OCC::SyncFileItem::BlacklistedError.*"
    r'error: "(?P<reason>.+?)"'
)
RETRY_AFTER_PATTERN = re.compile(
    r"trying again in (?P<amount>\d+) "
    r"(?P<unit>second|minute|hour|day)(?:\(s\)|s)?",
    re.IGNORECASE,
)
RETRY_AFTER_MULTIPLIERS = {
    "second": 1,
    "minute": 60,
    "hour": 60 * 60,
    "day": 24 * 60 * 60,
}


class SyncError(RuntimeError):
    def __init__(
        self,
        message: str,
        exit_code: int | None = None,
        retryable: bool = True,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


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
                if not err.retryable:
                    LOGGER.warning("[%s] sync will not be retried immediately: %s", job.name, err)
                    break
                LOGGER.warning("[%s] attempt %s/%s failed: %s", job.name, attempt, attempts, err)
                if attempt < attempts and self._stop_event.wait(
                    min(60, 5 * (2 ** (attempt - 1)))
                ):
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
        output = _sanitize_output("".join(output_lines), self._config.nextcloud.password)
        _close_process_streams(process)

        if "Usage: nextcloudcmd" in output:
            raise SyncError("nextcloudcmd rejected its command-line options")
        if process.returncode != 0:
            blacklisted_error = _extract_blacklisted_error(output, process.returncode)
            if blacklisted_error is not None:
                raise blacklisted_error
            message = _failure_details(output) or "nextcloudcmd returned no error details"
            raise SyncError(message, exit_code=process.returncode)

        if output:
            LOGGER.debug("[%s] nextcloudcmd output: %s", job.name, _truncate_output(output))
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
    return "\n".join(lines)


def _extract_blacklisted_error(output: str, exit_code: int | None) -> SyncError | None:
    for line in reversed(output.splitlines()):
        match = BLACKLISTED_ERROR_PATTERN.search(line)
        if match is None:
            continue

        reason = match.group("reason")
        retry_after_seconds = None
        retry_match = RETRY_AFTER_PATTERN.search(reason)
        if retry_match is not None:
            amount = int(retry_match.group("amount"))
            unit = retry_match.group("unit").lower()
            retry_after_seconds = amount * RETRY_AFTER_MULTIPLIERS[unit]

        path = match.group("path")
        return SyncError(
            f'nextcloudcmd temporarily blocked "{path}" after an earlier error: {reason}',
            exit_code=exit_code,
            retryable=False,
            retry_after_seconds=retry_after_seconds,
        )
    return None


def _failure_details(output: str) -> str:
    lines = output.splitlines()
    diagnostics = [
        line
        for line in lines
        if any(marker in line.lower() for marker in ("[ warning ", "[ critical ", "[ fatal "))
    ]
    return _truncate_output("\n".join(diagnostics or lines))


def _truncate_output(value: str) -> str:
    lines = value.splitlines()
    selected: list[str] = []
    length = 0
    for line in reversed(lines):
        added_length = len(line) + (1 if selected else 0)
        if length + added_length > MAX_ERROR_LENGTH:
            break
        selected.append(line)
        length += added_length

    if selected:
        return "\n".join(reversed(selected))
    if not lines:
        return ""
    return lines[-1][: MAX_ERROR_LENGTH - 3] + "..."


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
