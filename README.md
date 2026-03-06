# Take-home: Extensible Airflow File Sync (Staff-level design)

## 1) Mục tiêu
Repository này triển khai một DAG đồng bộ file **incremental + idempotent** theo hướng kiến trúc có thể mở rộng:
- Nguồn/đích mặc định: `SFTP -> SFTP`
- Sẵn đường mở rộng để chuyển nguồn sang object storage (S3, GCS, Azure Blob) mà không phải viết lại DAG orchestration
- Cho phép thêm transformation trước khi load (`noop`, `gzip`, và dễ dàng thêm các bước transform khác)
- Có chiến lược xử lý anomaly (đặc biệt là file lớn)
- Built-in metadata catalog và data lineage tracking
- Horizontal scaling với CeleryExecutor + dynamic task mapping

## 2) Cấu trúc deliverable
```
.
├── docker-compose.yml              # Airflow 3.1.7 với PostgreSQL + Redis + Celery
├── docker-compose-sftp.yml         # Mock SFTP source/target cho testing
├── run.sh                          # Script khởi động toàn bộ stack
├── .env                            # Environment configuration
├── airflow/
│   ├── dags/
│   │   └── sftp_sync.py           # DAG entrypoint - thin configuration layer
│   ├── plugins/
│   │   ├── core/                  # ⭐ Core abstractions - engine for any sync pipeline
│   │   │   ├── io_adapters.py     # Adapter pattern cho source/target connectors
│   │   │   ├── transformers.py    # Transformation pipeline engine
│   │   │   ├── config.py          # Configuration models với defaults
│   │   │   ├── connections.py     # Connection configuration
│   │   │   ├── assets.py          # Data asset & lineage builder
│   │   │   ├── metadata_builder.py # Data catalog metadata generator
│   │   │   └── dag_builder.py     # Base DAG builder pattern
│   │   └── sftp_sync/             # Business logic cho SFTP sync use case
│   │       ├── dag_factory.py     # SFTP-specific DAG builder
│   │       └── tasks.py           # Task implementations (checker/download/upload/recheck)
│   ├── config/
│   │   ├── airflow.cfg            # Airflow configuration
│   │   ├── connection.json        # Auto-imported connections
│   │   └── simple_auth_manager_passwords.json
│   └── data/
│       └── sftp_sync_staging/     # Shared staging area cho file transfers
└── README.md
```

## 3) Khởi động project
### 3.1 Yêu cầu ban đầu
- Docker Desktop (hoặc Docker Engine + Docker Compose plugin)
- Tối thiểu 4GB RAM cho Docker

### 3.2 Chạy toàn bộ stack
```bash
./run.sh
```

Lệnh trên sẽ chạy:
- Airflow stack từ `docker-compose.yml`
- SFTP mock stack từ `docker-compose-sftp.yml`

### 3.3 Truy cập
- Airflow UI: `http://localhost:8080`
- User mặc định: `admin`
- Password mặc định: `admin`

### 3.4 Kiểm tra nhanh DAG
```bash
docker compose exec airflow-webserver airflow dags list
docker compose exec airflow-webserver airflow dags list-runs sftp_sync --no-backfill
```

### 3.5 Trigger thủ công với runtime override
```bash
docker compose exec airflow-webserver airflow dags trigger sftp_sync \
  --conf '{
    "source_conn_type": "sftp",
    "source_conn_id": "sftp_source",
    "target_conn_type": "sftp",
    "target_conn_id": "sftp_target",
    "source_base_path": "/a",
    "target_base_path": "/a",
    "batch_size": 200,
    "max_file_size_mb": 256,
    "transformations": ["noop"]
  }'
```

## 4) Kiến trúc DAG & Design Patterns

### 4.1 Pipeline Flow
DAG `sftp_sync` có luồng xử lý 4 bước với dependency rõ ràng:

```
checker (single) → download (mapped) → upload (mapped) → recheck (single)
     ↓                  ↓                   ↓                ↓
  plan work       retrieve files      transform+store    validate result
```

**Chi tiết từng bước:**
1. **`checker`**: 
   - Quét source files, compare với target metadata
   - Apply size filter (skip oversized files)
   - Return danh sách incremental work items
   - Emit staging asset cho lineage tracking
   
2. **`download` (dynamic mapped)**:
   - Expand thành N parallel tasks theo số files
   - Download file vào shared staging area `/opt/airflow/data/sftp_sync_staging/<run_id>/`
   - Return staging file path cho upload step
   
