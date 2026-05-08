#!/usr/bin/env bash

normalize_namespace_part() {
  local value="$1"
  "${PYTHON_BIN}" - "$value" <<'PY'
import re
import sys

value = sys.argv[1].strip().lower()
value = re.sub(r"[^a-z0-9]+", "_", value)
value = re.sub(r"_+", "_", value).strip("_")
print(value or "default")
PY
}

banner() {
  local title="$1"
  printf '\n========== %s ==========\n' "${title}"
}

run_cmd() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [[ "${DRY_RUN}" == "1" ]]; then
    return 0
  fi
  local status=0
  trap ':' INT
  set +e
  "$@"
  status=$?
  set -e
  trap - INT
  if [[ "${status}" == "130" ]]; then
    printf 'Command interrupted with Ctrl-C; continuing to next stage.\n'
    return 0
  fi
  return "${status}"
}

parse_url_host_port() {
  local url="$1"
  "${PYTHON_BIN}" - "$url" <<'PY'
import sys
from urllib.parse import urlparse

url = sys.argv[1]
parsed = urlparse(url)
if not parsed.scheme or not parsed.hostname or parsed.port is None:
    raise SystemExit(f"Invalid URL: {url}")
print(parsed.hostname)
print(parsed.port)
PY
}

challenge_server_ready() {
  local url="$1"
  "${PYTHON_BIN}" - "$url" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request

base = sys.argv[1].rstrip("/")
with urllib.request.urlopen(f"{base}/openapi.json", timeout=5) as resp:
    payload = json.load(resp)
if payload.get("info", {}).get("title") != "CTF Manager Server":
    raise SystemExit(1)
PY
}

port_is_in_use() {
  local host="$1"
  local port="$2"
  "${PYTHON_BIN}" - "$host" "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(0.5)
try:
    sock.connect((host, port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

find_available_port() {
  local host="$1"
  local start_port="$2"
  "${PYTHON_BIN}" - "$host" "$start_port" <<'PY'
import socket
import sys

host = sys.argv[1]
start = int(sys.argv[2])

for port in range(start, start + 200):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            continue
        print(port)
        raise SystemExit(0)

raise SystemExit("No available port found")
PY
}

wait_for_challenge_server() {
  local url="$1"
  local timeout_s="$2"
  local deadline=$((SECONDS + timeout_s))
  while (( SECONDS < deadline )); do
    if challenge_server_ready "${url}"; then
      return 0
    fi
    if [[ -n "${CHALLENGE_SERVER_PID}" ]] && ! kill -0 "${CHALLENGE_SERVER_PID}" 2>/dev/null; then
      return 1
    fi
    sleep 1
  done
  challenge_server_ready "${url}"
}

cleanup_challenge_server() {
  if [[ "${CHALLENGE_SERVER_STARTED_BY_SCRIPT}" != "1" ]]; then
    return 0
  fi
  if [[ "${KEEP_CHALLENGE_SERVER}" == "1" ]]; then
    return 0
  fi
  if [[ -n "${CHALLENGE_SERVER_PID}" ]] && kill -0 "${CHALLENGE_SERVER_PID}" 2>/dev/null; then
    printf 'Stopping challenge_server pid=%s\n' "${CHALLENGE_SERVER_PID}"
    kill "${CHALLENGE_SERVER_PID}" 2>/dev/null || true
    wait "${CHALLENGE_SERVER_PID}" 2>/dev/null || true
  fi
}

start_challenge_server() {
  if [[ "${START_CHALLENGE_SERVER}" != "1" ]]; then
    return 0
  fi

  mkdir -p "${CHALLENGE_SERVER_LOG_DIR}"

  if port_is_in_use "${CHALLENGE_SERVER_PUBLIC_HOST}" "${CHALLENGE_SERVER_PORT}"; then
    local next_port
    next_port="$(find_available_port "${CHALLENGE_SERVER_BIND_HOST}" "$((CHALLENGE_SERVER_PORT + 1))")"
    printf 'Port %s is occupied, switching challenge_server to %s\n' "${CHALLENGE_SERVER_PORT}" "${next_port}"
    CHALLENGE_SERVER_PORT="${next_port}"
    CHALLENGE_SERVER_URL="http://${CHALLENGE_SERVER_PUBLIC_HOST}:${CHALLENGE_SERVER_PORT}"
    if [[ -z "${CHALLENGE_SERVER_LOG_PATH_USER_SET}" ]]; then
      CHALLENGE_SERVER_LOG_PATH="${CHALLENGE_SERVER_LOG_DIR}/${CTF_NAMESPACE}_${CHALLENGE_SERVER_PORT}.log"
    fi
  fi

  banner "Starting challenge_server"
  printf 'Namespace: %s\n' "${CTF_NAMESPACE}"
  printf 'Bind: %s:%s\n' "${CHALLENGE_SERVER_BIND_HOST}" "${CHALLENGE_SERVER_PORT}"
  printf 'Server URL: %s\n' "${CHALLENGE_SERVER_URL}"
  printf 'Log: %s\n' "${CHALLENGE_SERVER_LOG_PATH}"

  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '+'
    printf ' %q' env \
      CTF_NAMESPACE="${CTF_NAMESPACE}" \
      CTF_STARTUP_TIMEOUT_S="${CTF_STARTUP_TIMEOUT_S:-180}" \
      CTF_PORT_OPEN_STABILITY_CHECKS="${CTF_PORT_OPEN_STABILITY_CHECKS:-2}" \
      "${PYTHON_BIN}" "${CHALLENGE_SERVER_SCRIPT}" "${CHALLENGE_SERVER_BIND_HOST}" "${CHALLENGE_SERVER_PORT}"
    printf ' > %q 2>&1 &\n' "${CHALLENGE_SERVER_LOG_PATH}"
    return 0
  fi

  env \
    CTF_NAMESPACE="${CTF_NAMESPACE}" \
    CTF_STARTUP_TIMEOUT_S="${CTF_STARTUP_TIMEOUT_S:-180}" \
    CTF_PORT_OPEN_STABILITY_CHECKS="${CTF_PORT_OPEN_STABILITY_CHECKS:-2}" \
    "${PYTHON_BIN}" "${CHALLENGE_SERVER_SCRIPT}" "${CHALLENGE_SERVER_BIND_HOST}" "${CHALLENGE_SERVER_PORT}" \
    > "${CHALLENGE_SERVER_LOG_PATH}" 2>&1 &
  CHALLENGE_SERVER_PID=$!
  CHALLENGE_SERVER_STARTED_BY_SCRIPT=1
  trap cleanup_challenge_server EXIT

  if ! wait_for_challenge_server "${CHALLENGE_SERVER_URL}" "${CHALLENGE_SERVER_READY_TIMEOUT_S}"; then
    printf 'challenge_server failed to become ready at %s\n' "${CHALLENGE_SERVER_URL}" >&2
    if [[ -f "${CHALLENGE_SERVER_LOG_PATH}" ]]; then
      printf 'Last log lines from %s:\n' "${CHALLENGE_SERVER_LOG_PATH}" >&2
      tail -n 40 "${CHALLENGE_SERVER_LOG_PATH}" >&2 || true
    fi
    exit 1
  fi

  printf 'challenge_server is ready at %s\n' "${CHALLENGE_SERVER_URL}"
}
