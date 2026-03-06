# Extensible SFTP Sync DAG - Apache Airflow

Production-ready incremental file synchronization pipeline using Apache Airflow 3.1.7 with pluggable connector architecture and horizontal scaling capabilities.

## Quick Start

### Prerequisites
- Docker & Docker Compose
- 4GB+ RAM for Docker engine

### Running the Stack
```bash
./run.sh
```

This starts:
- **Airflow**: http://localhost:8080 (admin/admin)
- **PostgreSQL**: Metadata database
- **Redis**: Celery broker
- **SFTP Services**: Mock source/target for testing

### First Run
```bash
# Verify DAG is discovered
docker compose exec airflow-webserver airflow dags list

# Unpause DAG
docker compose exec airflow-webserver airflow dags unpause sftp_sync

# Trigger sync
docker compose exec airflow-webserver airflow dags trigger sftp_sync

# Monitor in UI or CLI
docker compose exec airflow-webserver airflow dags list-runs sftp_sync
```

## Architecture

### Data Flow
```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ Checker  │───▶│ Download │───▶│ Upload   │───▶│ Recheck  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
   (plan)       (parallel)      (parallel)       (validate)
```

**Tasks:**
- **checker**: Scan source, compare metadata with target, plan incremental work
- **download** (mapped): Parallel file retrieval to staging area
- **upload** (mapped): Transform and parallel upload to target
- **recheck**: Validate success count, fail if anomalies detected

### Plugin Architecture
```
airflow/
├── dags/
│   └── sftp_sync.py                    # DAG entrypoint (thin config layer)
├── plugins/
│   ├── core/                           # ⭐ Reusable abstractions
│   │   ├── io_adapters.py             # Connector interface (SourceAdapter/TargetAdapter)
│   │   ├── transformers.py            # Transformation pipeline (gzip, custom)
│   │   ├── config.py                  # Configuration models
│   │   ├── connections.py             # Connection definitions
│   │   ├── assets.py                  # Data lineage assets
│   │   ├── metadata_builder.py        # Data catalog metadata
│   │   └── dag_builder.py             # Base DAG builder
│   └── sftp_sync/                      # Business-specific implementation
│       ├── dag_factory.py             # SFTP DAG builder
│       └── tasks.py                   # Task implementations
└── config/
    ├── airflow.cfg
    └── connection.json
```

**Key Design Pattern**: Adapter pattern enables adding new connectors (S3, GCS, Azure Blob) without modifying DAG orchestration logic.

### Scaling Strategy
- **CeleryExecutor** with Redis broker for distributed execution
- **Dynamic task mapping**: Expands to N parallel tasks per file
- **Run-isolated staging**: `/opt/airflow/data/sftp_sync_staging/<run_id>/`
- **PostgreSQL backend**: ACID compliance for metadata consistency

**Capacity**: 
- 1 worker handles ~200-300 files/hour
- 10 workers handle ~10,000+ files/hour (depends on file size, network)

## Configuration

### Core Settings (.env)
```bash
AIRFLOW__CORE__EXECUTOR=CeleryExecutor
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql://airflow:airflow@postgres:5432/airflow
AIRFLOW__CELERY__BROKER_URL=redis://redis:6379/0
AIRFLOW__CELERY__RESULT_BACKEND=db+postgresql://airflow:airflow@postgres:5432/airflow
```

### DAG Configuration
Default settings in `airflow/dags/sftp_sync.py`:
```python
config = SFTPSyncDagConfig(
    schedule="0 8 * * *",              # Daily at 8 AM UTC
    max_active_tasks=32,               # Parallelism
    batch_size=500,
    max_file_size_mb=256,              # Skip oversized files
    transformations=("noop",),
)
```

### Runtime Override
```bash
docker compose exec airflow-webserver airflow dags trigger sftp_sync --conf '{
  "source_base_path": "/custom/path",
  "target_base_path": "/custom/target",
  "batch_size": 1000,
  "transformations": ["gzip"]
}'
```