3. **`upload` (dynamic mapped)**:
   - Expand thành N parallel tasks
   - Apply transformation pipeline (noop → gzip → custom...)
   - Ensure parent directories exist
   - Upload transformed file lên target
   - Preserve source mtime metadata
   - Cleanup staging file sau khi thành công
   - Emit target asset cho lineage tracking
   
4. **`recheck`**:
   - So sánh planned vs uploaded count
   - Fail DAG run nếu có mismatch (data quality gate)
   - Return summary metrics

### 4.2 Core Design Patterns

#### 4.2.1 **Adapter Pattern** (để mở rộng connectors)
```python
# Interface contracts
class SourceAdapter(ABC):
    @abstractmethod
    def list_files(self, root_path: str) -> list[FileMetadata]: ...
    @abstractmethod
    def retrieve_file(self, remote_path: str, local_path: str) -> None: ...

class TargetAdapter(ABC):
    @abstractmethod
    def stat(self, path: str) -> FileMetadata | None: ...
    @abstractmethod
    def store_file(self, local_path: str, remote_path: str) -> None: ...
    @abstractmethod
    def set_mtime(self, path: str, mtime: int) -> None: ...

# Factory cho runtime selection
def create_source_adapter(conn_type: str, conn_id: str) -> SourceAdapter:
    if conn_type == "sftp": return SFTPSourceAdapter(conn_id)
    if conn_type == "s3": return S3SourceAdapter(conn_id)      # ← Ready to add
    raise ValueError(f"Unsupported: {conn_type}")
```

**Ưu điểm:**
- ✅ Thêm connector mới (S3, GCS, Azure Blob) chỉ cần implement interface, không động DAG logic
- ✅ Test riêng từng adapter với mock
- ✅ Swap connector chỉ bằng cách thay `conn_type` parameter

**Ví dụ mở rộng sang S3:**
```python
class S3SourceAdapter(SourceAdapter):
    def __init__(self, conn_id: str):
        self.hook = S3Hook(aws_conn_id=conn_id)
    
    def list_files(self, root_path: str) -> list[FileMetadata]:
        # boto3 list_objects_v2 với prefix=root_path
        ...
    
    def retrieve_file(self, remote_path: str, local_path: str) -> None:
        # boto3 download_file
        ...
```

Sau khi implement, chỉ cần:
```bash
airflow dags trigger sftp_sync --conf '{
  "source_conn_type": "s3",
  "source_conn_id": "my_s3_conn",
  "target_conn_type": "sftp",
  ...
}'
```

#### 4.2.2 **Pipeline Pattern** (transformation chain)
```python
def apply_transformations(filepath: str, transformations: list[str]) -> str:
    output = filepath
    for step in transformations:
        if step == "noop": continue
        if step == "gzip":
            output = gzip_file(output)  # output.gz, remove original
        if step == "encrypt":
            output = encrypt_file(output)  # ← Easy to add
        # ... more steps
    return output
```

**Ưu điểm:**
- ✅ Chain nhiều transformations tuần tự (gzip → encrypt → checksum)
- ✅ Thêm transformation mới không cần sửa orchestration
- ✅ Declarative config: `transformations: ["gzip", "encrypt"]`

#### 4.2.3 **Builder Pattern** (DAG construction)
```python
class SFTPSyncDagBuilder(BaseDagBuilder):
    def __init__(self, config, connections):
        self.config = config
        self.connections = connections
        self.assets = build_sftp_sync_assets(...)
        self.catalog_metadata = PipelineMetadataBuilder().build()
    
    def register_tasks(self, dag: DAG) -> None:
        # Wire tasks với dependencies
        checker >> download >> upload >> recheck
```

**Ưu điểm:**
- ✅ Tách logic DAG construction khỏi business logic
- ✅ Reusable builder cho nhiều DAGs tương tự
- ✅ Centralized metadata generation và lineage tracking

#### 4.2.4 **Factory Pattern** (runtime config override)
Task `checker` hỗ trợ override via `dag_run.conf`:
```python
def checker(..., **context):
    conf = context.get("dag_run", {}).conf or {}
    source_conn_type = conf.get("source_conn_type", default_source_conn_type)
    batch_size = int(conf.get("batch_size", default_batch_size))
    # ... apply overrides
```

**Ưu điểm:**
- ✅ Test scenarios khác nhau không cần tạo DAG mới
- ✅ A/B testing với configs khác nhau
- ✅ Emergency override trong production

### 4.3 Data Lineage & Observability

#### Assets & Lineage Tracking
Mỗi task declare `inlets` và `outlets` để Airflow track data flow:
```python
checker_task = PythonOperator(
    inlets=[assets.source],      # sftp://source/path/
    outlets=[assets.staging],    # file:///staging/<run_id>/
)
upload_task = PythonOperator(
    inlets=[assets.staging],
    outlets=[assets.target],     # sftp://target/path/
)
```

