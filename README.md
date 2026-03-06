# Data Sync Platform

A production-grade incremental file synchronization platform for data movement at scale. Transfer files between SFTP, S3, GCS, or data warehouses with built-in parallelism, transformation, and reliability.

**Status**: Production-ready | **Language**: Python 3.12 | **Orchestration**: Apache Airflow 3.1.7 | **Executor**: CeleryExecutor

---

## What It Does

- **Incremental Sync**: Only transfers new/modified files (detects via size + mtime)
- **Parallel Execution**: 1 file = 1 task; scales from 10 to 10K files/run
- **Transformations**: Built-in gzip compression; extensible for custom pipelines
- **Idempotent**: Safe to re-run without duplicates
- **Lineage Tracking**: Auto-generated data catalog with source → staging → target lineage
- **Reliability**: Exponential backoff retry (4 retries, max 10min delay); fails on data quality anomalies

---

## Quick Start (5 minutes)

### Prerequisites
```bash
# System requirements
- Docker & Docker Compose (v2.0+)
- 4GB+ RAM
- 10GB free disk (for staging)
```

### Start the Platform
```bash
./run.sh
```

This starts:
- **Airflow UI**: http://localhost:8080 (admin/admin)
- **PostgreSQL**: Metadata database
- **Redis**: Task broker
- **SFTP Services**: Mock source/target for testing

### Run Your First Sync
```bash
# Verify platform started
docker compose exec airflow-webserver airflow dags list | grep sftp_sync

# Unpause sync job
docker compose exec airflow-webserver airflow dags unpause sftp_sync

# Trigger sync
docker compose exec airflow-webserver airflow dags trigger sftp_sync

# Monitor progress
docker compose exec airflow-webserver airflow dags list-runs sftp_sync
```

---

## How It Works

### Data Flow (Simple Path)
```
Source Files → Scan & Plan → Parallel Download → Transform → Parallel Upload → Validate → Target
```

**4 Main Stages:**
1. **Checker**: Scan source, compare with target metadata, plan what to transfer
2. **Download** (parallel): Fetch files to staging area
3. **Upload** (parallel): Transform & store to target
4. **Recheck**: Validate success count; fail if mismatches detected

### Architecture
```
data-sync-platform/
├── docker-compose.yml          # Multi-service orchestration
├── airflow/
│   ├── dags/
│   │   └── sftp_sync.py        # DAG: thin orchestration layer
│   ├── plugins/
│   │   ├── core/               # Reusable components
│   │   │   ├── io_adapters.py  # Connector interface (S3, GCS, SFTP, etc.)
│   │   │   ├── transformers.py # Transformation engine (gzip, custom)
│   │   │   ├── config.py       # Configuration models
│   │   │   └── metadata_builder.py  # Data catalog generator
│   │   └── sftp_sync/          # Use-case specific logic
│   │       ├── dag_factory.py  # DAG builder
│   │       └── tasks.py        # Task implementations
│   └── config/
│       ├── airflow.cfg         # Airflow settings
│       └── connection.json     # Connector credentials
└── README.md
```

**Key Design**: Adapter pattern separates connectors from orchestration
- Add new source/target without touching DAG
- Example: Add `S3SourceAdapter` → automatically available as sync option

---

## Configuration

### Set Defaults (.env)
```bash
# Backend
AIRFLOW__CORE__EXECUTOR=CeleryExecutor
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql://airflow:airflow@postgres:5432/airflow
AIRFLOW__CELERY__BROKER_URL=redis://redis:6379/0

# Sync parameters
SFTP_SOURCE_HOST=sftp-source
SFTP_TARGET_HOST=sftp-target
```

### Override at Runtime
```bash
# Custom paths, batch size, transformations
docker compose exec airflow-webserver airflow dags trigger sftp_sync --conf '{
  "source_base_path": "/custom/source",
  "target_base_path": "/custom/target",
  "batch_size": 1000,
  "max_file_size_mb": 512,
  "transformations": ["gzip"]
}'
```

---

## How to Extend

### Add a New Connector (e.g., S3)
```python
# airflow/plugins/core/io_adapters.py

class S3SourceAdapter(SourceAdapter):
    def list_files(self, root_path: str) -> list[FileMetadata]:
        # List S3 objects with metadata
        pass
    
    def retrieve_file(self, remote_path: str, local_path: str) -> None:
        # Download from S3 to local staging
        pass
```

Then use: `airflow dags trigger sftp_sync --conf '{"source_type": "s3"}'`

### Add a Transformation (e.g., Encrypt)
```python
# airflow/plugins/core/transformers.py

def apply_transformations(file_path, transforms):
    if "encrypt" in transforms:
        output = encrypt_file(file_path)  # Your logic
    return output
```

