#!/usr/bin/env bash
#
# HGM Cyber Agent: evolve + final eval pipeline
#
# Stage 0: Start Challenge server (target runtime manager)
# Stage 1: Evolution — Thompson Sampling tree search to evolve cyber agent
# Stage 2: Final eval — Run best agent(s) on full benchmark with pass@n
#
# Usage:
#   # Full pipeline
#   bash baseline/scripts/run_hgm_cyber.bash
#
#   # Custom model and budget
#   MODEL=Kimi-K2.5-sii MAX_TASK_EVALS=200 PASS_N=3 bash baseline/scripts/run_hgm_cyber.bash
#
#   # Skip evolution, only eval existing run
#   SKIP_EVOLVE=1 EVOLVE_OUTPUT_DIR=/path/to/run bash baseline/scripts/run_hgm_cyber.bash
#
#   # Dry run
#   DRY_RUN=1 bash baseline/scripts/run_hgm_cyber.bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HGM_DIR="${REPO_ROOT}/baseline/upstreams/HGM_cyber"

source "${REPO_ROOT}/common/scripts/lib/challenge_run_helpers.sh"

PYTHON_BIN="${PYTHON_BIN:-/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python}"
CHALLENGE_SERVER_SCRIPT="${CHALLENGE_SERVER_SCRIPT:-${REPO_ROOT}/bench_hub/server/challenge_server.py}"

# ── Model ──
MODEL="${MODEL:-Kimi-K2.5-sii}"
BENCHMARK="${BENCHMARK:-cvebench}"
CATEGORIES="${CATEGORIES:-}"  # Comma-separated category filter (e.g. "pwn,web")

# ── Evolution parameters ──
MAX_TASK_EVALS="${MAX_TASK_EVALS:-200}"
MAX_WORKERS="${MAX_WORKERS:-8}"
STEP_LIMIT="${STEP_LIMIT:-30}"
ALPHA="${ALPHA:-0.6}"
EVAL_TIMEOUT="${EVAL_TIMEOUT:-900}"
SELF_IMPROVE_TIMEOUT="${SELF_IMPROVE_TIMEOUT:-1800}"
MAX_INPUT_TOKENS="${MAX_INPUT_TOKENS:-}"
MAX_OUTPUT_TOKENS="${MAX_OUTPUT_TOKENS:-}"
MAX_TOTAL_TOKENS="${MAX_TOTAL_TOKENS:-}"
CONTINUE_FROM="${CONTINUE_FROM:-}"     # Resume from a previous run's output dir
SKIP_EVOLVE="${SKIP_EVOLVE:-0}"
EVOLVE_OUTPUT_DIR="${EVOLVE_OUTPUT_DIR:-}"

# ── Final eval parameters ──
PASS_N="${PASS_N:-1}"
EVAL_WORKERS="${EVAL_WORKERS:-${MAX_WORKERS}}"
EVAL_STEP_LIMIT="${EVAL_STEP_LIMIT:-${STEP_LIMIT}}"
EVAL_TIMEOUT_S="${EVAL_TIMEOUT_S:-${EVAL_TIMEOUT}}"
TOP_K="${TOP_K:-1}"
SKIP_EVAL="${SKIP_EVAL:-0}"

# ── Challenge server ──
CHALLENGE_SERVER_BIND_HOST="${CHALLENGE_SERVER_BIND_HOST:-0.0.0.0}"
CHALLENGE_SERVER_PUBLIC_HOST="${CHALLENGE_SERVER_PUBLIC_HOST:-10.1.2.146}"
CHALLENGE_SERVER_PORT="${CHALLENGE_SERVER_PORT:-8000}"
CHALLENGE_SERVER_READY_TIMEOUT_S="${CHALLENGE_SERVER_READY_TIMEOUT_S:-60}"
CHALLENGE_SERVER_LOG_DIR="${CHALLENGE_SERVER_LOG_DIR:-${REPO_ROOT}/logs/target_servers}"
START_CHALLENGE_SERVER="${START_CHALLENGE_SERVER:-1}"
KEEP_CHALLENGE_SERVER="${KEEP_CHALLENGE_SERVER:-0}"
CHALLENGE_SERVER_LOG_PATH_USER_SET="${CHALLENGE_SERVER_LOG_PATH_USER_SET:-}"