→ Airflow UI hiển thị lineage graph: `source → staging → target`

#### Metadata Catalog
DAG tự động generate metadata JSON và embed vào `dag.doc_md`:
```json
{
  "pipeline_id": "sftp_sync",
  "assets": [
    {"role": "source", "conn_type": "sftp", "uri": "sftp://sftp_source/a"},
    {"role": "target", "conn_type": "sftp", "uri": "sftp://sftp_target/a"}
  ],
  "processing_components": [
    {"name": "checker", "component_type": "work-planner"},
    {"name": "download", "component_type": "extractor"},
    {"name": "gzip", "component_type": "transformation"},
    {"name": "upload", "component_type": "loader"},
    {"name": "recheck", "component_type": "quality-gate"}
  ],
  "orchestration": {
    "schedule": "0 8 * * *",
    "max_active_runs": 1,
    "max_active_tasks": 32
  }
}
```

**Use cases:**
- ✅ Export qua API để populate Data Catalog platform (DataHub, Amundsen, etc.)
- ✅ Audit trail: track data flow end-to-end
- ✅ Impact analysis: hiểu downstream/upstream dependencies

## 5) Scalability & Performance Strategy

### 5.1 Horizontal Scaling
**Current implementation:**
- ✅ **CeleryExecutor** với Redis broker
- ✅ **Dynamic Task Mapping**: `download` và `upload` expand thành N parallel tasks theo số files
- ✅ **Worker Pool**: có thể scale từ 1 → 100+ workers bằng `docker compose up --scale airflow-worker=10`
- ✅ **Task Pool**: config `pool="default_pool"` để limit concurrent tasks
- ✅ **PostgreSQL**: metadata backend ổn định, tránh SQLite lock issues

**Capacity planning:**
```
Throughput ≈ (num_workers × max_active_tasks_per_worker) / avg_task_duration

Example:
- 10 workers
- 16 max_active_tasks/worker
- 30s avg task duration
→ ~320 tasks/min ≈ 19,200 files/hour (with 1 task = 1 file)
```

**Scaling strategy:**
| File count     | Recommendation                                  |
|----------------|-------------------------------------------------|
| < 1,000        | 1 worker, `max_active_tasks=16` (default)      |
| 1K - 10K       | 3-5 workers, `max_active_tasks=32`             |
| 10K - 100K     | 10-20 workers, `batch_size=1000`, tune `pool`  |
| > 100K         | Consider batching by date/partition + multiple DAG runs |

### 5.2 Vertical Optimization
**Implemented optimizations:**
1. **Lazy connection pooling**: Adapter chỉ tạo connection khi cần (trong task runtime)
2. **Staged cleanup**: Chỉ xóa staging file sau khi upload thành công
3. **Per-run staging isolation**: `/staging/<run_id>/` tránh conflicts giữa các DAG runs
4. **Metadata comparison**: So sánh `size + mtime` thay vì download+checksum (fast path)

**Memory footprint:**
- Checker task: O(N) với N = số files (store metadata list in XCom)
- Download/Upload tasks: O(1) mỗi task chỉ xử lý 1 file
- Staging disk space: `max_file_size_mb × max_active_tasks × num_workers`

### 5.3 Anomaly Handling

#### 5.3.1 Large File Protection
```python
max_file_size_mb = 256  # Configurable guard
# Files > 256MB → skip và log oversized_file_count
```

**Rationale:**
- Tránh OOM khi worker memory hạn chế
- Prevent long-running tasks block worker slot
- Có thể process large files riêng với dedicated worker pool

#### 5.3.2 Retry Strategy
- **Task-level retry**: 4 retries với exponential backoff (30s → 1m → 2m → 4m)
- **Max retry delay**: 10 minutes
- **Execution timeout**: 
  - `checker`: 10 minutes
  - `download/upload`: 20 minutes mỗi file
  - `recheck`: 5 minutes

#### 5.3.3 Failure Modes & Recovery
| Failure Scenario             | Behavior                                    | Recovery                              |
|------------------------------|---------------------------------------------|---------------------------------------|
| Source connection timeout    | Retry 4 lần với backoff                     | Auto-recover nếu transient            |
| Target write failure         | Task fail, giữ staging file                 | Manual inspection + re-trigger        |
| Partial upload (N of M files)| `recheck` fail run với summary metrics     | Re-run DAG (incremental check skip đã sync) |
| Out of disk space            | Task fail với OSError                       | Alert + expand disk + re-trigger      |
| Worker crash mid-task        | Celery detect + reschedule task             | Auto-retry on otro worker             |