Then use: `--conf '{"transformations": ["gzip", "encrypt"]}'`

---

## Operations

### Monitor Sync Progress
```bash
# View runs
docker compose exec airflow-webserver airflow dags list-runs sftp_sync

# View logs (JSON format for parsing)
docker compose logs airflow-worker | grep "task_instance"

# View task details
docker compose exec airflow-webserver airflow tasks list sftp_sync
```

### Scale for Large Datasets
```bash
# Add more parallel workers
docker compose up --scale airflow-worker=5 -d

# Monitor queue
docker compose logs airflow-scheduler | grep "scheduled"
```

### Backup & Recovery
```bash
# Backup metadata
docker compose exec postgres pg_dump -U airflow airflow > backup.sql

# Restore metadata
docker compose exec -T postgres psql -U airflow airflow < backup.sql

# Clean up staging files
rm -rf airflow/data/sftp_sync_staging/*
```

---

## Features Overview

### ✅ Built-In Features
| Feature | Details |
|---------|---------|
| **Incremental Detection** | Size + mtime comparison (misses silent corruption but fast) |
| **Idempotency** | Re-run safe; existing files skipped |
| **Parallelism** | N tasks for N files; scales horizontally |
| **Transformations** | gzip out-of-box; custom handlers extensible |
| **Anomaly Detection** | Skip oversized files (>256MB), fail on count mismatch |
| **Lineage** | Auto-generated data catalog with Airflow DAG visualizer |
| **Retry Logic** | Exponential backoff with configurable limits |
| **Multi-Tenant** | Single workspace (not multi-tenant) |

### ❌ By Design (Trade-offs)
| Not Included | Reason | Alternative |
|--------------|--------|-------------|
| **Checksum validation** | Overhead; throughput favored | Can add as optional flag |
| **Delete propagation** | Append-only pattern simpler | Requires new adapter |
| **Advanced schema handling** | File-based, not columnar | Extend with Spark transformer |
| **Multi-region failover** (dev) | Future production feature | Deploy each region separately |

---

## Observability

### Logs
```bash
# View structured logs (JSON)
docker compose logs airflow-scheduler | grep '"event":'
```

### Metrics (XCom)
Each run produces: `{"planned": N, "downloaded": N, "uploaded": N, "failed": 0}`

### UI Dashboard
- Airflow UI shows task duration, success rate, data lineage graph
- Gantt chart identifies bottlenecks
- Task logs available for each stage

### Recommended Monitoring Stack (Production)
- [ ] Prometheus (metrics export)
- [ ] Grafana (dashboards)
- [ ] Datadog/CloudWatch (alerts, cost tracking)
- [ ] PagerDuty (on-call)

---

## Performance Targets

**Based on 1 worker, 256MB max file size:**
- **Throughput**: 200-300 files/hour
- **P95 task duration**: <120s per file
- **Success rate**: >99%
- **Fault recovery**: <10 min after failure

**Scaling**: Add 1 worker → roughly 2x throughput (up to 10 workers tested)

---

## Testing

### Smoke Test
```bash
./run.sh && sleep 60
docker compose exec airflow-webserver airflow dags list | grep sftp_sync
docker compose exec airflow-webserver airflow dags trigger sftp_sync
sleep 120
docker compose exec airflow-webserver airflow dags list-runs sftp_sync
# Expected: 1 successful run
```

### Functional Test
```bash
# Create test files on source
docker exec sftp-source sh -c 'mkdir -p /home/source/a/b/c && echo "test" > /home/source/a/b/c/test.txt'

# Run sync
docker compose exec airflow-webserver airflow dags trigger sftp_sync
sleep 120

# Verify on target
docker exec sftp-target cat /home/target/a/b/c/test.txt
# Expected: "test"
```

