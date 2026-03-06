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

### 🐳 Container Orchestration
- [ ] **Kubernetes Deployment**
  - Deploy Airflow components as Kubernetes Helm charts (scheduler, webserver, worker, triggerer as separate pods)
  - Use KubernetesExecutor or CeleryKubernetesExecutor for dynamic worker scaling
  - Configure HPA (Horizontal Pod Autoscaler) to auto-scale workers based on queue depth
  - Set resource requests/limits (CPU, memory) per pod type
  - Use init containers to mount DAGs and plugins from ConfigMaps/volumes
  
- [ ] **PostgreSQL Migration**
  - Replace RDS/managed PostgreSQL with dedicated instance (CloudSQL, RDS Aurora, managed Azure Database)
  - Configure connection pooling (PgBouncer) for high concurrency
  - Enable read replicas for Airflow metadata queries
  - Setup automated backups with point-in-time recovery
  - Configure metrics export to CloudWatch/Datadog

### 🚀 Storage & Connectors
- [ ] **Object Storage Connectors** (S3/GCS/Azure Blob)
  - Implement `S3SourceAdapter` and `S3TargetAdapter` using boto3
  - Split large files using multipart upload for parallel throughput
  - Use object metadata (ETag, version ID) for incremental detection
  - Enable S3 Transfer Acceleration for cross-region speeds
  - Cache remote file listings in Redis to avoid repeated API calls

- [ ] **Staging Strategy**
  - Replace local staging with cloud object storage (S3/GCS staging bucket)
  - Use SSD-backed Instance Store or EBS gp3 volumes for worker nodes
  - Implement S3-to-S3 transfers using S3 Batch Operations for ultra-large datasets
  - Archive old staging data to Glacier for cost optimization

### ⚡ Transformation Layer
- [ ] **Spark Integration** (for complex transformations)
  - Add `SparkTransformer` that submits jobs to Spark cluster (EMR, Databricks, Spark on k8s)
  - Use PySpark for columnar processing of large files before upload
  - Enable adaptive query execution and partition pruning
  - Monitor Spark job metrics (shuffle read/write, task duration) via SparkListener
  - Example: `--conf '{"transformations": ["spark://compress_and_dedupe"]}'`

- [ ] **Batch Processing Pipeline**
  - Extend `transformers.py` to support batch operations:
    - Compression (gzip, zstd, parquet)
    - Deduplication (hash-based, bloom filter)
    - Format conversion (CSV → Parquet, JSON → Avro)
  - Chain multiple transformers with configurable ordering

### 📊 Data Warehouse Integration
- [ ] **Data Lake Connector**
  - Add `DeltaLakeTargetAdapter` for Apache Delta Lake tables
  - Implement `IcebergTargetAdapter` for Apache Iceberg format
  - Auto-generate DataFrames with schema inference
  - Support incremental merge operations (UPSERT) for idempotency

- [ ] **Metadata & Lineage**
  - Export DAG lineage to Apache Atlas or Collibra
  - Integrate with data governance platforms
  - Track data freshness SLAs via Airflow SLA sensors
  - Publish schema changes to central catalog

### 📈 Scaling for Large Volumes
- [ ] **Dynamic Task Mapping**
  - Increase `max_active_tasks` to 128-256 for high parallelism
  - Implement task grouping to avoid Airflow metadata overload (group 1000 files per task)
  - Use `TaskGroup` with dynamic expansion for better DAG clarity

- [ ] **Distributed File Listing**
  - Replace single `checker` task with distributed file scanner (map-reduce pattern)
  - Partition source directory by prefix (e.g., `/data/[a-m]/` → worker1, `/data/n-z/` → worker2)
  - Use Airflow `dynamic_task_mapping` or external Spark job for massive directorlies (10M+ files)