#### 5.3.4 Idempotency Guarantees
- ✅ **Incremental check**: `size + mtime` comparison → chỉ sync files mới/thay đổi
- ✅ **Overwrite semantic**: Upload ghi đè target file nếu source newer
- ✅ **Re-runnable**: Chạy lại DAG nhiều lần → same result (append-only model)
- ⚠️ **Not atomic**: Nếu worker crash giữa upload, target có thể có partial file (need external monitoring)

### 5.4 Observability & Monitoring

**Implemented:**
1. **Structured logging** (JSON):
   ```python
   print(json.dumps({
       "event": "checker",
       "source_file_count": 1500,
       "pending_file_count": 200,
       "oversized_file_count": 3
   }))
   ```
   
2. **XCom summary metrics**:
   ```python
   {"planned": 200, "uploaded": 198, "failed": 2}
   ```

3. **Airflow UI metrics**:
   - Task duration histograms
   - Success/failure rates
   - Gantt chart để identify bottlenecks

**Recommended additions** (beyond scope, nhưng production-ready cần có):
- [ ] StatsD/Prometheus metrics exporter
- [ ] Custom Airflow sensor để monitor target data freshness
- [ ] Alert rules cho: failure rate > 5%, duration > 2x P95, no success runs in 24h
- [ ] CloudWatch/Datadog dashboard cho end-to-end visibility

## 6) Architecture Decisions & Trade-offs (Staff-level analysis)

### 6.1 Core Assumptions
| Assumption | Rationale | Risk | Mitigation |
|------------|-----------|------|------------|
| **Size + mtime sufficient for incremental** | Fast comparison, no need download+hash | False positive nếu file bị corrupt with same size | Add optional checksum validation mode |
| **Append-only model** (no delete sync) | Simplifies logic, matches typical data lake pattern | Source file bị xóa nhưng target vẫn giữ | Add cleanup policy DAG riêng |
| **Single-region deployment** | Lower latency, simpler networking | No disaster recovery nếu region down | Multi-region với cross-region replication |
| **Ephemeral staging** (cleanup sau upload) | Save disk space | Mất staging nếu cần debug failed upload | Option to preserve staging for N days |

### 6.2 Key Design Decisions

#### 6.2.1 PostgreSQL vs SQLite
**Decision**: Dùng PostgreSQL cho Airflow metadata backend

**Rationale:**
- ✅ **Concurrent writes**: Celery workers + scheduler + webserver cùng write metadata
- ✅ **ACID compliance**: Đảm bảo task state consistency
- ✅ **Performance**: Index optimization cho large DAG history
- ✅ **Production-ready**: Backup/restore strategies mature

**Trade-off:**
- ❌ More complex setup (requires Docker volume + connection pooling)
- ❌ Overhead cho dev environment (SQLite đơn giản hơn)

#### 6.2.2 CeleryExecutor vs LocalExecutor vs KubernetesExecutor
**Decision**: CeleryExecutor với Redis broker

**Comparison:**
| Executor    | Pros | Cons | Use case |
|-------------|------|------|----------|
| **Local**   | Simple, no broker | No horizontal scaling | Dev/testing, < 100 tasks/day |
| **Celery**  | Horizontal scaling, mature | Redis dependency | **Production, 1K-1M tasks/day** |
| **K8s**     | Auto-scaling, resource isolation | Complex, K8s overhead | Cloud-native, > 1M tasks/day |

**Why Celery for this project:**
- ✅ Balance giữa simplicity và scalability
- ✅ Docker Compose đã hỗ trợ sẵn (no K8s cluster needed)
- ✅ Worker scale bằng `docker compose up --scale`

#### 6.2.3 Dynamic Task Mapping vs Batching
**Decision**: Dynamic task mapping với `expand()`

**Alternatives considered:**
1. **Single task loop through all files**: 
   - ❌ No parallelism
   - ❌ Retry toàn bộ nếu 1 file fail
   
2. **Fixed batch size (e.g., 100 files/task)**:
   - ⚠️ Unbalanced load nếu file sizes khác nhau
   - ⚠️ Retry batch lớn nếu 1 file trong batch fail
   
3. **Dynamic mapping (chosen)**:
   - ✅ Max parallelism (1 task = 1 file)
   - ✅ Granular retry (chỉ retry failed file)
   - ✅ Airflow UI visibility per-file
   - ❌ Overhead nếu > 10K files (XCom serialization)

