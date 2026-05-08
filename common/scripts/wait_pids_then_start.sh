#!/usr/bin/env bash
set -euo pipefail

# Usage: ./wait_pids_then_start.sh <pid> <command> [args...]
# Example: ./wait_pids_then_start.sh 1234 /path/to/xx --arg1 foo

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <pid> <command> [args...]"
  exit 1
fi

PID="$1"
shift

if ! [[ "$PID" =~ ^[0-9]+$ ]]; then
  echo "Error: pid must be a number."
  exit 1
fi

CMD=( "$@" )

is_alive() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null
}

echo "Waiting for PID $PID to exit..."
while true; do
  if ! is_alive "$PID"; then
    echo "Process exited. Starting: ${CMD[*]}"
    exec "${CMD[@]}"
  fi

  sleep 1
done
