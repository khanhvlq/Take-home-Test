from __future__ import annotations

import os
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


@dataclass(slots=True)
class SFTPSyncDagConfig(BaseSyncDagConfig):
    dag_id: str = "sftp_sync"
    schedule: str = "0 8 * * *"
    max_active_tasks: int = 32
    max_active_runs: int = 1
    tags: list[str] = field(default_factory=lambda: ["sftp", "sync", "incremental", "append-only"])
    pool: str = "default_pool"
    batch_size: int = 500
    source_base_path: str = "/a"
    target_base_path: str = "/a"

    @classmethod
    def from_env(cls) -> "SFTPSyncDagConfig":
        return cls(
            schedule=os.getenv("SFTP_SYNC_SCHEDULE", "0 8 * * *"),
            max_active_tasks=int(os.getenv("SFTP_SYNC_MAX_ACTIVE_TASKS", "32")),
            max_active_runs=int(os.getenv("SFTP_SYNC_MAX_ACTIVE_RUNS", "1")),
            pool=os.getenv("SFTP_SYNC_POOL", "default_pool"),
            batch_size=int(os.getenv("SFTP_SYNC_BATCH_SIZE", "500")),
            source_base_path=os.getenv("SFTP_SYNC_SOURCE_BASE_PATH", "/a"),
            target_base_path=os.getenv("SFTP_SYNC_TARGET_BASE_PATH", "/a"),
        )