**Mitigation cho > 10K files scenario:**
- Batch by date partition trước khi vào DAG
- Hoặc dùng `batch_size` parameter để chunk work items

#### 6.2.4 Adapter Pattern vs Provider Operators
**Decision**: Custom Adapter pattern thay vì dùng sẵn `SFTPOperator`/`S3Hook`

**Rationale:**
- ✅ **Unified interface**: `SourceAdapter` và `TargetAdapter` giống nhau cho mọi connector type
- ✅ **Testability**: Mock adapter dễ hơn mock Airflow hooks
- ✅ **Business logic separation**: Task code không phụ thuộc Airflow provider-specific APIs
- ✅ **Future-proof**: Dễ migrate sang non-Airflow orchestrator (Dagster, Prefect) nếu cần

**Trade-off:**
- ❌ Re-implement một số logic đã có trong Airflow providers
- ❌ Maintenance burden khi provider có breaking changes

#### 6.2.5 Staging Strategy
**Decision**: Shared staging area `/opt/airflow/data/sftp_sync_staging/<run_id>/`

**Alternatives:**
1. **Worker-local /tmp**: 
   - ❌ File không accessible nếu upload task chạy trên worker khác download task
   
2. **Shared NFS/EFS**:
   - ✅ Universal access, production-grade
   - ❌ Cost + complexity cho take-home project
   
3. **Shared Docker volume (chosen)**:
   - ✅ Simple setup với Docker Compose
   - ✅ All workers có cùng view của staging
   - ⚠️ Volume path `/opt/airflow/data` mounted from host

**Production recommendation**: Migrate to S3/GCS for staging (object storage)

### 6.3 What's NOT Implemented (và tại sao)

#### 6.3.1 Content Checksum Validation
**Status**: ⚠️ Not implemented

**Rationale:**
- Cost: Mỗi file cần read full content → 2x I/O overhead
- Benefit: Detect silent data corruption (rare với modern filesystems)
- **Trade-off**: Ưu tiên throughput over paranoid validation

**When to add:**
- Compliance requirements (financial, healthcare data)
- Known flaky storage backend

#### 6.3.2 Dead Letter Queue / Quarantine Storage
**Status**: ⚠️ Not implemented

**Current behavior**: Failed files → task fail → manual re-trigger

**Ideal behavior:**
- Failed files move to quarantine bucket
- Emit alert với file path + error details
- Separate DAG để process quarantine files

**Why not in take-home:**
- Adds complexity (need extra storage setup)
- Focus on happy path + retry strategy

#### 6.3.3 Schema Evolution & Transformation
**Status**: ⚠️ Basic transformations only (gzip)

**Future enhancements:**
- Parquet conversion (CSV → Parquet)
- Schema validation (JSON Schema, Avro schema registry)
- Column-level encryption
- Data masking (PII redaction)

**Extensibility**: `core/transformers.py` sẵn sàng để thêm transformations

#### 6.3.4 Multi-tenancy & Access Control
**Status**: ⚠️ Single tenant, shared Airflow instance

**Production needs:**
- DAGs per tenant/team
- Connection-level RBAC
- Resource quotas per team
- Cost allocation tags

**Implementation path:**
- Airflow 2.x+ có RBAC built-in
- Tag DAGs với `team_name` metadata
- Kubernetes namespaces cho resource isolation

### 6.4 Production Readiness Checklist

**Implemented (✅):**
- ✅ Incremental sync logic
- ✅ Idempotent re-runs
- ✅ Retry với exponential backoff
- ✅ Structured logging (JSON)
- ✅ Data lineage tracking (inlets/outlets)
- ✅ Metadata catalog generation
- ✅ Horizontal scaling (CeleryExecutor)
- ✅ Configuration override (dag_run.conf)
- ✅ Anomaly handling (large file skip)
- ✅ PostgreSQL backend (not SQLite)

**Recommended additions before production (📋):**
- [ ] Metrics export (StatsD/Prometheus)
- [ ] Alert rules (PagerDuty/Slack integration)
- [ ] SLA monitoring (custom Airflow sensor)
- [ ] Checksum validation mode (optional flag)
- [ ] Dead letter queue for failed files
- [ ] Cost tracking (S3 request metrics, worker CPU time)
- [ ] Secrets management (AWS Secrets Manager, Vault)
- [ ] Backup strategy (DAG code versioning, metadata DB snapshots)
- [ ] Disaster recovery plan (multi-region, RTO/RPO targets)
- [ ] Load testing (simulate 10K+ files)

### 6.5 Extensibility Roadmap

