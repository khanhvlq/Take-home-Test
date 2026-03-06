#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not in PATH"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Error: docker compose plugin is not available"
  exit 1
fi

export ENV_FILE_PATH="$SCRIPT_DIR/dev.env"

# Ensure required directories exist
mkdir -p airflow/config airflow/dags airflow/logs airflow/plugins airflow/data

# Create admin credentials file if not exists
if [ ! -f "airflow/config/simple_auth_manager_passwords.json" ]; then
  echo "Creating admin credentials file..."
  echo '{"admin": "admin"}' > airflow/config/simple_auth_manager_passwords.json
fi

echo "Starting all services from docker-compose.yml..."
docker compose -f docker-compose.yml -f docker-compose-sftp.yml up -d

echo "Services status:"
docker compose ps