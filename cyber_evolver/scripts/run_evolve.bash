#!/usr/bin/env bash
# =============================================================================
# Unified evolve runner — wait for model, start challenge_server, run evo/raw.
#
# Usage:
#   MODEL=DeepSeek-V3.1-sii BENCHMARK=nyu_ctf bash cyber_evolver/scripts/run_evolve.bash
#   MODEL=Kimi-K2.5-sii     BENCHMARK=autopenbench bash cyber_evolver/scripts/run_evolve.bash
#   MODEL=DeepSeek-V3.1-sii BENCHMARK=cvebench CVE_SETTINGS=zero_day,one_day bash cyber_evolver/scripts/run_evolve.bash
#
# Required env:
#   MODEL         — model key from common/configs/model.yml
#   BENCHMARK     — nyu_ctf | autopenbench | cvebench
#
# Optional env (most have sane defaults):
#   CONFIG_MODES        — comma-separated: "evo,raw" (default: "evo,raw")
#   CVE_SETTINGS        — for cvebench only: "zero_day,one_day" (default: "zero_day,one_day")
#   PROMPT_VARIANT      — override prompt variant (auto-set for cvebench)
#   SEED_INCLUDES_CSV   — extra seed includes, comma-separated
#   MAX_CONCURRENT      — total concurrent agent runs (default: 24)
#   EVOLVE_MAX_WORKERS  — parallel challenges for evolve (default: 4)
#   TASK_WORKERS        — parallel tasks per challenge (default: 6)
#   CHALLENGE_SERVER_PORT     — starting port (default: 8000, auto-finds free port)
#   SKIP_MODEL_WAIT     — set 1 to skip model probe
#   DRY_RUN             — set 1 to print commands without executing
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"
source "${REPO_ROOT}/common/scripts/lib/challenge_run_helpers.sh"

# ── Required ─────────────────────────────────────────────────────────────────
MODEL="${MODEL:?Set MODEL (e.g. DeepSeek-V3.1-sii)}"
BENCHMARK="${BENCHMARK:?Set BENCHMARK (nyu_ctf | autopenbench | cvebench)}"

# ── Paths & binaries ─────────────────────────────────────────────────────────
PYTHON_BIN="${PYTHON_BIN:-python}"
EVOLVE_SCRIPT="${EVOLVE_SCRIPT:-run_evolve_batch.py}"
BASELINE_SCRIPT="${BASELINE_SCRIPT:-baseline/batch/run_batch_baseline.py}"
CHALLENGE_SERVER_SCRIPT="${CHALLENGE_SERVER_SCRIPT:-bench_hub/server/challenge_server.py}"
MODEL_CONFIG="${MODEL_CONFIG:-common/configs/model.yml}"

# ── Run stages ────────────────────────────────────────────────────────────────
CONFIG_MODES="${CONFIG_MODES:-evo,raw}"
CVE_SETTINGS="${CVE_SETTINGS:-zero_day,one_day}"
RUN_ID_PREFIX="${RUN_ID_PREFIX:-${BENCHMARK}}"
DRY_RUN="${DRY_RUN:-0}"

# ── Concurrency ───────────────────────────────────────────────────────────────
MAX_CONCURRENT="${MAX_CONCURRENT:-24}"
EVOLVE_MAX_WORKERS="${EVOLVE_MAX_WORKERS:-4}"
TASK_WORKERS="${TASK_WORKERS:-6}"
LLM_MAX_INFLIGHT="${LLM_MAX_INFLIGHT:-${MAX_CONCURRENT}}"
LLM_MAX_INFLIGHT_PER_LANE="${LLM_MAX_INFLIGHT_PER_LANE:-6}"
LLM_REQUEST_TIMEOUT="${LLM_REQUEST_TIMEOUT:-600}"
LLM_MAX_ATTEMPTS="${LLM_MAX_ATTEMPTS:-3}"
LLM_RESPONSE_TIMEOUT="${LLM_RESPONSE_TIMEOUT:-3600}"
LLM_LARGE_REQUEST_DELAY="${LLM_LARGE_REQUEST_DELAY:-1.0}"

# ── Baseline ──────────────────────────────────────────────────────────────────
RUN_BASELINE="${RUN_BASELINE:-0}"
BASELINE_AGENTS="${BASELINE_AGENTS:-nyuctf_single,dcipher}"
BASELINE_MAX_WORKERS="${BASELINE_MAX_WORKERS:-${MAX_CONCURRENT}}"
BASELINE_STEP_LIMIT="${BASELINE_STEP_LIMIT:-30}"
BASELINE_SAMPLES="${BASELINE_SAMPLES:-1}"

