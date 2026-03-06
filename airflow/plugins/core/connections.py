from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BaseConnectionConfig:
    def apply_runtime_overrides(self, conf: dict[str, str]) -> "BaseConnectionConfig":
        return self


@dataclass(slots=True)
class SFTPSyncConnectionConfig(BaseConnectionConfig):
    source_conn_type: str
    source_conn_id: str
    target_conn_type: str
    target_conn_id: str

    @classmethod
    def from_dict(cls, raw: dict[str, str]) -> "SFTPSyncConnectionConfig":
        return cls(
            source_conn_type=raw.get("source_conn_type", "sftp"),
            source_conn_id=raw.get("source_conn_id", "sftp_source"),
            target_conn_type=raw.get("target_conn_type", "sftp"),
            target_conn_id=raw.get("target_conn_id", "sftp_target"),
        )

    def apply_runtime_overrides(self, conf: dict[str, str]) -> "SFTPSyncConnectionConfig":
        return SFTPSyncConnectionConfig(
            source_conn_type=conf.get("source_conn_type", self.source_conn_type),
            source_conn_id=conf.get("source_conn_id", self.source_conn_id),
            target_conn_type=conf.get("target_conn_type", self.target_conn_type),
            target_conn_id=conf.get("target_conn_id", self.target_conn_id),
        )
