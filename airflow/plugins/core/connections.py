from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class BaseConnectionConfig:
    def apply_runtime_overrides(self, conf: dict[str, str]) -> "BaseConnectionConfig":
        return self


@dataclass(slots=True)
class SFTPSyncConnectionConfig(BaseConnectionConfig):
    source_conn_id: str
    target_conn_id: str

    @classmethod
    def from_env(cls) -> "SFTPSyncConnectionConfig":
        return cls(
            source_conn_id=os.getenv("SFTP_SYNC_SOURCE_CONN_ID", "sftp_source"),
            target_conn_id=os.getenv("SFTP_SYNC_TARGET_CONN_ID", "sftp_target"),
        )

    def apply_runtime_overrides(self, conf: dict[str, str]) -> "SFTPSyncConnectionConfig":
        return SFTPSyncConnectionConfig(
            source_conn_id=conf.get("source_conn_id", self.source_conn_id),
            target_conn_id=conf.get("target_conn_id", self.target_conn_id),
        )
