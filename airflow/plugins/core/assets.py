from __future__ import annotations

from dataclasses import dataclass

from airflow.sdk import Asset


@dataclass(slots=True)
class SyncAssets:
    source: Asset
    staging: Asset
    target: Asset


def _build_asset_uri(conn_type: str, conn_id: str, base_path: str) -> str:
    normalized = base_path if base_path.startswith("/") else f"/{base_path}"
    return f"{conn_type}://{conn_id}{normalized}"


def build_sftp_sync_assets(
    source_conn_type: str,
    source_conn_id: str,
    target_conn_type: str,
    target_conn_id: str,
    source_base_path: str,
    target_base_path: str,
) -> SyncAssets:
    return SyncAssets(
        source=Asset(_build_asset_uri(source_conn_type, source_conn_id, source_base_path)),
        staging=Asset("file:///opt/airflow/data/sftp_sync_staging"),
        target=Asset(_build_asset_uri(target_conn_type, target_conn_id, target_base_path)),
    )
