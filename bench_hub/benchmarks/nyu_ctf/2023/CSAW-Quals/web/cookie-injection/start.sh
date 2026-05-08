#!/bin/bash
set -euo pipefail

docker-entrypoint.sh mysqld &
mysql_pid=$!

cleanup() {
    if [[ -n "${gunicorn_pid:-}" ]]; then
        kill "${gunicorn_pid}" 2>/dev/null || true
        wait "${gunicorn_pid}" 2>/dev/null || true
    fi
    kill "${mysql_pid}" 2>/dev/null || true
    wait "${mysql_pid}" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

until mysqladmin ping -h 127.0.0.1 -uroot -p"${MYSQL_ROOT_PASSWORD}" --silent; do
    sleep 1
done

until mysql -h 127.0.0.1 -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "USE ${MYSQL_DATABASE}; SELECT 1;" >/dev/null 2>&1; do
    sleep 1
done

cd /app
gunicorn -b 0.0.0.0:5800 -w 8 init:app >/tmp/cookie-injection-gunicorn.log 2>&1 &
gunicorn_pid=$!

python3 - <<'PY'
import sys
import time
import urllib.error
import urllib.request

deadline = time.time() + 120
url = "http://127.0.0.1:5800/"

while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            if 200 <= resp.status < 500:
                sys.exit(0)
    except urllib.error.HTTPError as exc:
        if 200 <= exc.code < 500:
            sys.exit(0)
    except Exception:
        pass
    time.sleep(1)

sys.exit(1)
PY

wait "${mysql_pid}"
