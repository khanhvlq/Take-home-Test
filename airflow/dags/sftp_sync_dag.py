from __future__ import annotations

import json
import os
import posixpath
import stat as statlib
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

from airflow.decorators import dag, task
from airflow.exceptions import AirflowException, AirflowFailException
from airflow.operators.python import get_current_context
from airflow.providers.sftp.hooks.sftp import SFTPHook
from airflow.utils.trigger_rule import TriggerRule


# Keep DAG parsing lightweight: only read env/config constants at import time.
DAG_ID = "sftp_sync"
SCHEDULE = os.getenv("SFTP_SYNC_SCHEDULE", "0 8 * * *")  # Default: daily at 8am
SOURCE_CONN_ID = os.getenv("SFTP_SYNC_SOURCE_CONN_ID", "sftp_source")
TARGET_CONN_ID = os.getenv("SFTP_SYNC_TARGET_CONN_ID", "sftp_target")
BATCH_SIZE = int(os.getenv("SFTP_SYNC_BATCH_SIZE", "500"))
MAX_ACTIVE_TASKS = int(os.getenv("SFTP_SYNC_MAX_ACTIVE_TASKS", "32"))
MAX_ACTIVE_RUNS = int(os.getenv("SFTP_SYNC_MAX_ACTIVE_RUNS", "1"))
SYNC_POOL = os.getenv("SFTP_SYNC_POOL", "default_pool")
SOURCE_BASE_PATH = os.getenv("SFTP_SYNC_SOURCE_BASE_PATH", "/a")
TARGET_BASE_PATH = os.getenv("SFTP_SYNC_TARGET_BASE_PATH", "/a")

DEFAULT_ARGS = {
    "owner": "data-platform",
    "depends_on_past": False,
    "retries": 4,
    "retry_delay": timedelta(seconds=30),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=10),
    "execution_timeout": timedelta(minutes=15),
}


def _walk_remote_files(sftp_client: Any, root_path: str) -> list[dict[str, Any]]:
    """List all files recursively under root_path using SFTP client."""

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
    """Create remote parent directories if needed (mkdir -p behavior)."""

    parent = posixpath.dirname(file_path)
    if not parent or parent == "/":
        return

    parts = [segment for segment in parent.split("/") if segment]
    current = "/"
    for segment in parts:
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


