#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"
source "${REPO_ROOT}/common/scripts/lib/challenge_run_helpers.sh"

PYTHON_BIN="${PYTHON_BIN:-/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python}"
BASELINE_SCRIPT="${BASELINE_SCRIPT:-baseline/batch/run_batch_baseline.py}"
CHALLENGE_SERVER_SCRIPT="${CHALLENGE_SERVER_SCRIPT:-bench_hub/server/challenge_server.py}"

NYU_FIXED_IDS=(
  2020f-web-picgram
  2021q-web-no_pass_needed
  2023q-web-cookie_injection
  2019q-pwn-traveller
  2018f-pwn-es1337
  2023f-cry-describeme
)

BENCHMARK="${BENCHMARK:-nyu_ctf}"
MODEL="${MODEL:-DeepSeek-V3.1-sii}"
RUN_ID_PREFIX="${RUN_ID_PREFIX:-nyuctfbench}"
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

BASELINE_MAX_WORKERS="${BASELINE_MAX_WORKERS:-8}"
BASELINE_STEP_LIMIT="${BASELINE_STEP_LIMIT:-30}"
BASELINE_SAMPLES="${BASELINE_SAMPLES:-1}"

CHALLENGE_SERVER_PID=""
CHALLENGE_SERVER_STARTED_BY_SCRIPT=0

join_by_comma() {
  local items=("$@")
  local joined=""
  local item
  for item in "${items[@]}"; do
    if [[ -n "${joined}" ]]; then
      joined+=","
    fi
    joined+="${item}"
  done
  printf '%s' "${joined}"
}

BENCHMARK_NAMESPACE_PART="$(normalize_namespace_part "${BENCHMARK}")"
MODEL_NAMESPACE_PART="$(normalize_namespace_part "${MODEL}")"
RUN_MODE_NAMESPACE_PART="${RUN_MODE_NAMESPACE_PART:-baseline}"
DEFAULT_CTF_NAMESPACE="${BENCHMARK_NAMESPACE_PART}_${MODEL_NAMESPACE_PART}_${RUN_MODE_NAMESPACE_PART}"
CTF_NAMESPACE="${CTF_NAMESPACE:-${DEFAULT_CTF_NAMESPACE}}"

readarray -t _challenge_server_url_parts < <(parse_url_host_port "${CHALLENGE_SERVER_URL}")
CHALLENGE_SERVER_PUBLIC_HOST="${_challenge_server_url_parts[0]}"
CHALLENGE_SERVER_PORT="${_challenge_server_url_parts[1]}"
if [[ -z "${CHALLENGE_SERVER_LOG_PATH_USER_SET}" ]]; then
  CHALLENGE_SERVER_LOG_PATH="${CHALLENGE_SERVER_LOG_DIR}/${CTF_NAMESPACE}_${CHALLENGE_SERVER_PORT}.log"
else
  CHALLENGE_SERVER_LOG_PATH="${CHALLENGE_SERVER_LOG_PATH}"
fi

CHALLENGE_CSV="$(join_by_comma "${NYU_FIXED_IDS[@]}")"

start_challenge_server

BASELINE_COMMON_ARGS=(
  --model "${MODEL}"
  --benchmark "${BENCHMARK}"
  --max-workers "${BASELINE_MAX_WORKERS}"
  --samples "${BASELINE_SAMPLES}"
  --step-limit "${BASELINE_STEP_LIMIT}"
  --challenge-server-url "${CHALLENGE_SERVER_URL}"
)

# banner "Stage 1/2: nyuctf_single baseline"
# run_cmd "${PYTHON_BIN}" "${BASELINE_SCRIPT}" \
#   --agent nyuctf_single \
#   --run-id "${RUN_ID_PREFIX}_baseline_nyuctf_single" \
#   "${BASELINE_COMMON_ARGS[@]}"

banner "Stage 2/2: dcipher baseline"
run_cmd "${PYTHON_BIN}" "${BASELINE_SCRIPT}" \
  --agent dcipher \
  --run-id "${RUN_ID_PREFIX}_baseline_dcipher" \
  "${BASELINE_COMMON_ARGS[@]}"

banner "Completed"
printf 'Challenges: %s\n' "${CHALLENGE_CSV}"
printf 'Model: %s\n' "${MODEL}"
printf 'Namespace: %s\n' "${CTF_NAMESPACE}"
printf 'Challenge server: %s\n' "${CHALLENGE_SERVER_URL}"
