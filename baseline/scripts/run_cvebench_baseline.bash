#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"
source "${REPO_ROOT}/common/scripts/lib/challenge_run_helpers.sh"

PYTHON_BIN="${PYTHON_BIN:-/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python}"
BASELINE_SCRIPT="${BASELINE_SCRIPT:-baseline/batch/run_batch_baseline.py}"
CHALLENGE_SERVER_SCRIPT="${CHALLENGE_SERVER_SCRIPT:-bench_hub/server/challenge_server.py}"

BENCHMARK="${BENCHMARK:-cvebench}"
MODEL="${MODEL:-DeepSeek-V3.1-sii}"
RUN_ID_PREFIX="${RUN_ID_PREFIX:-cvebench}"
BASELINE_AGENT="${BASELINE_AGENT:-cy_agent}"
DRY_RUN="${DRY_RUN:-0}"
START_CHALLENGE_SERVER="${START_CHALLENGE_SERVER:-1}"
KEEP_CHALLENGE_SERVER="${KEEP_CHALLENGE_SERVER:-0}"

CHALLENGE_SERVER_BIND_HOST="${CHALLENGE_SERVER_BIND_HOST:-0.0.0.0}"
CHALLENGE_SERVER_PUBLIC_HOST="${CHALLENGE_SERVER_PUBLIC_HOST:-127.0.0.1}"
CHALLENGE_SERVER_PORT="${CHALLENGE_SERVER_PORT:-8000}"
CHALLENGE_SERVER_READY_TIMEOUT_S="${CHALLENGE_SERVER_READY_TIMEOUT_S:-60}"
CHALLENGE_SERVER_LOG_DIR="${CHALLENGE_SERVER_LOG_DIR:-logs/target_servers}"
CHALLENGE_SERVER_LOG_PATH_USER_SET="${CHALLENGE_SERVER_LOG_PATH+x}"
DEFAULT_CHALLENGE_SERVER_URL="http://${CHALLENGE_SERVER_PUBLIC_HOST}:${CHALLENGE_SERVER_PORT}"
CHALLENGE_SERVER_URL="${CHALLENGE_SERVER_URL:-${DEFAULT_CHALLENGE_SERVER_URL}}"

BASELINE_MAX_WORKERS="${BASELINE_MAX_WORKERS:-24}"
BASELINE_STEP_LIMIT="${BASELINE_STEP_LIMIT:-30}"
BASELINE_SAMPLES="${BASELINE_SAMPLES:-1}"

CHALLENGE_SERVER_PID=""
CHALLENGE_SERVER_STARTED_BY_SCRIPT=0

readarray -t _challenge_server_url_parts < <(parse_url_host_port "${CHALLENGE_SERVER_URL}")
CHALLENGE_SERVER_PUBLIC_HOST="${_challenge_server_url_parts[0]}"
CHALLENGE_SERVER_PORT="${_challenge_server_url_parts[1]}"

RUN_ID_NAMESPACE_PART="$(normalize_namespace_part "${RUN_ID_PREFIX}")"
MODEL_NAMESPACE_PART="$(normalize_namespace_part "${MODEL}")"
RUN_MODE_NAMESPACE_PART="${RUN_MODE_NAMESPACE_PART:-baseline}"
CTF_NAMESPACE="${CTF_NAMESPACE:-${RUN_ID_NAMESPACE_PART}_${MODEL_NAMESPACE_PART}_${RUN_MODE_NAMESPACE_PART}}"

if [[ -z "${CHALLENGE_SERVER_LOG_PATH_USER_SET}" ]]; then
  CHALLENGE_SERVER_LOG_PATH="${CHALLENGE_SERVER_LOG_DIR}/${CTF_NAMESPACE}_${CHALLENGE_SERVER_PORT}.log"
else
  CHALLENGE_SERVER_LOG_PATH="${CHALLENGE_SERVER_LOG_PATH}"
fi

start_challenge_server

BASELINE_COMMON_ARGS=(
  --agent "${BASELINE_AGENT}"
  --model "${MODEL}"
  --benchmark "${BENCHMARK}"
  --max-workers "${BASELINE_MAX_WORKERS}"
  --samples "${BASELINE_SAMPLES}"
  --step-limit "${BASELINE_STEP_LIMIT}"
  --challenge-server-url "${CHALLENGE_SERVER_URL}"
)

BASELINE_T_AGENT_ARGS=(
  --agent t_agent
  --model "${MODEL}"
  --benchmark "${BENCHMARK}"
  --max-workers "${BASELINE_MAX_WORKERS}"
  --samples "${BASELINE_SAMPLES}"
  --step-limit "${BASELINE_STEP_LIMIT}"
  --challenge-server-url "${CHALLENGE_SERVER_URL}"
)

# banner "Stage 1/4: cy_agent baseline zero_day"
# run_cmd "${PYTHON_BIN}" "${BASELINE_SCRIPT}" \
#   "${BASELINE_COMMON_ARGS[@]}" \
#   --run-id "${RUN_ID_PREFIX}_baseline_zero_day" \
#   --prompt-variant zero_day

# banner "Stage 2/4: t_agent baseline zero_day"
# run_cmd "${PYTHON_BIN}" "${BASELINE_SCRIPT}" \
#   "${BASELINE_T_AGENT_ARGS[@]}" \
#   --run-id "${RUN_ID_PREFIX}_t_agent_zero_day" \
#   --prompt-variant zero_day

banner "Stage 3/4: cy_agent baseline one_day"
run_cmd "${PYTHON_BIN}" "${BASELINE_SCRIPT}" \
  "${BASELINE_COMMON_ARGS[@]}" \
  --run-id "${RUN_ID_PREFIX}_baseline_one_day" \
  --prompt-variant one_day

banner "Stage 4/4: t_agent baseline one_day"
run_cmd "${PYTHON_BIN}" "${BASELINE_SCRIPT}" \
  "${BASELINE_T_AGENT_ARGS[@]}" \
  --run-id "${RUN_ID_PREFIX}_t_agent_one_day" \
  --prompt-variant one_day


banner "Completed"
printf 'Benchmark: %s\n' "${BENCHMARK}"
printf 'Model: %s\n' "${MODEL}"
printf 'Namespace: %s\n' "${CTF_NAMESPACE}"
printf 'Challenge server: %s\n' "${CHALLENGE_SERVER_URL}"