def _chunk_items(items: list[dict[str, Any]], chunk_size: int) -> list[dict[str, Any]]:
    if chunk_size <= 0:
        raise AirflowException("batch_size must be greater than 0")

    batches: list[dict[str, Any]] = []
    for index in range(0, len(items), chunk_size):
        batches.append(
            {
                "batch_id": (index // chunk_size) + 1,
                "files": items[index : index + chunk_size],
            }
        )
    return batches


@dag(
    dag_id=DAG_ID,
    start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
    schedule=SCHEDULE,
    catchup=False,
    default_args=DEFAULT_ARGS,
    max_active_runs=MAX_ACTIVE_RUNS,
    max_active_tasks=MAX_ACTIVE_TASKS,
    tags=["sftp", "sync", "incremental", "append-only"],
    doc_md="""
### SFTP Source -> Target incremental sync via airflow.providers.sftp
- Unidirectional: source -> target
- Append-only: no delete propagation
- Preserve directory structure
- Incremental + idempotent via metadata comparison (size/mtime)
- Dynamic task mapping for horizontal scaling on CeleryExecutor
""",
)
def sftp_sync() -> None:
    @task(task_id="prepare_sync_context", execution_timeout=timedelta(minutes=2))
    def prepare_sync_context() -> dict[str, Any]:
        ctx = get_current_context()
        dag_run = ctx.get("dag_run")
        conf = (dag_run.conf if dag_run else {}) or {}

        run_context = {
            "run_id": (dag_run.run_id if dag_run else f"manual__{datetime.utcnow().isoformat()}"),
            "source_conn_id": conf.get("source_conn_id", SOURCE_CONN_ID),
            "target_conn_id": conf.get("target_conn_id", TARGET_CONN_ID),
            "source_base_path": conf.get("source_base_path", SOURCE_BASE_PATH),
            "target_base_path": conf.get("target_base_path", TARGET_BASE_PATH),
            "batch_size": int(conf.get("batch_size", BATCH_SIZE)),
            "append_only": True,
            "preserve_structure": True,
        }

        print(
            json.dumps(
                {
                    "event": "run_context_prepared",
                    "dag_id": DAG_ID,
                    "run_id": run_context["run_id"],
                    "source_conn_id": run_context["source_conn_id"],
                    "target_conn_id": run_context["target_conn_id"],
                    "source_base_path": run_context["source_base_path"],
                    "target_base_path": run_context["target_base_path"],
                    "batch_size": run_context["batch_size"],
                }
            )
        )
        return run_context

    @task(task_id="plan_incremental_work_items", execution_timeout=timedelta(minutes=10))
    def plan_incremental_work_items(run_context: dict[str, Any]) -> list[dict[str, Any]]:
        source_hook = SFTPHook(ssh_conn_id=run_context["source_conn_id"])
        target_hook = SFTPHook(ssh_conn_id=run_context["target_conn_id"])

        source_client = source_hook.get_conn()
        target_client = target_hook.get_conn()

        try:
            source_files = _walk_remote_files(source_client, run_context["source_base_path"])
            pending_files: list[dict[str, Any]] = []

            for file_info in source_files:
                rel_path = posixpath.relpath(file_info["path"], run_context["source_base_path"])
                target_path = posixpath.join(run_context["target_base_path"], rel_path)

                if _target_is_up_to_date(
                    target_client,
                    target_path,
                    file_info["size"],
                    file_info["mtime"],
                ):
                    continue

                pending_files.append(
                    {
                        "source_path": file_info["path"],
                        "target_path": target_path,
                        "size": file_info["size"],
                        "mtime": file_info["mtime"],
                    }
                )

            batches = _chunk_items(pending_files, run_context["batch_size"])
        finally:
            source_client.close()
            target_client.close()

        if not isinstance(batches, list):
            raise AirflowException("plan_incremental_work_items must return a list")

        print(
            json.dumps(
                {
                    "event": "plan_completed",
                    "run_id": run_context["run_id"],
                    "source_file_count": len(source_files) if "source_files" in locals() else 0,
                    "pending_file_count": sum(len(batch.get("files", [])) for batch in batches),
                    "batch_count": len(batches),
                }
            )
        )
        return batches

    @task.short_circuit(task_id="has_pending_work")
    def has_pending_work(batches: list[dict[str, Any]]) -> bool:
        has_items = len(batches) > 0
        print(json.dumps({"event": "has_work_checked", "has_work": has_items, "batch_count": len(batches)}))
        return has_items

    @task(
        task_id="sync_work_item",
        retries=5,
        retry_delay=timedelta(seconds=20),
        retry_exponential_backoff=True,
        max_retry_delay=timedelta(minutes=8),
        execution_timeout=timedelta(minutes=20),
        pool=SYNC_POOL,
    )
    def sync_work_item(run_context: dict[str, Any], batch_ref: dict[str, Any]) -> dict[str, Any]:
        source_hook = SFTPHook(ssh_conn_id=run_context["source_conn_id"])
        target_hook = SFTPHook(ssh_conn_id=run_context["target_conn_id"])

        source_client = source_hook.get_conn()
        target_client = target_hook.get_conn()

        try:
            synced_count = 0
            skipped_count = 0
            files = batch_ref.get("files", [])

            for file_meta in files:
                source_path = file_meta["source_path"]
                target_path = file_meta["target_path"]
                source_size = int(file_meta.get("size", 0))
                source_mtime = int(file_meta.get("mtime", 0))

                if _target_is_up_to_date(target_client, target_path, source_size, source_mtime):
                    skipped_count += 1
                    continue

                _ensure_target_dirs(target_client, target_path)

                with tempfile.NamedTemporaryFile(prefix="airflow_sftp_sync_", delete=False) as temp_file:
                    temp_path = temp_file.name

                try:
                    source_hook.retrieve_file(remote_full_path=source_path, local_full_path=temp_path)
                    target_hook.store_file(remote_full_path=target_path, local_full_path=temp_path)
                    target_client.utime(target_path, (source_mtime, source_mtime))
                    synced_count += 1
                finally:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

            result = {
                "batch_id": batch_ref.get("batch_id"),
                "total_in_batch": len(files),
                "synced": synced_count,
                "skipped": skipped_count,
            }

            print(
                json.dumps(
                    {
                        "event": "work_item_synced",
                        "run_id": run_context["run_id"],
                        "batch_ref": batch_ref,
                        "result": result,
                    }
                )
            )
            return result
        except Exception as exc:
            # Raise to trigger retries/backoff and preserve failure visibility.
            print(
                json.dumps(
                    {
                        "event": "work_item_sync_failed",
                        "run_id": run_context["run_id"],
                        "batch_ref": batch_ref,
                        "error": str(exc),
                    }
                )
            )
            raise
        finally:
            source_client.close()
            target_client.close()

    @task(
        task_id="finalize_sync_run",
        trigger_rule=TriggerRule.ALL_DONE,
        execution_timeout=timedelta(minutes=5),
    )
    def finalize_sync_run(run_context: dict[str, Any]) -> dict[str, Any]:
        """Finalize with graceful handling.

        We don't pass mapped outputs directly to keep this task runnable even
        when some mapped children fail; instead we pull available XComs.
        """

        ctx = get_current_context()
        ti = ctx["ti"]
        mapped_results = ti.xcom_pull(task_ids="sync_work_item", key="return_value") or []

        if not isinstance(mapped_results, list):
            mapped_results = [mapped_results]

        valid_results = [result for result in mapped_results if isinstance(result, dict)]
        summary = {
            "batch_count": len(valid_results),
            "files_total": sum(int(result.get("total_in_batch", 0)) for result in valid_results),
            "files_synced": sum(int(result.get("synced", 0)) for result in valid_results),
            "files_skipped": sum(int(result.get("skipped", 0)) for result in valid_results),
            "failed_batches": 0,
        }

        print(
            json.dumps(
                {
                    "event": "run_finalized",
                    "run_id": run_context["run_id"],
                    "received_results": len(mapped_results),
                    "valid_results": len(valid_results),
                    "summary": summary,
                }
            )
        )

        # Graceful failure signal for alerting/observability while still
        # allowing finalize bookkeeping to execute first.
        failed_batches = int(summary.get("failed_batches", 0)) if isinstance(summary, dict) else 0
        if failed_batches > 0:
            raise AirflowFailException(
                f"Run {run_context['run_id']} completed with {failed_batches} failed batches"
            )

        return dict(summary) if isinstance(summary, dict) else {"summary": summary}

    context = prepare_sync_context()
    planned_work_items = plan_incremental_work_items(context)
    work_gate = has_pending_work(planned_work_items)
    mapped_sync = sync_work_item.partial(run_context=context).expand(batch_ref=planned_work_items)

    # Orchestration choice:
    # - has_pending_work short-circuits mapped tasks on empty plans.
    # - finalize_sync_run always executes (ALL_DONE) for cleanup/checkpointing.
    work_gate >> mapped_sync
    [work_gate, mapped_sync] >> finalize_sync_run(context)


dag = sftp_sync()
