from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(slots=True)
class BaseDagConfig:
    dag_id: str
    schedule: str
    start_date: datetime = datetime(2025, 1, 1, tzinfo=timezone.utc)
    catchup: bool = False
    max_active_runs: int = 1
    max_active_tasks: int = 16
    owner: str = "data-platform"
    tags: list[str] = field(default_factory=list)

    @property
    def default_args(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "depends_on_past": False,
            "retries": 4,
            "retry_delay": timedelta(seconds=30),
            "retry_exponential_backoff": True,
            "max_retry_delay": timedelta(minutes=10),
            "execution_timeout": timedelta(minutes=15),
        }


@dataclass(slots=True)
class BaseSyncDagConfig(BaseDagConfig):
    pool: str = "default_pool"
    batch_size: int = 500
    source_base_path: str = "/"
    target_base_path: str = "/"
    max_file_size_mb: int = 256
    transformations: tuple[str, ...] = ("noop",)


@dataclass(slots=True)
class SFTPSyncDagConfig(BaseSyncDagConfig):
    dag_id: str = "sftp_sync"
    schedule: str = "0 8 * * *"
    max_active_tasks: int = 32
    max_active_runs: int = 1
    tags: list[str] = field(default_factory=lambda: ["sync", "sftp"])
    pool: str = "default_pool"
    batch_size: int = 500
    source_base_path: str = "/a"
    target_base_path: str = "/a"
    max_file_size_mb: int = 256
    transformations: tuple[str, ...] = ("noop",)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SFTPSyncDagConfig":
        return cls(
            schedule=str(raw.get("schedule", "0 8 * * *")),
            max_active_tasks=int(raw.get("max_active_tasks", 32)),
            max_active_runs=int(raw.get("max_active_runs", 1)),
            pool=str(raw.get("pool", "default_pool")),
            batch_size=int(raw.get("batch_size", 500)),
            source_base_path=str(raw.get("source_base_path", "/a")),
            target_base_path=str(raw.get("target_base_path", "/a")),
            max_file_size_mb=int(raw.get("max_file_size_mb", 256)),
            transformations=tuple(str(item) for item in raw.get("transformations", ["noop"])),
        )
