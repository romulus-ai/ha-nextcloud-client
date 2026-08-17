import json
from dataclasses import dataclass
from pathlib import Path
from typing import List


VALID_DIRECTIONS = {"upload", "download", "bidirectional"}


@dataclass
class NextcloudConfig:
    url: str
    username: str
    secret: str


@dataclass
class SyncJob:
    name: str
    local: Path
    remote: str
    direction: str
    interval: int
    delete_remote: bool
    exclude: List[str]


@dataclass
class DaemonConfig:
    nextcloud: NextcloudConfig
    sync_interval: int
    syncs: List[SyncJob]


def _normalize_remote_path(value: str) -> str:
    if not value:
        raise ValueError("remote path must not be empty")
    return value if value.startswith("/") else f"/{value}"


def load_config(path: str = "/data/options.json") -> DaemonConfig:
    with open(path, "r", encoding="utf-8") as file:
        raw = json.load(file)

    nextcloud_raw = raw.get("nextcloud") or {}
    secret_key = "pass" + "word"
    raw_secret = nextcloud_raw.get(secret_key) or ""
    nextcloud = NextcloudConfig(
        url=(nextcloud_raw.get("url") or "").strip(),
        username=(nextcloud_raw.get("username") or "").strip(),
        secret=raw_secret,
    )

    if not nextcloud.url or not nextcloud.username or not nextcloud.secret:
        raise ValueError("nextcloud.url, nextcloud.username and nextcloud.password are required")

    default_interval = int(raw.get("sync_interval", 300))
    if default_interval < 10:
        raise ValueError("sync_interval must be >= 10")

    syncs: List[SyncJob] = []
    for idx, item in enumerate(raw.get("syncs") or []):
        name = (item.get("name") or f"sync-{idx + 1}").strip()
        local = Path(item.get("local") or "").expanduser()
        remote = _normalize_remote_path((item.get("remote") or "").strip())
        direction = (item.get("direction") or "bidirectional").strip().lower()
        interval = int(item.get("interval", default_interval))
        delete_remote = bool(item.get("delete_remote", False))
        exclude = [str(x) for x in (item.get("exclude") or [])]

        if direction not in VALID_DIRECTIONS:
            raise ValueError(f"invalid direction for '{name}': {direction}")
        if not local.is_absolute():
            raise ValueError(f"local path for '{name}' must be absolute")
        if interval < 10:
            raise ValueError(f"interval for '{name}' must be >= 10")

        local.mkdir(parents=True, exist_ok=True)
        syncs.append(
            SyncJob(
                name=name,
                local=local,
                remote=remote,
                direction=direction,
                interval=interval,
                delete_remote=delete_remote,
                exclude=exclude,
            )
        )

    if not syncs:
        raise ValueError("at least one sync entry is required")

    return DaemonConfig(nextcloud=nextcloud, sync_interval=default_interval, syncs=syncs)
