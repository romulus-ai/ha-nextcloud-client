import filecmp
import fcntl
import fnmatch
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from config import DaemonConfig, SyncJob


LOGGER = logging.getLogger("nextcloud-sync")


class SyncRunner:
    def __init__(self, config: DaemonConfig, state_dir: Path = Path("/data/sync_state")) -> None:
        self._config = config
        self._state_dir = state_dir
        self._state_dir.mkdir(parents=True, exist_ok=True)

    def run_job(self, job: SyncJob) -> None:
        job_state = self._state_dir / _slug(job.name)
        job_state.mkdir(parents=True, exist_ok=True)
        lock_file = job_state / ".lock"

        with open(lock_file, "w", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                LOGGER.info("[%s] sync skipped, previous run still active", job.name)
                return

            if job.direction == "upload":
                self._run_upload(job, job_state)
            elif job.direction == "download":
                self._run_download(job, job_state)
            else:
                self._run_bidirectional(job)

    def _run_upload(self, job: SyncJob, job_state: Path) -> None:
        staging = job_state / "upload_staging"
        staging.mkdir(parents=True, exist_ok=True)
        _copy_tree(job.local, staging, job.exclude, delete=job.delete_remote)
        self._run_nextcloud_sync(job, staging, job_state)

    def _run_download(self, job: SyncJob, job_state: Path) -> None:
        staging = job_state / "download_staging"
        staging.mkdir(parents=True, exist_ok=True)
        self._run_nextcloud_sync(job, staging, job_state)
        _copy_tree(staging, job.local, job.exclude, delete=True)

    def _run_bidirectional(self, job: SyncJob) -> None:
        self._run_nextcloud_sync(job, job.local, self._state_dir / _slug(job.name))

    def _run_nextcloud_sync(self, job: SyncJob, local_path: Path, job_state: Path) -> None:
        cmd = [
            "nextcloudcmd",
            "--non-interactive",
            "--user",
            self._config.nextcloud.username,
            "--password",
            self._config.nextcloud.secret,
            "--path",
            job.remote,
        ]

        exclude_file = _write_exclude_file(job.exclude, job_state)
        if exclude_file:
            cmd.extend(["--exclude", str(exclude_file)])

        cmd.extend([str(local_path), self._config.nextcloud.url])

        LOGGER.info("[%s] starting sync (%s)", job.name, job.direction)
        for attempt in range(1, 4):
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                if proc.stdout.strip():
                    LOGGER.info("[%s] %s", job.name, proc.stdout.strip())
                LOGGER.info("[%s] sync finished", job.name)
                return

            LOGGER.warning("[%s] sync attempt %s/3 failed", job.name, attempt)
            if proc.stdout.strip():
                LOGGER.warning("[%s] stdout: %s", job.name, proc.stdout.strip())
            if proc.stderr.strip():
                LOGGER.warning("[%s] stderr: %s", job.name, proc.stderr.strip())
            if attempt < 3:
                time.sleep(5)

        raise RuntimeError(f"sync failed for '{job.name}' after 3 attempts")


def list_local_state(path: Path, excludes: Iterable[str]) -> Dict[str, Tuple[int, float]]:
    state: Dict[str, Tuple[int, float]] = {}
    for root, _, files in os.walk(path):
        root_path = Path(root)
        for filename in files:
            file_path = root_path / filename
            rel = file_path.relative_to(path).as_posix()
            if _is_excluded(rel, excludes):
                continue
            stat = file_path.stat()
            state[rel] = (stat.st_size, stat.st_mtime)
    return state


def _copy_tree(source: Path, target: Path, excludes: Iterable[str], delete: bool) -> None:
    source.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)

    source_files = set()
    for root, _, files in os.walk(source):
        root_path = Path(root)
        for filename in files:
            src_file = root_path / filename
            rel = src_file.relative_to(source).as_posix()
            if _is_excluded(rel, excludes):
                continue

            source_files.add(rel)
            dst_file = target / rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)

            if not dst_file.exists() or not filecmp.cmp(src_file, dst_file, shallow=False):
                shutil.copy2(src_file, dst_file)

    if delete:
        for root, _, files in os.walk(target):
            root_path = Path(root)
            for filename in files:
                dst_file = root_path / filename
                rel = dst_file.relative_to(target).as_posix()
                if _is_excluded(rel, excludes):
                    continue
                if rel not in source_files:
                    dst_file.unlink(missing_ok=True)


def _is_excluded(relative_path: str, excludes: Iterable[str]) -> bool:
    for pattern in excludes:
        if fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(Path(relative_path).name, pattern):
            return True
    return False


def _slug(value: str) -> str:
    safe = [c.lower() if c.isalnum() else "-" for c in value]
    return "".join(safe).strip("-") or "sync"


def _write_exclude_file(excludes: List[str], job_state: Path) -> Path | None:
    if not excludes:
        return None

    exclude_file = job_state / "exclude.lst"
    exclude_file.write_text("\n".join(excludes) + "\n", encoding="utf-8")
    return exclude_file