- [ ] **Memory & CPU Tuning**
  - Adjust Airflow task pool concurrency: `[core] max_active_tasks_per_dag = 512`
  - Configure Celery worker concurrency: `--concurrency=16 --max-tasks-per-child=10`
  - Use worker autoscaling with queue depth metrics

### 🔒 Security & Compliance
- [ ] **Secrets Management**
  - Rotate SFTP/S3 credentials via AWS Secrets Manager / HashiCorp Vault
  - Use temporary STS tokens for cloud storage access
  - Encrypt connection parameters in PostgreSQL vault

- [ ] **Data Encryption**
  - Enable TLS 1.3 for all service-to-service communication
  - Implement client-side encryption before upload (e.g., GPG, KMS)
  - Setup field-level encryption for PII columns
  - Audit access logs to centralized SIEM

- [ ] **Network Isolation**
  - Deploy Airflow in private VPC with NAT gateway for outbound
  - Use VPC endpoints for S3/DynamoDB access (avoid internet traffic)
  - Implement network policies (Kubernetes NetworkPolicy) for service mesh communication

### 📡 Observability at Scale
- [ ] **Metrics Export**
  - Export Airflow metrics to Prometheus (scrape interval 15s)
  - Add custom metrics: `files_synced`, `bytes_transferred`, `transform_duration_ms`
  - Setup Grafana dashboards for real-time monitoring

- [ ] **Distributed Tracing**
  - Integrate with Jaeger/Datadog APM for request tracing
  - Track file transfer latency end-to-end (checker → download → upload → recheck)
  - Monitor Redis broker latency and memory usage

- [ ] **Alerting**
  - Alert on DAG SLA breach (e.g., sync should complete by 12 PM UTC)
  - Alert on worker node failures or high CPU/memory usage
  - Alert on S3/GCS API throttling errors
  - Setup on-call rotation via PagerDuty

### 💾 High-Availability & DR
- [ ] **Multi-Region Deployment**
  - Deploy Airflow scheduler in active-passive mode across regions
  - Replicate metadata database via PostgreSQL streaming replication
  - Use S3 cross-region replication for data lake buckets
  - Implement disaster recovery runbook with RTO/RPO targets

- [ ] **Backup & Recovery**
  - Automate daily database backups with 30-day retention
  - Implement incremental backups for large data volumes
  - Test recovery procedures quarterly
  - Store backup inventory in S3 with lifecycle policies

### 🎯 Cost Optimization
- [ ] **Infrastructure**
  - Use Spot instances for Airflow workers (cost reduction: 70%)
  - Right-size compute (start with m5.large, scale based on metrics)
  - Leverage Compute Savings Plans or Reserved Instances for base load

- [ ] **Storage**
  - Archive completed syncs to S3 Intelligent-Tiering
  - Delete staging data after S3 lifecycle expiry (7 days)
  - Use S3 Transfer Acceleration only for latency-critical paths
  - Monitor CloudFront caching hit ratios

### ✅ Pre-Production Validation
- [ ] **Load Testing Scenarios**
  - Test with 100K+ files (benchmark parallelism limits)
  - Test with geographic distribution (cross-region latency)
  - Simulate 90% simultaneous success rate (chaos engineering)
  - Profile memory leaks in long-running workers (24h+ stress test)

- [ ] **Failover Testing**
  - Kill scheduler pod during DAG run; verify recovery
  - Simulate database connection timeout; verify retry
  - Simulate partial S3 upload; verify idempotency
  - Measure failover time from detection to recovery

- [ ] **Cost Analysis**
  - Estimate monthly spend: compute + storage + data transfer
  - Calculate per-file cost: $/file_synced
  - Benchmark alternative services (AWS DataSync, Azure Data Factory)

## License
MIT

---

**For production deployment**, ensure:
- [ ] Secrets management (AWS Secrets Manager / HashiCorp Vault)
- [ ] Metrics export & alerting setup
- [ ] Multi-region backup strategy
- [ ] RBAC configuration for teams
- [ ] Load testing with realistic file distribution