# ── Seed ──────────────────────────────────────────────────────────────────────
BASE_SEED_PATH="${BASE_SEED_PATH:-./cyber_evolver/seed_agent_templates/mini_cyberagent}"
PROMPT_VARIANT="${PROMPT_VARIANT:-}"
SEED_INCLUDES_CSV="${SEED_INCLUDES_CSV:-}"

# ── Challenge Server ────────────────────────────────────────────────────────────────
START_CHALLENGE_SERVER="${START_CHALLENGE_SERVER:-1}"
KEEP_CHALLENGE_SERVER="${KEEP_CHALLENGE_SERVER:-0}"
CHALLENGE_SERVER_BIND_HOST="${CHALLENGE_SERVER_BIND_HOST:-0.0.0.0}"
CHALLENGE_SERVER_PUBLIC_HOST="${CHALLENGE_SERVER_PUBLIC_HOST:-127.0.0.1}"
CHALLENGE_SERVER_PORT="${CHALLENGE_SERVER_PORT:-8000}"
CHALLENGE_SERVER_READY_TIMEOUT_S="${CHALLENGE_SERVER_READY_TIMEOUT_S:-60}"
CHALLENGE_SERVER_LOG_DIR="${CHALLENGE_SERVER_LOG_DIR:-logs/target_servers}"
CHALLENGE_SERVER_LOG_PATH_USER_SET="${CHALLENGE_SERVER_LOG_PATH+x}"

# ── Model probe ───────────────────────────────────────────────────────────────
SKIP_MODEL_WAIT="${SKIP_MODEL_WAIT:-0}"
WATCH_INTERVAL_S="${WATCH_INTERVAL_S:-60}"
PROBE_TIMEOUT_S="${PROBE_TIMEOUT_S:-30}"

# =============================================================================
# Derived values
# =============================================================================

BENCHMARK_NS="$(normalize_namespace_part "${BENCHMARK}")"
MODEL_NS="$(normalize_namespace_part "${MODEL}")"
CTF_NAMESPACE="${CTF_NAMESPACE:-${BENCHMARK_NS}_${MODEL_NS}}"

CHALLENGE_SERVER_URL="http://${CHALLENGE_SERVER_PUBLIC_HOST}:${CHALLENGE_SERVER_PORT}"
readarray -t _url_parts < <(parse_url_host_port "${CHALLENGE_SERVER_URL}")
CHALLENGE_SERVER_PUBLIC_HOST="${_url_parts[0]}"
CHALLENGE_SERVER_PORT="${_url_parts[1]}"

if [[ -z "${CHALLENGE_SERVER_LOG_PATH_USER_SET}" ]]; then
  CHALLENGE_SERVER_LOG_PATH="${CHALLENGE_SERVER_LOG_DIR}/${CTF_NAMESPACE}_${CHALLENGE_SERVER_PORT}.log"
else
  CHALLENGE_SERVER_LOG_PATH="${CHALLENGE_SERVER_LOG_PATH}"
fi

CHALLENGE_SERVER_PID=""
CHALLENGE_SERVER_STARTED_BY_SCRIPT=0

# Default seed includes per benchmark
declare -a SEED_ARGS=()
case "${BENCHMARK}" in
  cvebench)
    SEED_ARGS+=(--seed-include "commands/check_done.py")
    ;;
  autopenbench)
    SEED_ARGS+=(--seed-include "commands/submit.py")
    ;;
  nyu_ctf)
    SEED_ARGS+=(--seed-include "commands/submit.py")
    ;;
esac

if [[ -n "${SEED_INCLUDES_CSV}" ]]; then
  IFS=',' read -r -a _extra_seeds <<< "${SEED_INCLUDES_CSV}"
  for _seed in "${_extra_seeds[@]}"; do
    _seed="${_seed//[[:space:]]/}"
    [[ -n "${_seed}" ]] && SEED_ARGS+=(--seed-include "${_seed}")
  done
fi

LOG_DIR="logs/run_scripts/${CTF_NAMESPACE}_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${LOG_DIR}"

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" | tee -a "${LOG_DIR}/run.log"
}

# =============================================================================
# Model probe (reused from watch script)
# =============================================================================