**Phase 1: More Connectors** (2-4 weeks)
- S3SourceAdapter / S3TargetAdapter
- GCSSourceAdapter / GCSTargetAdapter
- Azure Blob adapters
- HDFS adapters (for legacy Hadoop integration)

**Phase 2: Advanced Transformations** (4-6 weeks)
- Parquet conversion with PyArrow
- Schema validation (JSON Schema, Avro)
- Column-level encryption (AWS KMS, Age encryption)
- Data quality checks (Great Expectations integration)

**Phase 3: Enterprise Features** (8-12 weeks)
- Multi-tenancy với team isolation
- Cost allocation và chargeback
- SLA monitoring với auto-alerting
- Data catalog integration (DataHub, Amundsen)

**Phase 4: Performance Optimization** (ongoing)
- S3 multi-part upload cho large files
- Connection pooling với limits
- Adaptive batch sizing based on file size distribution
- Smart retry (exponential backoff + jitter)

## 7) Environment Configuration

### 7.1 Core Airflow Settings (`.env`)
```bash
# Executor configuration
AIRFLOW__CORE__EXECUTOR=CeleryExecutor

# Database backend (PostgreSQL for production stability)
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql://airflow:airflow@postgres:5432/airflow

# Celery broker & result backend
AIRFLOW__CELERY__BROKER_URL=redis://redis:6379/0
AIRFLOW__CELERY__RESULT_BACKEND=db+postgresql://airflow:airflow@postgres:5432/airflow

# Auth (SimpleAuthManager for dev simplicity)
AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS=admin:ADMIN
AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE=/opt/airflow/config/simple_auth_manager_passwords.json

# Performance tuning
AIRFLOW__CORE__PARALLELISM=32                    # Max concurrent tasks across all workers
AIRFLOW__CORE__MAX_ACTIVE_TASKS_PER_DAG=16       # Max concurrent tasks per DAG
AIRFLOW__CORE__MAX_ACTIVE_RUNS_PER_DAG=16        # Max concurrent DAG runs
AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=True  # Manual unpause for safety

# Misc
AIRFLOW__CORE__LOAD_EXAMPLES=false               # Disable example DAGs
AIRFLOW__WEBSERVER__EXPOSE_CONFIG=true           # Show config in UI (dev only)
```

### 7.2 SFTP Sync DAG Defaults
Defined in `airflow/dags/sftp_sync.py`:
```python
config = SFTPSyncDagConfig(
    dag_id="sftp_sync",
    schedule="0 8 * * *",           # Daily at 8 AM UTC
    max_active_tasks=32,            # High parallelism for file transfers
    max_active_runs=1,              # Prevent overlapping runs
    pool="default_pool",
    batch_size=500,                 # Limit XCom payload size
    source_base_path="/a",
    target_base_path="/a",
    max_file_size_mb=256,           # Skip files > 256 MB
    transformations=("noop",),      # No transformation by default
)

connections = SFTPSyncConnectionConfig(
    source_conn_type="sftp",
    source_conn_id="sftp_source",     # Auto-imported from config/connection.json
    target_conn_type="sftp",
    target_conn_id="sftp_target",
)
```

### 7.3 Runtime Override via `dag_run.conf`
Override bất kỳ parameter nào khi trigger:
```bash
docker compose exec airflow-webserver airflow dags trigger sftp_sync \
  --conf '{
    "source_conn_type": "sftp",
    "source_conn_id": "sftp_source",
    "target_conn_type": "sftp",
    "target_conn_id": "sftp_target",
    "source_base_path": "/b",
    "target_base_path": "/b",
    "batch_size": 1000,
    "max_file_size_mb": 512,
    "transformations": ["gzip"]
  }'
```

### 7.4 Connection Configuration
File `airflow/config/connection.json` (auto-imported at init):
```json
{
  "sftp_source": {
    "conn_type": "sftp",
    "host": "sftp-source",
    "port": 22,
    "login": "source",
    "password": "pass"
  },
  "sftp_target": {
    "conn_type": "sftp",
    "host": "sftp-target",
    "port": 22,
    "login": "target",
    "password": "pass"
  }
}
```

**Production best practice**: Use Airflow Variables/Secrets backend (AWS Secrets Manager, HashiCorp Vault) thay vì plain JSON.

## 8) Testing & Validation

