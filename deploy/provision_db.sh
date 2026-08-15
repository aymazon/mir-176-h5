#!/usr/bin/env bash
# Idempotent provisioning of the mir-176-h5 business PostgreSQL database.
#
# Usage: bash deploy/provision_db.sh [pg-container-name]   (default: postgres)
#
# Creates (skipping anything that already exists):
#   - database "mir-176-h5"
#   - role mir176 with LOGIN
# then applies D13 grants (PG 15+: schema public + default privileges)
# and applies backend/schema.sql as role mir176, so the role owns all tables.

set -euo pipefail

PG_CONTAINER="${1:-postgres}"
DB_NAME="mir-176-h5"
ROLE_NAME="mir176"
ROLE_PASSWORD="mir176"
ADMIN_USER="postgres"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA_FILE="${SCRIPT_DIR}/../backend/schema.sql"

if [ ! -f "${SCHEMA_FILE}" ]; then
  echo "error: schema file not found: ${SCHEMA_FILE}" >&2
  exit 1
fi

pg_admin() {
  docker exec -i "${PG_CONTAINER}" psql -U "${ADMIN_USER}" -v ON_ERROR_STOP=1 -qtA -c "$1"
}

db_exists() {
  [ "$(pg_admin "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'")" = "1" ]
}

role_exists() {
  [ "$(pg_admin "SELECT 1 FROM pg_roles WHERE rolname = '${ROLE_NAME}'")" = "1" ]
}

# 1) business database (CREATE DATABASE has no IF NOT EXISTS -> check first)
if db_exists; then
  echo "skip: database \"${DB_NAME}\" already exists"
else
  echo "create: database \"${DB_NAME}\""
  pg_admin "CREATE DATABASE \"${DB_NAME}\""
fi

# 2) business role
if role_exists; then
  echo "skip: role ${ROLE_NAME} already exists"
else
  echo "create: role ${ROLE_NAME}"
  pg_admin "CREATE ROLE ${ROLE_NAME} LOGIN PASSWORD '${ROLE_PASSWORD}'"
fi

# 3) database-level privilege
pg_admin "GRANT ALL PRIVILEGES ON DATABASE \"${DB_NAME}\" TO ${ROLE_NAME}"

# 4) D13: schema-level grants (PG 15+), run connected to the business database
docker exec -i "${PG_CONTAINER}" psql -U "${ADMIN_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 -qtA \
  -c "GRANT ALL ON SCHEMA public TO ${ROLE_NAME}" \
  -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ${ROLE_NAME}" \
  -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ${ROLE_NAME}"

# 5) apply schema as the business role so it owns every table it creates
docker exec -i "${PG_CONTAINER}" psql -U "${ROLE_NAME}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 -q -f - < "${SCHEMA_FILE}"

echo "done: schema applied to \"${DB_NAME}\" as ${ROLE_NAME}"