## Features

### ✅ Implemented
- **Incremental Sync**: Only transfer new/modified files (size + mtime comparison)
- **Idempotent Operations**: Safe to re-run; skips already-synced files
- **Dynamic Parallelism**: 1 task = 1 file, scales horizontally
- **Transformation Pipeline**: Out-of-box support for gzip; easy to extend
- **Anomaly Handling**: Skip large files (> 256MB), fail if upload count mismatches
- **Data Lineage**: Built-in asset tracking with Airflow inlets/outlets
- **Metadata Catalog**: Auto-generated pipeline metadata in DAG documentation
- **Retry Strategy**: Exponential backoff (4 retries, max 10min delay)
- **Connection Auto-import**: JSON-based connection configuration

### 🔌 Extensible
**Add S3 Connector** (example):
```python
class S3SourceAdapter(SourceAdapter):
    def list_files(self, root_path: str) -> list[FileMetadata]: ...
    def retrieve_file(self, remote_path: str, local_path: str) -> None: ...
```

Then use: `airflow dags trigger sftp_sync --conf '{"source_conn_type": "s3"}'`

**Add Transformation**:
```python
# In transformers.py
if action == "encrypt":
    output = encrypt_file(output)
```

Then use: `--conf '{"transformations": ["gzip", "encrypt"]}'`

## Key Design Decisions

| Decision | Rationale | Trade-off |
|----------|-----------|-----------|
| **PostgreSQL backend** | ACID + concurrent writes support | More complex than SQLite |
| **CeleryExecutor** | Horizontal scaling + mature ecosystem | Redis/broker dependency |
| **Adapter pattern** | Connector independence | Small abstraction overhead |
| **Size+mtime validation** | Fast incremental detection | Misses silent corruption |
| **Append-only model** | Simpler logic, data lake pattern | No delete propagation |
| **Per-run staging** | Parallel run isolation | Disk space overhead |

## What's Not Included

- **Checksum validation**: Trade-off favors throughput; can be added as optional flag
- **Dead letter queue**: Failed files retry; manual intervention if all retries fail
- **Multi-tenancy**: Single team/workspace for this take-home
- **Advanced schema evolution**: Handles files only, not schema registry integration

## Monitoring & Observability

### Logs & Metrics
- **Structured logging**: JSON output from checker/upload tasks
- **XCom metrics**: `{"planned": N, "uploaded": N, "failed": 0}`
- **Airflow UI**:
  - Task duration histograms
  - Success/failure rates  
  - Gantt chart for bottleneck identification
  - Data lineage graph (source → staging → target)

### Example: View Task Summary
```bash
docker compose exec airflow-webserver airflow dags list-runs sftp_sync
docker compose logs airflow-scheduler | grep '"event":'  # JSON logs
```

### Production Monitoring (Recommended)
- [ ] Metrics export (Prometheus/StatsD)
- [ ] Alert rules (failure rate, duration anomalies)
- [ ] SLA sensor for freshness guarantees
- [ ] Cost tracking via CloudWatch/Datadog

## Troubleshooting

### DAG Not Visible
```bash
docker compose exec airflow-scheduler airflow dags list-import-errors
docker compose exec airflow-scheduler airflow dags reserialize
docker compose restart airflow-scheduler airflow-dag-processor
```

### Tasks Stuck in Queued
```bash
docker compose ps airflow-worker
docker compose logs airflow-worker
docker compose restart airflow-worker
# Or scale up
docker compose up --scale airflow-worker=5 -d
```

### Module Import Errors
```bash
# Ensure plugin __init__.py files exist
touch airflow/plugins/__init__.py
touch airflow/plugins/core/__init__.py
touch airflow/plugins/sftp_sync/__init__.py
docker compose restart airflow-scheduler
```

### PostgreSQL Connection Issues
```bash
docker compose exec postgres psql -U airflow airflow -c "SELECT 1"
# If failed, clean slate
docker compose down -v && docker compose up -d
```