### Load Test (1000 files)
```bash
# Generate files
docker exec sftp-source sh -c 'mkdir -p /home/source/a/b/c && for i in $(seq 1 1000); do echo $i > /home/source/a/b/c/file_$i.txt; done'

# Run sync
docker compose exec airflow-webserver airflow dags trigger sftp_sync

# Monitor UI for parallelism, duration, success%
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **Admin user not found** | Run `./run.sh` again to ensure initialization completes. Or create manually: `echo '{"admin": "admin"}' > airflow/config/simple_auth_manager_passwords.json && docker compose restart` |
| **DAG not visible** | `docker compose exec airflow-scheduler airflow dags list-import-errors` |
| **Tasks stuck in queued** | Check workers: `docker compose ps airflow-worker` then `docker compose up --scale airflow-worker=5 -d` |
| **Module import errors** | Ensure plugin __init__.py files exist: `touch airflow/plugins/{,core,sftp_sync}/__init__.py` |
| **PostgreSQL connection error** | `docker compose down -v && docker compose up -d` (clean slate) |
| **Staging disk full** | `rm -rf airflow/data/sftp_sync_staging/*` |

---

## For Developers

### Project Structure
```
├── docker-compose.yml         # All services defined here
├── .env                       # Environment configuration
├── run.sh                     # One-command startup
├── airflow/dags/sftp_sync.py  # DAG entry point
├── airflow/plugins/
│   ├── core/                  # Reusable library
│   │   ├── io_adapters.py    # SourceAdapter/TargetAdapter ABC
│   │   ├── transformers.py   # Transformation pipeline
│   │   ├── config.py         # Config dataclasses
│   │   └── metadata_builder.py # Data catalog
│   └── sftp_sync/             # SFTP-specific implementation
│       ├── dag_factory.py    # DAG builder
│       └── tasks.py          # Task logic (checker, download, upload, recheck)
└── airflow/config/
    ├── airflow.cfg           # Airflow settings
    └── connection.json       # Auto-imported credentials
```

### Design Patterns Used
1. **Adapter Pattern**: Add S3/GCS/Azure without modifying DAG
2. **Builder Pattern**: DAG factory constructs orchestration
3. **Pipeline Pattern**: Transformations chained together

### Key Decisions & Trade-offs

| Decision | Rationale | Trade-off |
|----------|-----------|-----------|
| **PostgreSQL** | ACID compliance, concurrent writes | More complex than SQLite |
| **CeleryExecutor** | Horizontal scaling, proven | Redis dependency |
| **Adapter pattern** | Connector independence | Small abstraction overhead |
| **Size+mtime only** | Fast incremental detection | Misses silent corruption |
| **Per-run staging** | Parallel run isolation | Disk space overhead |

---

## For DataOps / Platform Engineers

### Production Deployment Checklist

#### Infrastructure
- [ ] **Kubernetes**: Deploy Airflow components as separate pods (scheduler, webserver, worker, triggerer)
- [ ] **Auto-scaling**: Configure HPA based on task queue depth
- [ ] **Database**: Use managed PostgreSQL (RDS Aurora, CloudSQL) with connection pooling
- [ ] **Message Broker**: Deploy Celery Redis cluster for high availability

#### Storage & Connectors
- [ ] **Object Storage**: Implement S3/GCS/Azure Blob adapters (multipart upload for parallelism)
- [ ] **Staging**: Replace local staging with cloud object storage bucket
- [ ] **Data Lake**: Add Delta Lake or Iceberg connectors for analytics

#### Processing
- [ ] **Spark**: Integrate Spark cluster (EMR, Databricks) for complex batch transformations
- [ ] **Pipeline**: Extend transformer engine for compression, deduplication, format conversion

#### Observability
- [ ] **Metrics**: Export to Prometheus; ingest into Grafana
- [ ] **Alerts**: Setup SLA monitoring (failure rate, duration anomalies, API throttling)
- [ ] **Lineage**: Export to Apache Atlas or Collibra for data governance
- [ ] **Cost**: Track compute + storage + data transfer via CloudWatch/Datadog

#### Reliability
- [ ] **Backup**: Daily automated PostgreSQL backups with 30-day retention
- [ ] **Disaster Recovery**: Multi-region deployment with RTO/RPO targets
- [ ] **Secrets**: Use AWS Secrets Manager or HashiCorp Vault for credential rotation
- [ ] **Network**: VPC isolation, VPC endpoints for S3/data warehouse access

#### Scaling
- [ ] **Celery Cluster**: Deploy workers across machines; configure priority queues
- [ ] **Dynamic Tasks**: Use task mapping + grouping to avoid metadata bloat (1000+ files/task)
- [ ] **Worker Autoscaling**: Scale based on queue depth; monitor health via heartbeats

---

## Tech Stack

- **Python**: 3.12
- **Orchestration**: Apache Airflow 3.1.7 (CeleryExecutor)
- **Database**: PostgreSQL 16
- **Message Broker**: Redis 7.2
- **Containerization**: Docker Compose v2.0+
- **SFTP** (dev/testing): atmoz/sftp

---

## License
MIT

---

## Quick Links
- [Architecture Details](./docs/ARCHITECTURE.md) — Design patterns, extensibility
- [Operational Guide](./docs/OPERATIONS.md) — Monitoring, scaling, troubleshooting
- [Deployment Guide](./docs/DEPLOYMENT.md) — Production setup, HA/DR
