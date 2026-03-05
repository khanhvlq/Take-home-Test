from __future__ import annotations

from datetime import timedelta

from airflow.models.xcom_arg import XComArg
from airflow.providers.standard.operators.python import PythonOperator
from airflow.task.trigger_rule import TriggerRule
from airflow.sdk import DAG

from core.assets import build_sftp_sync_assets
from core.config import SFTPSyncDagConfig
from core.connections import SFTPSyncConnectionConfig
from core.dag_builder import BaseDagBuilder
from sftp_sync.tasks import checker, download, recheck, upload


class SFTPSyncDagBuilder(BaseDagBuilder):
    def __init__(self, config: SFTPSyncDagConfig, connections: SFTPSyncConnectionConfig) -> None:
        super().__init__(config=config)
        self.sync_config = config
        self.connections = connections
        self.assets = build_sftp_sync_assets(
            source_conn_id=self.connections.source_conn_id,
            target_conn_id=self.connections.target_conn_id,
            source_base_path=self.sync_config.source_base_path,
            target_base_path=self.sync_config.target_base_path,
        )

    def register_tasks(self, dag: DAG) -> None:
        checker_task = PythonOperator(
            task_id="checker",
            python_callable=checker,
            op_kwargs={
                "source_conn_id": self.connections.source_conn_id,
                "target_conn_id": self.connections.target_conn_id,
                "source_base_path": self.sync_config.source_base_path,
                "target_base_path": self.sync_config.target_base_path,
                "batch_size": self.sync_config.batch_size,
            },
            execution_timeout=timedelta(minutes=10),
            inlets=[self.assets.source],
            outlets=[self.assets.staging],
            dag=dag,
        )

        download_task = (
            PythonOperator.partial(
                task_id="download",
                python_callable=download,
                execution_timeout=timedelta(minutes=20),
                pool=self.sync_config.pool,
                dag=dag,
            ).expand(op_kwargs=XComArg(checker_task))
        )

        upload_task = (
            PythonOperator.partial(
                task_id="upload",
                python_callable=upload,
                execution_timeout=timedelta(minutes=20),
                pool=self.sync_config.pool,
                inlets=[self.assets.staging],
                outlets=[self.assets.target],
                dag=dag,
            ).expand(op_kwargs=XComArg(download_task))
        )

        recheck_task = PythonOperator(
            task_id="recheck",
            python_callable=recheck,
            op_kwargs={"checker_task_id": "checker", "upload_task_id": "upload"},
            trigger_rule=TriggerRule.ALL_DONE,
            execution_timeout=timedelta(minutes=5),
            inlets=[self.assets.target],
            dag=dag,
        )

        checker_task >> download_task >> upload_task
        [checker_task, upload_task] >> recheck_task


def build_sftp_sync_dag() -> DAG:
    config = SFTPSyncDagConfig.from_env()
    connections = SFTPSyncConnectionConfig.from_env()
    return SFTPSyncDagBuilder(config=config, connections=connections).build()
