# Airflow 3.1.7 + Docker Compose + SQLite

## Yêu cầu
- Docker Desktop (hoặc Docker Engine + Compose plugin)

## Chạy nhanh
```bash
docker compose up -d
```

Airflow UI: http://localhost:8080  
Tài khoản mặc định:
- Username: `admin`
- Password: `admin` (đổi trong `airflow/config/simple_auth_manager_passwords.json`)

## Xem log
```bash
docker compose logs -f airflow-apiserver airflow-scheduler airflow-worker
```

## Dừng và xoá container
```bash
docker compose down
```

## Chạy SFTP services riêng
Khởi động stack SFTP (`sftp-source`, `sftp-target`) bằng file compose riêng:

```bash
docker compose -f docker-compose-sftp.yml up -d
```

Thông tin truy cập mặc định:
- `sftp-source`: host `localhost`, port `2222`, user `source`, password `source123`
- `sftp-target`: host `localhost`, port `2223`, user `target`, password `target123`

Cấu trúc dữ liệu seed sẵn:
- `sftp://sftp-source/a/b/c/file_1.txt`
- `sftp://sftp-source/a/b/c/file_2.txt`
- `sftp://sftp-source/a/b/c/file_3.txt`

Mapping local folders:
- Source data: `./sftp-source/data`
- Source config: `./sftp-source/config`
- Target data: `./sftp-target/data`
- Target config: `./sftp-target/config`

## DAG đồng bộ SFTP
DAG hiện tại: `sftp_sync` tại `airflow/dags/sftp_sync_dag.py`.

## Thư viện connection dùng chung (plugin)
Thư viện mới tại `airflow/plugins/connection_lib` (ngang cấp với `sftp_sync_lib`) cho phép import/inherit để tái sử dụng nhiều backend.

Các backend mặc định đã có:
- `sftp`
- `s3`
- `postgres`

Ví dụ dùng nhanh (chỉ truyền Airflow connection id):

```python
from sftp_sync_lib.connection_lib import get_sftp_hook, get_s3_hook, get_postgres_hook

sftp_hook = get_sftp_hook("sftp_source")
s3_hook = get_s3_hook("aws_default")
pg_hook = get_postgres_hook("postgres_default")
```

Generic factory:

```python
from sftp_sync_lib.connection_lib import get_hook

hook = get_hook(conn_id="sftp_source", conn_type="sftp")
client = hook.get_conn()
```

Đăng ký backend mới (inherit):

```python
from sftp_sync_lib.connection_lib import ConnectionProvider, register_connection_provider

class MyProvider(ConnectionProvider):
	conn_type = "my_backend"

	def get_hook(self, conn_id: str, **hook_kwargs):
		...

register_connection_provider(MyProvider())
```

Đặc tính orchestration:
- Đồng bộ một chiều: source -> target
- Giữ nguyên cấu trúc thư mục
- Append-only (không propagate delete)
- Incremental + idempotent (so sánh `size`/`mtime`)
- Dynamic task mapping theo batch để scale

Biến cấu hình chính (trong `.env`):
- `AIRFLOW__CORE__EXECUTION_API_SERVER_URL=http://airflow-apiserver:8080/execution/`
- `AIRFLOW__API__BASE_URL=http://airflow-apiserver:8080`
- `SFTP_SYNC_SOURCE_CONN_ID=sftp_source`
- `SFTP_SYNC_TARGET_CONN_ID=sftp_target`
- `SFTP_SYNC_SOURCE_BASE_PATH=/a`
- `SFTP_SYNC_TARGET_BASE_PATH=/a`
- `SFTP_SYNC_BATCH_SIZE=500`
- `SFTP_SYNC_SCHEDULE=@daily`

Trigger thủ công với override runtime config:
```bash
docker compose exec airflow-apiserver airflow dags trigger sftp_sync \
	--conf '{"source_conn_id":"sftp_source","target_conn_id":"sftp_target","source_base_path":"/a","target_base_path":"/a","batch_size":200}'
```

## Ghi chú
- Phiên bản image: `apache/airflow:3.1.7`
- Database: SQLite tại `./airflow/data/airflow.db`
- Executor: `CeleryExecutor`
- Celery broker: Redis (`redis://redis:6379/0`)
- Connections được auto import từ `./airflow/config/connection.json` mỗi lần chạy `docker compose up`