# ── LLM Proxy ──
LLM_PROXY_BASE_PORT="${LLM_PROXY_BASE_PORT:-8880}"
START_LLM_PROXY="${START_LLM_PROXY:-1}"
MODEL_CONFIG="${MODEL_CONFIG:-${REPO_ROOT}/common/configs/model.yml}"
LLM_PROXY_PID=""

# ── General ──
DRY_RUN="${DRY_RUN:-0}"
LOG_DIR="${LOG_DIR:-}"
SKIP_MODEL_WAIT="${SKIP_MODEL_WAIT:-0}"

# ── Internal state ──
CTF_NAMESPACE=""
CHALLENGE_SERVER_URL=""
CHALLENGE_SERVER_PID=""
CHALLENGE_SERVER_STARTED_BY_SCRIPT=0
CHALLENGE_SERVER_LOG_PATH=""

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { printf '[%s] %s\n' "$(ts)" "$*"; }

slugify() {
  local raw="$1"
  raw="${raw,,}"
  raw="${raw//[^a-z0-9]/_}"
  raw="$(printf '%s' "${raw}" | sed -E 's/_+/_/g; s/^_+//; s/_+$//')"
  printf '%s\n' "${raw:-default}"
}

# ─────────────────────────────────────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────────────────────────────────────
INTERRUPTED=0

cleanup_all() {
  if [[ -n "${LLM_PROXY_PID}" ]] && kill -0 "${LLM_PROXY_PID}" 2>/dev/null; then
    log "Stopping LLM proxy pid=${LLM_PROXY_PID}"
    kill "${LLM_PROXY_PID}" 2>/dev/null || true
  fi
  cleanup_challenge_server
}

handle_interrupt() {
  if [[ "${INTERRUPTED}" == "1" ]]; then return 130; fi
  INTERRUPTED=1
  trap - INT TERM
  log "Interrupted; cleaning up"
  cleanup_all
  exit 130
}

trap handle_interrupt INT TERM
trap cleanup_all EXIT

# ─────────────────────────────────────────────────────────────────────────────
# Model probe
# ─────────────────────────────────────────────────────────────────────────────
probe_model_once() {
  "${PYTHON_BIN}" -c "
import sys
sys.path.insert(0, '${HGM_DIR}')
from llm import create_client
client, model = create_client('${MODEL}')
r = client.chat.completions.create(
    model=model,
    messages=[{'role': 'user', 'content': 'Reply: OK'}],
    max_tokens=4, temperature=0, timeout=30,
)
content = (r.choices[0].message.content or '').strip()
print('ok:', content or '(empty but responded)')
" 2>&1
}

WATCH_INTERVAL_S="${WATCH_INTERVAL_S:-30}"

wait_for_model() {
  if [[ "${SKIP_MODEL_WAIT}" == "1" ]]; then
    log "Skipping model probe (SKIP_MODEL_WAIT=1)"
    return 0
  fi
  log "Waiting for model: ${MODEL} (will retry indefinitely every ${WATCH_INTERVAL_S}s)"
  local attempts=0
  until probe_model_once >> "${LOG_DIR}/probe.log" 2>&1; do
    attempts=$((attempts + 1))
    log "Model probe failed (attempt ${attempts}); retrying in ${WATCH_INTERVAL_S}s"
    sleep "${WATCH_INTERVAL_S}"
  done
  log "Model probe OK (after ${attempts} retries)"
}

# ─────────────────────────────────────────────────────────────────────────────
# Stage 0: Challenge Server
# ─────────────────────────────────────────────────────────────────────────────
setup_challenge_server() {
  local model_slug
  model_slug="$(slugify "${MODEL}")"
  CTF_NAMESPACE="$(normalize_namespace_part "hgm_cyber_${model_slug}")"
  export CTF_NAMESPACE
  export CHALLENGE_SERVER_PORT
  CHALLENGE_SERVER_URL="http://${CHALLENGE_SERVER_PUBLIC_HOST}:${CHALLENGE_SERVER_PORT}"
  export CHALLENGE_SERVER_URL
  CHALLENGE_SERVER_LOG_PATH="${CHALLENGE_SERVER_LOG_DIR}/${CTF_NAMESPACE}_${CHALLENGE_SERVER_PORT}.log"
  export CHALLENGE_SERVER_LOG_PATH

  banner "Stage 0: Challenge Server"
  log "Namespace:  ${CTF_NAMESPACE}"
  log "Server URL: ${CHALLENGE_SERVER_URL}"

  # Check if server is already running
  if challenge_server_ready "${CHALLENGE_SERVER_URL}" 2>/dev/null; then
    log "Challenge server already running at ${CHALLENGE_SERVER_URL}"
    return 0
  fi

  start_challenge_server
  log "Challenge server started: pid=${CHALLENGE_SERVER_PID}"
}

