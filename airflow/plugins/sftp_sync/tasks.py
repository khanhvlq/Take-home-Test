from __future__ import annotations

import json
import os
import posixpath
import tempfile
from typing import Any

from airflow.exceptions import AirflowException, AirflowFailException

from core.io_adapters import close_adapter, create_source_adapter, create_target_adapter
from core.transformers import apply_transformations, normalize_transformations


STAGING_ROOT = "/opt/airflow/data/sftp_sync_staging"


def checker(
    source_conn_type: str,
    source_conn_id: str,
    target_conn_type: str,
    target_conn_id: str,
    source_base_path: str,
    target_base_path: str,
    batch_size: int,
    max_file_size_mb: int,
    transformations: list[str] | tuple[str, ...] | None = None,
    **context: Any,
) -> list[dict[str, Any]]:
    dag_run = context.get("dag_run")
    conf = (dag_run.conf if dag_run else {}) or {}

    source_conn_type = conf.get("source_conn_type", source_conn_type)
    source_conn_id = conf.get("source_conn_id", source_conn_id)
    target_conn_type = conf.get("target_conn_type", target_conn_type)
    target_conn_id = conf.get("target_conn_id", target_conn_id)
    source_base_path = conf.get("source_base_path", source_base_path)
    target_base_path = conf.get("target_base_path", target_base_path)
    batch_size = int(conf.get("batch_size", batch_size))
    max_file_size_mb = int(conf.get("max_file_size_mb", max_file_size_mb))
    transformations = normalize_transformations(conf.get("transformations", transformations))

    if batch_size <= 0:
        raise AirflowException("batch_size must be greater than 0")

    max_file_size_bytes = max_file_size_mb * 1024 * 1024 if max_file_size_mb > 0 else None

    source_adapter = create_source_adapter(conn_type=source_conn_type, conn_id=source_conn_id)
    target_adapter = create_target_adapter(conn_type=target_conn_type, conn_id=target_conn_id)

    try:
        source_files = source_adapter.list_files(source_base_path)
        pending: list[dict[str, Any]] = []
        oversized: list[str] = []

        for source_file in source_files:
            if max_file_size_bytes is not None and source_file.size > max_file_size_bytes:
                oversized.append(source_file.path)
                continue

            rel_path = posixpath.relpath(source_file.path, source_base_path)
            target_path = posixpath.join(target_base_path, rel_path)
            target_stat = target_adapter.stat(target_path)

            if target_stat and target_stat.size == source_file.size and target_stat.mtime >= source_file.mtime:
                continue

            pending.append(
                {
                    "source_conn_type": source_conn_type,
                    "source_conn_id": source_conn_id,
                    "target_conn_type": target_conn_type,
                    "target_conn_id": target_conn_id,
                    "source_path": source_file.path,
                    "target_path": target_path,
                    "source_mtime": int(source_file.mtime),
                    "transformations": transformations,
                }
            )

        print(
            json.dumps(
                {
                    "event": "checker",
                    "source_file_count": len(source_files),
                    "pending_file_count": len(pending),
                    "oversized_file_count": len(oversized),
                    "max_file_size_mb": max_file_size_mb,
                    "batch_size": batch_size,
                }
            )
        )
        return pending
    finally:
        close_adapter(source_adapter)
        close_adapter(target_adapter)


def download(
    source_conn_type: str,
    source_conn_id: str,
    target_conn_type: str,
    target_conn_id: str,
    source_path: str,
    target_path: str,
    source_mtime: int,
    transformations: list[str] | None = None,
    **context: Any,
) -> dict[str, Any]:
    source_adapter = create_source_adapter(conn_type=source_conn_type, conn_id=source_conn_id)

    ti = context.get("ti")
    run_id = getattr(ti, "run_id", "manual")
    safe_run_id = str(run_id).replace(":", "_")
    staging_dir = os.path.join(STAGING_ROOT, safe_run_id)
    os.makedirs(staging_dir, exist_ok=True)

    file_descriptor, local_filepath = tempfile.mkstemp(prefix="airflow_sync_", dir=staging_dir)
    os.close(file_descriptor)

    try:
        source_adapter.retrieve_file(remote_path=source_path, local_path=local_filepath)
    finally:
        close_adapter(source_adapter)

    result = {
        "target_conn_type": target_conn_type,
        "target_conn_id": target_conn_id,
        "target_path": target_path,
        "source_mtime": source_mtime,
        "local_filepath": local_filepath,
        "transformations": normalize_transformations(transformations),
    }
    print(json.dumps({"event": "download", **result}))
    return result


def upload(
    target_conn_type: str,
    target_conn_id: str,
    target_path: str,
    source_mtime: int,
    local_filepath: str,
    transformations: list[str] | None = None,
    **_: Any,
) -> dict[str, Any]:
    if not os.path.exists(local_filepath):
        raise AirflowException(f"Staging file not found: {local_filepath}")

    target_adapter = create_target_adapter(conn_type=target_conn_type, conn_id=target_conn_id)
    uploaded = False
    transformed_local_path = local_filepath

    try:
        transformed_local_path = apply_transformations(local_filepath, normalize_transformations(transformations))

        target_adapter.ensure_parent_dirs(target_path)
        target_adapter.store_file(local_path=transformed_local_path, remote_path=target_path)

        try:
            target_adapter.set_mtime(path=target_path, mtime=source_mtime)
        except OSError as exc:
            print(
                json.dumps(
                    {
                        "event": "upload_mtime_skipped",
                        "target_path": target_path,
                        "reason": str(exc),
                    }
                )
            )

        uploaded = True
        return {"target_path": target_path, "uploaded": 1}
    finally:
        close_adapter(target_adapter)
        if uploaded:
            for candidate in {local_filepath, transformed_local_path}:
                try:
                    if os.path.exists(candidate):
                        os.remove(candidate)
                except OSError:
                    pass


def recheck(checker_task_id: str = "checker", upload_task_id: str = "upload", **context: Any) -> dict[str, Any]:
    ti = context["ti"]
    planned_items = ti.xcom_pull(task_ids=checker_task_id, key="return_value") or []
    uploaded_items = ti.xcom_pull(task_ids=upload_task_id, key="return_value") or []

    if not isinstance(planned_items, list):
        planned_items = [planned_items]
    if not isinstance(uploaded_items, list):
        uploaded_items = [uploaded_items]

    uploaded_ok = [item for item in uploaded_items if isinstance(item, dict) and int(item.get("uploaded", 0)) == 1]

    summary = {
        "planned": len(planned_items),
        "uploaded": len(uploaded_ok),
        "failed": max(0, len(planned_items) - len(uploaded_ok)),
    }
    print(json.dumps({"event": "recheck", "summary": summary}))

    if summary["failed"] > 0:
        raise AirflowFailException(f"Sync has {summary['failed']} failed item(s)")

    return summary
