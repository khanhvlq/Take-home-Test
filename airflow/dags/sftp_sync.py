from __future__ import annotations

from airflow.sdk import DAG
from sftp_sync.dag_factory import build_sftp_sync_dag


dag = build_sftp_sync_dag()
