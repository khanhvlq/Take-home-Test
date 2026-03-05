from __future__ import annotations

import json
import os
import posixpath
import stat as statlib
import tempfile
from typing import Any

from airflow.exceptions import AirflowException, AirflowFailException
from airflow.providers.sftp.hooks.sftp import SFTPHook


STAGING_ROOT = os.getenv("SFTP_SYNC_STAGING_ROOT", "/opt/airflow/data/sftp_sync_staging")


def _list_remote_files(sftp_client: Any, root_path: str) -> list[dict[str, Any]]:
    queue = [root_path.rstrip("/") or "/"]
    files: list[dict[str, Any]] = []

    while queue:
        current_dir = queue.pop()
        for entry in sftp_client.listdir_attr(current_dir):
            full_path = posixpath.join(current_dir, entry.filename)
            if statlib.S_ISDIR(entry.st_mode):
                queue.append(full_path)
            elif statlib.S_ISREG(entry.st_mode):
                files.append(
                    {
                        "path": full_path,
                        "size": int(getattr(entry, "st_size", 0) or 0),
                        "mtime": int(getattr(entry, "st_mtime", 0) or 0),
                    }
                )
    return files


def _ensure_target_dirs(sftp_client: Any, file_path: str) -> None:
    parent = posixpath.dirname(file_path)
    if not parent or parent == "/":
        return

    current = "/"
    for segment in [part for part in parent.split("/") if part]:
        current = posixpath.join(current, segment)
        try:
            sftp_client.stat(current)
        except OSError:
            sftp_client.mkdir(current)


def _target_is_up_to_date(sftp_client: Any, target_path: str, source_size: int, source_mtime: int) -> bool:
    try:
        target_stat = sftp_client.stat(target_path)
    except OSError:
        return False

    return (
        int(getattr(target_stat, "st_size", -1)) == int(source_size)
        and int(getattr(target_stat, "st_mtime", -1)) >= int(source_mtime)
    )


def checker(
    source_conn_id: str,
    target_conn_id: str,
    source_base_path: str,
    target_base_path: str,
    batch_size: int,
    **context: Any,
) -> list[dict[str, Any]]:
    dag_run = context.get("dag_run")
    conf = (dag_run.conf if dag_run else {}) or {}

    source_conn_id = conf.get("source_conn_id", source_conn_id)
    target_conn_id = conf.get("target_conn_id", target_conn_id)
    source_base_path = conf.get("source_base_path", source_base_path)
    target_base_path = conf.get("target_base_path", target_base_path)
    batch_size = int(conf.get("batch_size", batch_size))

    if batch_size <= 0:
        raise AirflowException("batch_size must be greater than 0")

    source_hook = SFTPHook(ssh_conn_id=source_conn_id)
    target_hook = SFTPHook(ssh_conn_id=target_conn_id)
    source_client = source_hook.get_conn()
    target_client = target_hook.get_conn()

    try:
        source_files = _list_remote_files(source_client, source_base_path)
        pending: list[dict[str, Any]] = []

        for source_file in source_files:
            rel_path = posixpath.relpath(source_file["path"], source_base_path)
            target_path = posixpath.join(target_base_path, rel_path)

            if _target_is_up_to_date(target_client, target_path, source_file["size"], source_file["mtime"]):
                continue

            pending.append(
                {
                    "source_conn_id": source_conn_id,
                    "target_conn_id": target_conn_id,
                    "source_path": source_file["path"],
                    "target_path": target_path,
                    "source_mtime": int(source_file["mtime"]),
                }
            )

        print(
            json.dumps(
                {
                    "event": "checker",
                    "source_file_count": len(source_files),
                    "pending_file_count": len(pending),
                    "batch_size": batch_size,
                }
            )
        )
        return pending
    finally:
        source_client.close()
        target_client.close()


def download(
    source_conn_id: str,
    target_conn_id: str,
    source_path: str,
    target_path: str,
    source_mtime: int,
    **context: Any,
) -> dict[str, Any]:
    source_hook = SFTPHook(ssh_conn_id=source_conn_id)

    ti = context.get("ti")
    run_id = getattr(ti, "run_id", "manual")
    safe_run_id = str(run_id).replace(":", "_")
    staging_dir = os.path.join(STAGING_ROOT, safe_run_id)
    os.makedirs(staging_dir, exist_ok=True)

    file_descriptor, local_filepath = tempfile.mkstemp(prefix="airflow_sftp_sync_", dir=staging_dir)
    os.close(file_descriptor)

    source_hook.retrieve_file(remote_full_path=source_path, local_full_path=local_filepath)

    result = {
        "target_conn_id": target_conn_id,
        "target_path": target_path,
        "source_mtime": source_mtime,
        "local_filepath": local_filepath,
    }
    print(json.dumps({"event": "download", **result}))
    return result


def upload(
    target_conn_id: str,
    target_path: str,
    source_mtime: int,
    local_filepath: str,
    **_: Any,
) -> dict[str, Any]:
    if not os.path.exists(local_filepath):
        raise AirflowException(f"Staging file not found: {local_filepath}")

    target_hook = SFTPHook(ssh_conn_id=target_conn_id)
    target_client = target_hook.get_conn()
    uploaded = False

    try:
        _ensure_target_dirs(target_client, target_path)
        target_hook.store_file(remote_full_path=target_path, local_full_path=local_filepath)
        try:
            target_client.utime(target_path, (source_mtime, source_mtime))
        except OSError:
            refreshed_client = target_hook.get_conn()
            try:
                refreshed_client.utime(target_path, (source_mtime, source_mtime))
            except OSError as exc:
                print(
                    json.dumps(
                        {
                            "event": "upload_utime_skipped",
                            "target_path": target_path,
                            "reason": str(exc),
                        }
                    )
                )
            finally:
                refreshed_client.close()

        uploaded = True
        return {"target_path": target_path, "uploaded": 1}
    finally:
        target_client.close()
        if uploaded:
            try:
                os.remove(local_filepath)
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
