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

# Ensure required directories exist (for fresh clones)
echo "Creating required directories..."
mkdir -p airflow/dags airflow/logs airflow/plugins airflow/config airflow/data

# Check if simple_auth_manager_passwords.json exists
if [ ! -f "airflow/config/simple_auth_manager_passwords.json" ]; then
  echo "Creating admin credentials file..."
  echo '{"admin": "admin"}' > airflow/config/simple_auth_manager_passwords.json
fi

echo "Starting all services from docker-compose.yml..."
docker compose -f docker-compose.yml -f docker-compose-sftp.yml up -d

echo ""
echo "Waiting for Airflow initialization..."
echo "This may take 30-60 seconds on first run..."

# Wait for airflow-init to complete
timeout=120
elapsed=0
while [ $elapsed -lt $timeout ]; do
  if docker compose ps airflow-init | grep -q "exited"; then
    echo "✅ Airflow database initialized successfully"
    break
  fi
  sleep 2
  elapsed=$((elapsed + 2))
  echo -n "."
done
echo ""

# Wait for webserver to be ready
echo "Waiting for Airflow webserver to start..."
timeout=60
elapsed=0
while [ $elapsed -lt $timeout ]; do
  if curl -sf http://localhost:8080/health >/dev/null 2>&1; then
    echo "✅ Airflow webserver is ready"
    break
  fi
  sleep 2
  elapsed=$((elapsed + 2))
  echo -n "."
done
echo ""

echo "Services status:"
docker compose ps

echo ""
echo "=========================================="
echo "🚀 Data Sync Platform is ready!"
echo "=========================================="
echo ""
echo "Airflow UI: http://localhost:8080"
echo "Username: admin"
echo "Password: admin"
echo ""
echo "To trigger a sync:"
echo "  docker compose exec airflow-webserver airflow dags unpause sftp_sync"
echo "  docker compose exec airflow-webserver airflow dags trigger sftp_sync"
echo ""
echo "To stop all services:"
echo "  docker compose down"
echo "=========================================="