start_llm_proxy() {
  if [[ "${START_LLM_PROXY}" != "1" ]]; then
    return 0
  fi
  banner "LLM Proxy"

  # Kill any leftover proxy from previous runs
  pkill -f "llm_proxy.py" 2>/dev/null || true
  sleep 1

  local proxy_log="${LOG_DIR}/llm_proxy.log"
  "${PYTHON_BIN}" "${HGM_DIR}/llm_proxy.py" \
    --model-yml "${MODEL_CONFIG}" \
    --base-port "${LLM_PROXY_BASE_PORT}" \
    > "${proxy_log}" 2>&1 &
  LLM_PROXY_PID=$!
  sleep 2

  if ! kill -0 "${LLM_PROXY_PID}" 2>/dev/null; then
    log "ERROR: LLM proxy failed to start. Log:"
    cat "${proxy_log}"
    return 1
  fi

  log "LLM proxy started: pid=${LLM_PROXY_PID}"
  grep "Proxy :" "${proxy_log}" || true
}

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Evolution
# ─────────────────────────────────────────────────────────────────────────────
run_evolution() {
  banner "Stage 1: Evolution (max_task_evals=${MAX_TASK_EVALS}, alpha=${ALPHA}, workers=${MAX_WORKERS})"

  local evolve_cmd=(
    "${PYTHON_BIN}" -u "${HGM_DIR}/hgm_cyber.py"
    --config "${HGM_DIR}/config_cyber.yaml"
    --model "${MODEL}"
    --benchmark "${BENCHMARK}"
    --max_task_evals "${MAX_TASK_EVALS}"
    --max_workers "${MAX_WORKERS}"
    --step_limit "${STEP_LIMIT}"
    --alpha "${ALPHA}"
    --evaluation_timeout "${EVAL_TIMEOUT}"
    --self_improve_timeout "${SELF_IMPROVE_TIMEOUT}"
    --server_url "${CHALLENGE_SERVER_URL}"
  )

  [[ -n "${CATEGORIES}" ]] && evolve_cmd+=(--categories "${CATEGORIES}")

  evolve_cmd+=(--output_dir "${EVOLVE_OUTPUT_DIR}")
  if [[ -n "${CONTINUE_FROM}" ]]; then
    evolve_cmd+=(--continue_from "${CONTINUE_FROM}")
  fi
  if [[ -n "${MAX_INPUT_TOKENS}" ]]; then
    evolve_cmd+=(--max_input_tokens "${MAX_INPUT_TOKENS}")
  fi
  if [[ -n "${MAX_OUTPUT_TOKENS}" ]]; then
    evolve_cmd+=(--max_output_tokens "${MAX_OUTPUT_TOKENS}")
  fi
  if [[ -n "${MAX_TOTAL_TOKENS}" ]]; then
    evolve_cmd+=(--max_total_tokens "${MAX_TOTAL_TOKENS}")
  fi

  log "Command: ${evolve_cmd[*]}"

  if [[ "${DRY_RUN}" == "1" ]]; then
    log "[DRY RUN] Would run evolution"
    return 0
  fi

  local evolve_log="${LOG_DIR}/cyber_evolver.evolve.log"

  cd "${HGM_DIR}"
  local code=0
  "${evolve_cmd[@]}" > "${evolve_log}" 2>&1 || code=$?

  if [[ "${code}" != "0" ]]; then
    log "Evolution failed (exit=${code}). Last 20 lines:"
    tail -20 "${evolve_log}" || true
    return "${code}"
  fi

  log "Evolution complete."
  grep -A 30 "RUN COMPLETE" "${evolve_log}" || true
}

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: Final evaluation
# ─────────────────────────────────────────────────────────────────────────────
run_final_eval() {
  banner "Stage 2: Final Eval (pass@${PASS_N}, top_k=${TOP_K}, workers=${EVAL_WORKERS})"

  if [[ -z "${EVOLVE_OUTPUT_DIR}" ]]; then
    log "ERROR: EVOLVE_OUTPUT_DIR not set"
    return 1
  fi
  if [[ ! -d "${EVOLVE_OUTPUT_DIR}" ]]; then
    log "ERROR: ${EVOLVE_OUTPUT_DIR} does not exist"
    return 1
  fi

  local eval_log="${LOG_DIR}/final_eval.log"
  log "Eval log: ${eval_log}"

  local eval_cmd=(
    "${PYTHON_BIN}" -u "${HGM_DIR}/eval_best_agent.py"
    --evolve_output_dir "${EVOLVE_OUTPUT_DIR}"
    --model "${MODEL}"
    --benchmark "${BENCHMARK}"
    --pass_n "${PASS_N}"
    --top_k "${TOP_K}"
    --max_workers "${EVAL_WORKERS}"
    --step_limit "${EVAL_STEP_LIMIT}"
    --evaluation_timeout "${EVAL_TIMEOUT_S}"
    --server_url "${CHALLENGE_SERVER_URL}"
    --skip_probe
  )

  [[ -n "${CATEGORIES}" ]] && eval_cmd+=(--categories "${CATEGORIES}")

  log "Command: ${eval_cmd[*]}"

  if [[ "${DRY_RUN}" == "1" ]]; then
    log "[DRY RUN] Would run final eval"
    return 0
  fi

  cd "${HGM_DIR}"
  local code=0
  "${eval_cmd[@]}" > "${eval_log}" 2>&1 || code=$?

  if [[ "${code}" != "0" ]]; then
    log "Final eval failed (exit=${code}). Last 20 lines:"
    tail -20 "${eval_log}" || true
    return "${code}"
  fi

  log "Final eval complete."
  grep -A 50 "FINAL RESULTS" "${eval_log}" || true
}

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
main() {
  local model_slug
  model_slug="$(slugify "${MODEL}")"

  # LOG_DIR = EVOLVE_OUTPUT_DIR (all logs in one place)
  if [[ -z "${LOG_DIR}" ]]; then
    if [[ -n "${CONTINUE_FROM}" ]]; then
      # Resume: write into the same directory
      LOG_DIR="$(cd "${CONTINUE_FROM}" && pwd)"
    elif [[ -n "${EVOLVE_OUTPUT_DIR}" ]]; then
      LOG_DIR="$(cd "${EVOLVE_OUTPUT_DIR}" && pwd)"
    else
      local run_name="${MODEL//\//_}__${BENCHMARK}"
      [[ -n "${CATEGORIES}" ]] && run_name+="__${CATEGORIES//,/_}"
      LOG_DIR="${HGM_DIR}/output_hgm_cyber/${run_name}__$(date +%Y%m%d_%H%M%S)"
    fi
  fi
  mkdir -p "${LOG_DIR}"
  EVOLVE_OUTPUT_DIR="${LOG_DIR}"

  banner "HGM Cyber Agent Pipeline"
  log "Model:      ${MODEL}"
  log "Benchmark:  ${BENCHMARK}"
  log "Evolve:     max_evals=${MAX_TASK_EVALS} alpha=${ALPHA} workers=${MAX_WORKERS} step=${STEP_LIMIT}"
  log "Final eval: pass@${PASS_N} top_k=${TOP_K} workers=${EVAL_WORKERS}"
  log "Output:     ${LOG_DIR}"

  # Save config for reproducibility
  {
    echo "# $(ts)"
    env | grep -E '^(MODEL|BENCHMARK|MAX_|STEP_|ALPHA|EVAL_|PASS_N|TOP_K|SKIP_|CHALLENGE_SERVER)' | sort
  } > "${LOG_DIR}/env.txt" 2>/dev/null || true

  # Stage 0: Challenge server + LLM proxy
  setup_challenge_server
  start_llm_proxy

  # Model probe
  wait_for_model

  # Stage 1: Evolution
  if [[ "${SKIP_EVOLVE}" != "1" ]]; then
    run_evolution
  else
    log "Skipping evolution (SKIP_EVOLVE=1)"
    if [[ -z "${EVOLVE_OUTPUT_DIR}" ]]; then
      log "ERROR: SKIP_EVOLVE=1 but EVOLVE_OUTPUT_DIR not set"
      return 1
    fi
  fi

  # Stage 2: Final eval
  if [[ "${SKIP_EVAL}" != "1" ]]; then
    run_final_eval
  else
    log "Skipping final eval (SKIP_EVAL=1)"
  fi

  banner "Pipeline Complete"
  log "Logs:          ${LOG_DIR}"
  log "Evolve output: ${EVOLVE_OUTPUT_DIR:-N/A}"
}

main "$@"
