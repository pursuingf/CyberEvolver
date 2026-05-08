#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"
source "${REPO_ROOT}/common/scripts/lib/challenge_run_helpers.sh"

PYTHON_BIN="${PYTHON_BIN:-/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python}"
EVOLVE_SCRIPT="${EVOLVE_SCRIPT:-run_evolve_batch_skill.py}"
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
BASE_SEED_PATH="${BASE_SEED_PATH:-./cyber_evolver/gen0_root/skill_based}"
EVOLVE_MAX_WORKERS="${EVOLVE_MAX_WORKERS:-4}"
TASK_WORKERS="${TASK_WORKERS:-6}"
LLM_MAX_INFLIGHT="${LLM_MAX_INFLIGHT:-24}"
LLM_MAX_INFLIGHT_PER_LANE="${LLM_MAX_INFLIGHT_PER_LANE:-6}"
LLM_REQUEST_TIMEOUT="${LLM_REQUEST_TIMEOUT:-600}"
LLM_MAX_ATTEMPTS="${LLM_MAX_ATTEMPTS:-3}"
LLM_RESPONSE_TIMEOUT="${LLM_RESPONSE_TIMEOUT:-3600}"
LLM_LARGE_REQUEST_DELAY="${LLM_LARGE_REQUEST_DELAY:-1.0}"
PROMPT_VARIANT="${PROMPT_VARIANT:-}"

DEFAULT_SEED_INCLUDES=(
  "commands/submit.py"
)

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
RUN_MODE_NAMESPACE_PART="${RUN_MODE_NAMESPACE_PART:-ours}"
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

SEED_ARGS=()
for seed_include in "${DEFAULT_SEED_INCLUDES[@]}"; do
  SEED_ARGS+=(--seed-include "${seed_include}")
done

if [[ -n "${EXTRA_SEED_INCLUDE_CSV:-}" ]]; then
  IFS=',' read -r -a extra_seed_includes <<< "${EXTRA_SEED_INCLUDE_CSV}"
  for seed_include in "${extra_seed_includes[@]}"; do
    seed_include="${seed_include//[[:space:]]/}"
    if [[ -n "${seed_include}" ]]; then
      SEED_ARGS+=(--seed-include "${seed_include}")
    fi
  done
fi

start_challenge_server

stage_index=1
for config_mode in evo raw; do
  EVOLVE_ARGS=(
    --benchmark "${BENCHMARK}"
    --config-mode "${config_mode}"
    --model "${MODEL}"
    --run-id "${RUN_ID_PREFIX}_${config_mode}_fixed"
    --challenge-server-url "${CHALLENGE_SERVER_URL}"
    --ids "${CHALLENGE_CSV}"
    --base_seed_path "${BASE_SEED_PATH}"
    --max-workers "${EVOLVE_MAX_WORKERS}"
    --task_workers "${TASK_WORKERS}"
    --llm-max-inflight "${LLM_MAX_INFLIGHT}"
    --llm-max-inflight-per-lane "${LLM_MAX_INFLIGHT_PER_LANE}"
    --llm-request-timeout "${LLM_REQUEST_TIMEOUT}"
    --llm-max-attempts "${LLM_MAX_ATTEMPTS}"
    --llm-response-timeout "${LLM_RESPONSE_TIMEOUT}"
    --llm-large-request-delay "${LLM_LARGE_REQUEST_DELAY}"
  )
  EVOLVE_ARGS+=("${SEED_ARGS[@]}")
  if [[ -n "${PROMPT_VARIANT}" ]]; then
    EVOLVE_ARGS+=(--prompt-variant "${PROMPT_VARIANT}")
  fi

  banner "Stage ${stage_index}/2: ${config_mode} fixed NYU challenges"
  run_cmd "${PYTHON_BIN}" "${EVOLVE_SCRIPT}" "${EVOLVE_ARGS[@]}"
  stage_index=$((stage_index + 1))
done

banner "Completed"
printf 'Challenges: %s\n' "${CHALLENGE_CSV}"
printf 'Model: %s\n' "${MODEL}"
printf 'Namespace: %s\n' "${CTF_NAMESPACE}"
printf 'Challenge server: %s\n' "${CHALLENGE_SERVER_URL}"