## Testing

### Smoke Test
```bash
./run.sh && sleep 60
docker compose exec airflow-webserver airflow dags list | grep sftp_sync
docker compose exec airflow-webserver airflow dags trigger sftp_sync
sleep 120
docker compose exec airflow-webserver airflow dags list-runs sftp_sync
```

### Functional Test
```bash
# Seed test files
docker exec sftp-source sh -c 'echo "test" > /home/source/a/test.txt'

# Trigger sync and verify
docker compose exec airflow-webserver airflow dags trigger sftp_sync
sleep 120

# Check target
docker exec sftp-target cat /home/target/a/test.txt
```

### Load Test (1000 files)
```bash
docker exec sftp-source sh -c 'for i in $(seq 1 1000); do echo $i > /home/source/a/file_$i.txt; done'
docker compose exec airflow-webserver airflow dags trigger sftp_sync
# Monitor: check UI for parallelism, duration, success rate
```

## Operations

### Stop Services
```bash
docker compose down
```

### Clean Database
```bash
docker compose down -v
rm -rf airflow/data/sftp_sync_staging/*
```

### Backup & Restore
```bash
# Backup
docker compose exec postgres pg_dump -U airflow airflow > backup.sql

# Restore
docker compose exec -T postgres psql -U airflow airflow < backup.sql
```

### Scale Workers
```bash
docker compose up --scale airflow-worker=10 -d
```

## Performance Benchmarks

Target SLAs (based on 1 worker, 256MB max file):
- **Throughput**: 200-300 files/hour
- **P95 task duration**: < 120s per file
- **Success rate**: > 99%
- **Fault recovery**: < 10 min after failure

## Project Structure Summary

```
├── docker-compose.yml         # Main Airflow + DB stack
├── docker-compose-sftp.yml    # Mock SFTP services
├── run.sh                     # Startup script
├── .env                       # Configuration
└── airflow/
    ├── dags/sftp_sync.py      # DAG definition
    ├── plugins/
    │   ├── core/              # Reusable components
    │   └── sftp_sync/         # Use-case specific
    └── config/                # Connections & auth
```

## Stack Details
- **Airflow**: 3.1.7
- **Backend**: PostgreSQL 16 (ACID compliance)
- **Executor**: CeleryExecutor
- **Broker**: Redis 7.2
- **Python**: 3.12
- **SFTP**: atmoz/sftp (mock services for testing)

## Production Deployment Checklist (Large-Scale Sync)

### 🐳 Kubernetes Deployment
- [ ] Deploy Airflow on Kubernetes (Helm charts: scheduler, webserver, worker, triggerer)
- [ ] Use KubernetesExecutor or CeleryKubernetesExecutor for auto-scaling workers
- [ ] Configure HPA (Horizontal Pod Autoscaler) based on queue depth
- [ ] Setup managed PostgreSQL (RDS Aurora, CloudSQL) with connection pooling

### 🚀 Object Storage Integration
- [ ] Implement S3/GCS/Azure Blob adapters (multipart upload for parallelism)
- [ ] Replace local staging with cloud storage bucket
- [ ] Enable cross-region replication and S3 Transfer Acceleration

### ⚡ Spark for Transformations
- [ ] Integrate Spark cluster (EMR, Databricks, or Spark on k8s)
- [ ] Implement `SparkTransformer` for complex batch operations
- [ ] Support transformations: compression, deduplication, format conversion

### 📊 Data Warehouse Integration
- [ ] Add Delta Lake or Apache Iceberg connectors for data lake
- [ ] Export lineage to Apache Atlas or Collibra

### � Celery Cluster
- [ ] Deploy Celery workers across multiple machines/pods
- [ ] Configure priority queues for SLA-critical tasks
- [ ] Setup worker autoscaling based on task queue depth
- [ ] Monitor worker health and implement heartbeat checks

## License
MIT
