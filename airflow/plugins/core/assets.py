from __future__ import annotations

from dataclasses import dataclass

from airflow.sdk import Asset


@dataclass(slots=True)
class SyncAssets:
    source: Asset
    target: Asset


def build_sftp_sync_assets(source_conn_id: str, target_conn_id: str, source_base_path: str, target_base_path: str) -> SyncAssets:
    return SyncAssets(
        source=Asset(f"sftp://{source_conn_id}{source_base_path}"),
        target=Asset(f"sftp://{target_conn_id}{target_base_path}"),
    )