probe_model_once() {
  "${PYTHON_BIN}" - "${MODEL_CONFIG}" "${MODEL}" "${PROBE_TIMEOUT_S}" <<'PY'
import json, sys, urllib.error, urllib.request, yaml
config_path, model_key, timeout_s = sys.argv[1], sys.argv[2], int(sys.argv[3])
with open(config_path, "r", encoding="utf-8") as fh:
    configs = yaml.safe_load(fh)
if model_key not in configs:
    raise SystemExit(f"model key not found: {model_key}")
cfg = configs[model_key]
url = cfg["openai_api_base"].rstrip("/") + "/chat/completions"
payload = {
    "model": cfg["model"],
    "messages": [{"role": "user", "content": "Reply exactly: OK"}],
    "max_tokens": 4, "temperature": 0,
}
req = urllib.request.Request(
    url, data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json",
             "Authorization": "Bearer " + str(cfg.get("openai_api_key", ""))},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read().decode("utf-8", errors="replace")
except urllib.error.HTTPError as exc:
    detail = exc.read().decode("utf-8", errors="replace")[:240]
    raise SystemExit(f"HTTP {exc.code}: {detail}")
except Exception as exc:
    raise SystemExit(repr(exc))
parsed = json.loads(body)
if not parsed.get("choices"):
    raise SystemExit("missing choices")
print("ok")
PY
}

wait_for_model() {
  if [[ "${SKIP_MODEL_WAIT}" == "1" ]]; then
    log "Skipping model probe (SKIP_MODEL_WAIT=1)"
    return 0
  fi
  log "Probing model=${MODEL} ..."
  until probe_model_once >> "${LOG_DIR}/probe.log" 2>&1; do
    log "Model not ready, retrying in ${WATCH_INTERVAL_S}s ..."
    sleep "${WATCH_INTERVAL_S}"
  done
  log "Model is ready"
}

# =============================================================================
# Helpers
# =============================================================================

list_has_item() {
  local list="$1" needle="$2" item
  IFS=',' read -ra _items <<< "${list}"
  for item in "${_items[@]}"; do
    item="$(printf '%s' "${item}" | tr '[:upper:]' '[:lower:]' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    [[ "${item}" == "${needle}" ]] && return 0
  done
  return 1
}

