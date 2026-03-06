from __future__ import annotations

import sys
from pathlib import Path

# Add plugins directory to Python path for module resolution
plugins_dir = Path(__file__).parent.parent / "plugins"
if str(plugins_dir) not in sys.path:
    sys.path.insert(0, str(plugins_dir))

from airflow import DAG  # noqa: F401 - marker for DAG discovery heuristic

from core.config import SFTPSyncDagConfig
from core.connections import SFTPSyncConnectionConfig
from sftp_sync.dag_factory import SFTPSyncDagBuilder


# Configuration defined directly in DAG
config = SFTPSyncDagConfig(
    dag_id="sftp_sync",
    schedule="0 8 * * *",
    max_active_tasks=32,
    max_active_runs=1,
    pool="default_pool",
    batch_size=500,
    source_base_path="/a",
    target_base_path="/a",
    max_file_size_mb=256,
    transformations=("noop",),
)

connections = SFTPSyncConnectionConfig(
    source_conn_type="sftp",
    source_conn_id="sftp_source",
    target_conn_type="sftp",
    target_conn_id="sftp_target",
)

# Build DAG with explicit configuration
dag = SFTPSyncDagBuilder(config=config, connections=connections).build()
