#!/bin/sh
set -e

echo "Waiting for MySQL at ${DB_HOST:-db}:${DB_PORT:-3306} ..."

python <<'PY'
import os
import sys
import time

import pymysql

host = os.environ.get("DB_HOST", "db")
port = int(os.environ.get("DB_PORT", "3306"))
user = os.environ.get("DB_USER") or os.environ.get("MYSQL_USER", "root")
password = os.environ.get("DB_PASSWORD") or os.environ.get("MYSQL_PASSWORD", "")
database = os.environ.get("DB_NAME") or os.environ.get("MYSQL_DATABASE", "ai_listing")

deadline = time.time() + int(os.environ.get("DB_WAIT_TIMEOUT", "60"))
last_err = None
while time.time() < deadline:
    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            connect_timeout=5,
        )
        conn.close()
        print("MySQL is ready.")
        sys.exit(0)
    except Exception as exc:
        last_err = exc
        time.sleep(2)

print(f"MySQL not ready after timeout: {last_err}", file=sys.stderr)
sys.exit(1)
PY

echo "Running migrations ..."
python manage.py migrate --noinput

echo "Collecting static files ..."
python manage.py collectstatic --noinput

echo "Starting application ..."
exec "$@"
