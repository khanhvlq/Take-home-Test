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
- Password: `admin` (đổi trong `config/simple_auth_manager_passwords.json`)

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

## Ghi chú
- Phiên bản image: `apache/airflow:3.1.7`
- Database: SQLite tại `./airflow/data/airflow.db`
- Executor: `CeleryExecutor`
- Celery broker: Redis (`redis://redis:6379/0`)
- Connections được auto import từ `./airflow/config/connection.json` mỗi lần chạy `docker compose up`