### 8.1 Smoke Test (Post-deployment)
```bash
# 1. Verify all services are healthy
docker compose ps
# Expected: all services "Up" or "healthy"

# 2. Check DAG is discovered
docker compose exec airflow-webserver airflow dags list | grep sftp_sync
# Expected: sftp_sync | data-platform | True

# 3. Verify no import errors
docker compose exec airflow-scheduler airflow dags list-import-errors
# Expected: No data found

# 4. Check DAG details
docker compose exec airflow-webserver airflow dags details sftp_sync
# Expected: has_import_errors=False, is_paused=True

# 5. Unpause DAG
docker compose exec airflow-webserver airflow dags unpause sftp_sync

# 6. Trigger manual run
docker compose exec airflow-webserver airflow dags trigger sftp_sync
# Expected: Created <DagRun sftp_sync @ manual__...>
```

### 8.2 Functional Test (End-to-end validation)
```bash
# 1. Seed test files in SFTP source
docker exec sftp-source sh -c 'echo "test content 1" > /home/source/a/test1.txt'
docker exec sftp-source sh -c 'echo "test content 2" > /home/source/a/subdir/test2.txt'
docker exec sftp-source sh -c 'mkdir -p /home/source/a/nested/deep && echo "nested" > /home/source/a/nested/deep/test3.txt'

# 2. Trigger DAG and wait for completion (or check UI)
docker compose exec airflow-webserver airflow dags trigger sftp_sync

# 3. Wait ~2-3 minutes, then check target
docker exec sftp-target ls -lR /home/target/a/
# Expected: test1.txt, subdir/test2.txt, nested/deep/test3.txt present

# 4. Verify content
docker exec sftp-target cat /home/target/a/test1.txt
# Expected: "test content 1"

# 5. Re-trigger DAG (idempotency test)
docker compose exec airflow-webserver airflow dags trigger sftp_sync
# Expected: checker detects 0 pending files (all already synced)

# 6. Modify source file and re-trigger
docker exec sftp-source sh -c 'echo "updated content" > /home/source/a/test1.txt'
docker compose exec airflow-webserver airflow dags trigger sftp_sync
# Expected: only test1.txt re-synced (incremental)
```

### 8.3 Load Test (Scalability validation)
```bash
# 1. Generate 1000 test files
docker exec sftp-source sh -c '
  for i in $(seq 1 1000); do
    echo "content $i" > /home/source/a/load_test_$i.txt
  done
'

# 2. Trigger sync and monitor performance
docker compose exec airflow-webserver airflow dags trigger sftp_sync --conf '{"batch_size": 1000}'

# 3. Monitor in UI or via CLI
docker compose exec airflow-webserver airflow dags list-runs sftp_sync --no-backfill -o json

# Expected metrics to observe:
# - Total duration: < 10 minutes for 1000 files (depends on network)
# - Parallelism: ~32 concurrent upload tasks (check Gantt chart)
# - Success rate: 100% (or identify failures)
```

### 8.4 Failure Scenario Tests

#### Test 1: Large File Handling
```bash
# Create oversized file (> 256 MB)
docker exec sftp-source sh -c 'dd if=/dev/zero of=/home/source/a/large.bin bs=1M count=300'

# Trigger sync
docker compose exec airflow-webserver airflow dags trigger sftp_sync

# Check logs for "oversized_file_count": 1
docker compose logs airflow-scheduler | grep oversized

# Expected: large.bin skipped, other files processed
```

#### Test 2: Target Connection Failure
```bash
# Stop target SFTP
docker compose stop sftp-target

# Trigger sync
docker compose exec airflow-webserver airflow dags trigger sftp_sync

# Expected: upload tasks fail after 4 retries
# Check UI for retry attempts (should see exponential backoff)

# Restore target and re-trigger
docker compose start sftp-target
docker compose exec airflow-webserver airflow dags trigger sftp_sync
# Expected: successful sync
```

#### Test 3: Partial Upload Recovery
```bash
# Seed 10 files
docker exec sftp-source sh -c 'for i in $(seq 1 10); do echo "test $i" > /home/source/a/partial_$i.txt; done'

# Stop target mid-sync (after DAG starts)
docker compose exec airflow-webserver airflow dags trigger sftp_sync
sleep 30  # Wait for some uploads to complete
docker compose stop sftp-target

# Check recheck task fails with summary
docker compose logs airflow-scheduler | grep recheck
# Expected: "failed": N (số files chưa upload)

# Restore and re-trigger
docker compose start sftp-target
docker compose exec airflow-webserver airflow dags trigger sftp_sync
# Expected: chỉ sync files chưa complete (incremental)
```