build_evolve_args() {
  local config_mode="$1"
  local run_id="$2"
  local extra_variant="${3:-}"

  local -a args=(
    --benchmark "${BENCHMARK}"
    --config-mode "${config_mode}"
    --model "${MODEL}"
    --run-id "${run_id}"
    --challenge-server-url "${CHALLENGE_SERVER_URL}"
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
  args+=("${SEED_ARGS[@]}")

  # Prompt variant: explicit > per-run override > global
  if [[ -n "${extra_variant}" ]]; then
    args+=(--prompt-variant "${extra_variant}")
  elif [[ -n "${PROMPT_VARIANT}" ]]; then
    args+=(--prompt-variant "${PROMPT_VARIANT}")
  fi

  printf '%s\n' "${args[@]}"
}

run_evolve_stage() {
  local config_mode="$1"
  local run_id="$2"
  local variant="${3:-}"
  local label="${4:-${config_mode}}"

  local stdout_log="${LOG_DIR}/${run_id}.stdout.log"

  readarray -t args < <(build_evolve_args "${config_mode}" "${run_id}" "${variant}")

  banner "${label}"
  log "Starting: ${label} → ${stdout_log}"

  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '+ %q' "${PYTHON_BIN}" "${EVOLVE_SCRIPT}" "${args[@]}"
    printf '\n'
    return 0
  fi

  set +e
  "${PYTHON_BIN}" "${EVOLVE_SCRIPT}" "${args[@]}" > "${stdout_log}" 2>&1
  local code=$?
  set -e

  if [[ "${code}" == "0" ]]; then
    log "Finished: ${label} (exit=0)"
  else
    log "WARNING: ${label} exited with code=${code}"
  fi
  return "${code}"
}

run_baseline_stage() {
  local agent="$1"
  local run_id="$2"
  local variant="${3:-}"

  local stdout_log="${LOG_DIR}/${run_id}.stdout.log"
  local -a args=(
    --agent "${agent}"
    --model "${MODEL}"
    --benchmark "${BENCHMARK}"
    --max-workers "${BASELINE_MAX_WORKERS}"
    --samples "${BASELINE_SAMPLES}"
    --step-limit "${BASELINE_STEP_LIMIT}"
    --challenge-server-url "${CHALLENGE_SERVER_URL}"
    --run-id "${run_id}"
  )
  if [[ -n "${variant}" ]]; then
    args+=(--prompt-variant "${variant}")
  fi

  banner "Baseline: ${agent} ${variant:+(${variant})}"
  log "Starting baseline: ${agent} → ${stdout_log}"

  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '+ %q' "${PYTHON_BIN}" "${BASELINE_SCRIPT}" "${args[@]}"
    printf '\n'
    return 0
  fi

  set +e
  "${PYTHON_BIN}" "${BASELINE_SCRIPT}" "${args[@]}" > "${stdout_log}" 2>&1
  local code=$?
  set -e

  if [[ "${code}" == "0" ]]; then
    log "Finished baseline: ${agent} (exit=0)"
  else
    log "WARNING: baseline ${agent} exited with code=${code}"
  fi
}

# =============================================================================
# Main
# =============================================================================

log "========================================"
log "Evolve Runner"
log "  MODEL     = ${MODEL}"
log "  BENCHMARK = ${BENCHMARK}"
log "  MODES     = ${CONFIG_MODES}"
log "  WORKERS   = evolve_max=${EVOLVE_MAX_WORKERS} task=${TASK_WORKERS} llm_inflight=${LLM_MAX_INFLIGHT}"
log "  LOG_DIR   = ${LOG_DIR}"
[[ "${BENCHMARK}" == "cvebench" ]] && log "  CVE       = ${CVE_SETTINGS}"
log "========================================"

# Step 1: Wait for model
wait_for_model

# Step 2: Start Challenge server
start_challenge_server

# Step 3: Run stages
stage=0
total_stages=0

# Count stages
IFS=',' read -ra _modes <<< "${CONFIG_MODES}"
if [[ "${BENCHMARK}" == "cvebench" ]]; then
  IFS=',' read -ra _cve_settings <<< "${CVE_SETTINGS}"
  total_stages=$(( ${#_modes[@]} * ${#_cve_settings[@]} ))
else
  total_stages=${#_modes[@]}
fi
if [[ "${RUN_BASELINE}" == "1" ]]; then
  IFS=',' read -ra _baseline_agents <<< "${BASELINE_AGENTS}"
  if [[ "${BENCHMARK}" == "cvebench" ]]; then
    total_stages=$(( total_stages + ${#_baseline_agents[@]} * ${#_cve_settings[@]} ))
  else
    total_stages=$(( total_stages + ${#_baseline_agents[@]} ))
  fi
fi

# Execute evolve stages
if [[ "${BENCHMARK}" == "cvebench" ]]; then
  for config_mode in "${_modes[@]}"; do
    config_mode="$(printf '%s' "${config_mode}" | tr -d '[:space:]')"
    for cve_setting in "${_cve_settings[@]}"; do
      cve_setting="$(printf '%s' "${cve_setting}" | tr -d '[:space:]')"
      stage=$((stage + 1))
      run_evolve_stage \
        "${config_mode}" \
        "${RUN_ID_PREFIX}_${config_mode}_${cve_setting}" \
        "${cve_setting}" \
        "Stage ${stage}/${total_stages}: ${config_mode} ${cve_setting}" \
        || true
    done
  done
else
  for config_mode in "${_modes[@]}"; do
    config_mode="$(printf '%s' "${config_mode}" | tr -d '[:space:]')"
    stage=$((stage + 1))
    run_evolve_stage \
      "${config_mode}" \
      "${RUN_ID_PREFIX}_${config_mode}" \
      "" \
      "Stage ${stage}/${total_stages}: ${config_mode}" \
      || true
  done
fi

# Execute baseline stages
if [[ "${RUN_BASELINE}" == "1" ]]; then
  IFS=',' read -ra _baseline_agents <<< "${BASELINE_AGENTS}"
  if [[ "${BENCHMARK}" == "cvebench" ]]; then
    for agent in "${_baseline_agents[@]}"; do
      agent="$(printf '%s' "${agent}" | tr -d '[:space:]')"
      for cve_setting in "${_cve_settings[@]}"; do
        cve_setting="$(printf '%s' "${cve_setting}" | tr -d '[:space:]')"
        stage=$((stage + 1))
        run_baseline_stage "${agent}" "${RUN_ID_PREFIX}_baseline_${agent}_${cve_setting}" "${cve_setting}" || true
      done
    done
  else
    for agent in "${_baseline_agents[@]}"; do
      agent="$(printf '%s' "${agent}" | tr -d '[:space:]')"
      stage=$((stage + 1))
      run_baseline_stage "${agent}" "${RUN_ID_PREFIX}_baseline_${agent}" "" || true
    done
  fi
fi

banner "All done"
log "MODEL=${MODEL} BENCHMARK=${BENCHMARK} MODES=${CONFIG_MODES}"
log "Logs: ${LOG_DIR}"
