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

## Ghi chú
- Phiên bản image: `apache/airflow:3.1.7`
- Database: SQLite tại `./data/airflow.db`
- Executor: `CeleryExecutor`
- Celery broker: Redis (`redis://redis:6379/0`)