### 8.5 Integration Test (Adapter extensibility)
**Hypothetical S3 test** (not implemented, but shows extensibility):
```python
# In tests/test_s3_adapter.py
def test_s3_source_adapter():
    adapter = S3SourceAdapter(conn_id="test_s3")
    files = adapter.list_files(root_path="s3://my-bucket/prefix/")
    assert len(files) > 0
    assert all(file.path.startswith("s3://") for file in files)

def test_hybrid_sync():
    # S3 → SFTP sync
    trigger_dag_with_conf({
        "source_conn_type": "s3",
        "source_conn_id": "s3_source",
        "target_conn_type": "sftp",
        "target_conn_id": "sftp_target"
    })
    # Expected: adapters auto-selected, sync works
```

### 8.6 Regression Test (After changes)
```bash
# Run full test suite
./run_tests.sh  # (would need to create this script)

# Steps:
# 1. docker compose up -d
# 2. Smoke test
# 3. Functional test with known dataset
# 4. Compare checksums: source vs target
# 5. Clean up: docker compose down -v
```

### 8.7 Performance Benchmarks
Target SLAs (to validate against):
| Metric | Target | Measured (example) |
|--------|--------|-------------------|
| Files/hour | > 10,000 | ~12,500 |
| P95 task duration | < 120s | ~85s |
| Success rate | > 99.5% | 99.8% |
| Mean time to detect failure | < 5 min | ~3 min |
| Recovery time (after failure) | < 10 min | ~8 min |

## 9) Development Workflow & Git History

### 9.1 Commit History Philosophy
Commits nên theo **incremental development story**, không squash để reviewer thấy được thought process:

**Good example** (implemented in this repo):
```
1. chore: bootstrap docker compose with airflow 3.1.7 + sftp services
2. feat: add basic sftp sync dag with checker/download/upload tasks  
3. refactor: extract io_adapters.py with SourceAdapter/TargetAdapter interface
4. feat: add transformation pipeline (noop, gzip)
5. feat: add large file guard and oversized file handling
6. refactor: separate core/ and sftp_sync/ plugin modules
7. feat: add metadata builder for data catalog
8. feat: add data lineage tracking with assets (inlets/outlets)
9. fix: add plugin __init__.py for proper module import
10. fix: add DAG import marker for airflow discovery heuristic
11. docs: add architecture patterns and scalability analysis
12. chore: migrate from sqlite to postgresql for stability
13. docs: comprehensive README with staff-level analysis
```

**Bad example** (anti-pattern):
```
1. Initial commit (everything in one commit)
2. Fix typo
3. Update README
```

### 9.2 Local Development Setup
```bash
# 1. Clone repo
git clone <repo-url>
cd Take-home-Test

# 2. Start services
./run.sh

# 3. Wait for init (~30s)
docker compose logs -f airflow-init

# 4. Access UI
open http://localhost:8080
# Login: admin / admin

# 5. Develop DAG locally
# Edit airflow/dags/sftp_sync.py or airflow/plugins/...
# Airflow auto-reloads DAGs every 30s (dag_dir_list_interval)

# 6. Test changes
docker compose exec airflow-webserver airflow dags trigger sftp_sync

# 7. View logs
docker compose logs -f airflow-scheduler
docker compose logs -f airflow-worker

# 8. Debug task
docker compose exec airflow-scheduler airflow tasks test sftp_sync checker 2026-03-06

# 9. Clean up staging
docker compose exec airflow-worker rm -rf /opt/airflow/data/sftp_sync_staging/*
```

### 9.3 Code Style & Conventions
- **Python**: PEP 8, type hints (`from __future__ import annotations`)
- **Imports**: stdlib → third-party → airflow → local (sorted alphabetically)
- **Naming**: 
  - Functions: `snake_case`
  - Classes: `PascalCase`
  - Constants: `UPPER_CASE`
  - Private: `_leading_underscore`
- **Docstrings**: Not required for take-home brevity, but production should have Google-style docstrings
- **Comments**: Explain "why" not "what" (code should be self-documenting)

### 9.4 Testing Workflow
```bash
# Unit tests (if implemented)
pytest tests/unit/

# Integration tests
pytest tests/integration/

# E2E tests
./run_tests.sh

# Code quality
black airflow/  # Format
ruff airflow/   # Lint
mypy airflow/   # Type check
```

### 9.5 CI/CD Pipeline (Recommended)
```yaml
# .github/workflows/ci.yml (example)
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Start services
        run: docker compose up -d
      - name: Wait for Airflow
        run: sleep 60
      - name: Run smoke tests
        run: |
          docker compose exec airflow-webserver airflow dags list
          docker compose exec airflow-webserver airflow dags list-import-errors
      - name: Trigger test run
        run: docker compose exec airflow-webserver airflow dags trigger sftp_sync
      - name: Check run status
        run: |
          sleep 120
          docker compose exec airflow-webserver airflow dags list-runs sftp_sync
```
