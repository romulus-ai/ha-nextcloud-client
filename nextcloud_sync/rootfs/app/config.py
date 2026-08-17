import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


DEFAULT_ALLOWED_ROOTS = (Path("/backup"), Path("/share"), Path("/media"))
JOB_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MQTT_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class NextcloudConfig:
    url: str
    username: str
    password: str


@dataclass
class SyncJob:
    id: str
    name: str
    enabled: bool
    local: Path
    remote: str
    interval: int
    upload_limit: int
    download_limit: int
    exclude: list[str]


@dataclass
class DaemonConfig:
    nextcloud: NextcloudConfig
    sync_interval: int
    timeout: int
    max_retries: int
    max_parallel_jobs: int
    mqtt_enabled: bool
    mqtt_discovery_prefix: str
    syncs: list[SyncJob]


def _normalize_remote_path(value: str) -> str:
    if not value:
        raise ValueError("remote path must not be empty")
    path = PurePosixPath("/" + value.lstrip("/"))
    if ".." in path.parts:
        raise ValueError("remote path must not contain '..'")
    normalized = str(path)
    if normalized == "/":
        raise ValueError("remote path must not be the Nextcloud root")
    return normalized


def _validate_server_url(value: str) -> str:
    url = value.rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("nextcloud.url must be an HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("credentials must not be included in nextcloud.url")
    if parsed.query or parsed.fragment:
        raise ValueError("nextcloud.url must not contain a query or fragment")
    lowered_path = parsed.path.lower().rstrip("/")
    if "/dav" in lowered_path or "/webdav" in lowered_path:
        raise ValueError("nextcloud.url must be the base URL, not a DAV endpoint")
    if any(ord(character) < 32 for character in value):
        raise ValueError("nextcloud.url contains control characters")
    return url


def _validate_local_path(value: str, name: str, allowed_roots: tuple[Path, ...]) -> Path:
    local = Path(value).expanduser()
    if not local.is_absolute():
        raise ValueError(f"local path for '{name}' must be absolute")

    resolved = local.resolve(strict=False)
    roots = tuple(root.resolve(strict=False) for root in allowed_roots)
    if not any(resolved == root or root in resolved.parents for root in roots):
        allowed = ", ".join(str(root) for root in roots)
        raise ValueError(f"local path for '{name}' must be below one of: {allowed}")
    return resolved


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def load_config(
    path: str = "/data/options.json",
    allowed_roots: tuple[Path, ...] = DEFAULT_ALLOWED_ROOTS,
) -> DaemonConfig:
    with open(path, "r", encoding="utf-8") as file:
        raw = json.load(file)

    nextcloud_raw = raw.get("nextcloud") or {}
    password = str(nextcloud_raw.get("password") or "")
    nextcloud = NextcloudConfig(
        url=_validate_server_url(str(nextcloud_raw.get("url") or "").strip()),
        username=str(nextcloud_raw.get("username") or "").strip(),
        password=password,
    )

    if not nextcloud.username or not nextcloud.password:
        raise ValueError("nextcloud.url, nextcloud.username and nextcloud.password are required")
    if nextcloud.username.startswith("-") or "\n" in nextcloud.username:
        raise ValueError("nextcloud.username contains unsupported characters")
    if "\n" in nextcloud.password or "\r" in nextcloud.password:
        raise ValueError("nextcloud.password must not contain line breaks")

    default_interval = int(raw.get("sync_interval", 300))
    timeout = int(raw.get("timeout", 3600))
    max_retries = int(raw.get("max_retries", 2))
    max_parallel_jobs = int(raw.get("max_parallel_jobs", 2))
    mqtt_enabled = bool(raw.get("mqtt_enabled", True))
    mqtt_discovery_prefix = str(raw.get("mqtt_discovery_prefix") or "homeassistant").strip()
    if default_interval < 60:
        raise ValueError("sync_interval must be >= 60")
    if not 60 <= timeout <= 86400:
        raise ValueError("timeout must be between 60 and 86400 seconds")
    if not 0 <= max_retries <= 10:
        raise ValueError("max_retries must be between 0 and 10")
    if not 1 <= max_parallel_jobs <= 4:
        raise ValueError("max_parallel_jobs must be between 1 and 4")
    if not MQTT_PREFIX_PATTERN.fullmatch(mqtt_discovery_prefix):
        raise ValueError("mqtt_discovery_prefix contains invalid characters")

    syncs: list[SyncJob] = []
    job_ids: set[str] = set()
    for idx, item in enumerate(raw.get("syncs") or []):
        job_id = str(item.get("id") or "").strip().lower()
        name = str(item.get("name") or f"Sync {idx + 1}").strip()
        if not JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError(
                f"id for '{name}' must start with a lowercase letter or number and only contain a-z, 0-9, _ or -"
            )
        if job_id in job_ids:
            raise ValueError(f"duplicate sync id: {job_id}")
        job_ids.add(job_id)

        local = _validate_local_path(str(item.get("local") or ""), name, allowed_roots)
        remote = _normalize_remote_path(str(item.get("remote") or "").strip())
        interval = int(item.get("interval", default_interval))
        upload_limit = int(item.get("upload_limit", 0))
        download_limit = int(item.get("download_limit", 0))
        exclude = [str(x) for x in (item.get("exclude") or [])]

        if interval < 60:
            raise ValueError(f"interval for '{name}' must be >= 60")
        if upload_limit < 0 or download_limit < 0:
            raise ValueError(f"bandwidth limits for '{name}' must not be negative")
        if any(_paths_overlap(local, existing.local) for existing in syncs):
            raise ValueError(f"local path for '{name}' overlaps another sync job")
        if any(
            remote == existing.remote
            or remote.startswith(existing.remote + "/")
            or existing.remote.startswith(remote + "/")
            for existing in syncs
        ):
            raise ValueError(f"remote path for '{name}' overlaps another sync job")

        local.mkdir(parents=True, exist_ok=True)
        syncs.append(
            SyncJob(
                id=job_id,
                name=name,
                enabled=bool(item.get("enabled", True)),
                local=local,
                remote=remote,
                interval=interval,
                upload_limit=upload_limit,
                download_limit=download_limit,
                exclude=exclude,
            )
        )

    if not syncs:
        raise ValueError("at least one sync job is required")

    return DaemonConfig(
        nextcloud=nextcloud,
        sync_interval=default_interval,
        timeout=timeout,
        max_retries=max_retries,
        max_parallel_jobs=max_parallel_jobs,
        mqtt_enabled=mqtt_enabled,
        mqtt_discovery_prefix=mqtt_discovery_prefix,
        syncs=syncs,
    )
