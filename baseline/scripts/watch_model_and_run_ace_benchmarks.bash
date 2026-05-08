#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"
source "${REPO_ROOT}/common/scripts/lib/challenge_run_helpers.sh"

PYTHON_BIN="${PYTHON_BIN:-/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python}"
BATCH_SCRIPT="${BATCH_SCRIPT:-baseline/batch/run_batch_baseline.py}"
CHALLENGE_SERVER_SCRIPT="${CHALLENGE_SERVER_SCRIPT:-bench_hub/server/challenge_server.py}"
MODEL_CONFIG="${MODEL_CONFIG:-common/configs/model.yml}"
MODEL="${MODEL:-Kimi-K2.5}"
MODEL_TAG="${MODEL_TAG:-}"

WATCH_INTERVAL_S="${WATCH_INTERVAL_S:-60}"
PROBE_TIMEOUT_S="${PROBE_TIMEOUT_S:-30}"

slugify_model_tag() {
  local raw="$1"
  raw="${raw,,}"
  raw="${raw//[^a-z0-9]/_}"
  raw="$(printf '%s' "${raw}" | sed -E 's/_+/_/g; s/^_+//; s/_+$//')"
  if [[ -z "${raw}" ]]; then
    raw="model"
  fi
  printf '%s\n' "${raw}"
}

if [[ -z "${MODEL_TAG}" ]]; then
  MODEL_TAG="$(slugify_model_tag "${MODEL}")"
fi

RUN_ID_PREFIX="${RUN_ID_PREFIX:-${MODEL_TAG}_ace_$(date +%Y%m%d_%H%M%S)}"

ACE_AGENT="${ACE_AGENT:-ace_agent}"
ACE_RUN_MODE="${ACE_RUN_MODE:-challenge}"
ACE_EVOLVE_DEPTH="${ACE_EVOLVE_DEPTH:-16}"
ACE_EXTEND_DEPTH="${ACE_EXTEND_DEPTH:-}"
ACE_PROMPT_PROFILE="${ACE_PROMPT_PROFILE:-}"
ACE_PLAYBOOK_SCOPE="${ACE_PLAYBOOK_SCOPE:-benchmark}"
ACE_BATCH_SIZE="${ACE_BATCH_SIZE:-}"
ACE_BATCH_ORDER="${ACE_BATCH_ORDER:-sorted}"
ACE_CURATE_MODE="${ACE_CURATE_MODE:-batch}"
ACE_WORKER_ALLOCATION="${ACE_WORKER_ALLOCATION:-lane-balanced}"
ACE_SAMPLES="${ACE_SAMPLES:-1}"
ACE_CHALLENGES="${ACE_CHALLENGES:-${CHALLENGES:-}}"
STEP_LIMIT="${STEP_LIMIT:-30}"
NYU_WORKERS="${NYU_WORKERS:-24}"
DOWNSTREAM_WORKERS="${DOWNSTREAM_WORKERS:-24}"
NYU_RESUME_RUN_DIR="${NYU_RESUME_RUN_DIR:-}"
CVE_ZERO_DAY_RESUME_RUN_DIR="${CVE_ZERO_DAY_RESUME_RUN_DIR:-}"
CVE_ONE_DAY_RESUME_RUN_DIR="${CVE_ONE_DAY_RESUME_RUN_DIR:-}"
AUTOPEN_RESUME_RUN_DIR="${AUTOPEN_RESUME_RUN_DIR:-}"

CHALLENGE_SERVER_BIND_HOST="${CHALLENGE_SERVER_BIND_HOST:-0.0.0.0}"
CHALLENGE_SERVER_PUBLIC_HOST="${CHALLENGE_SERVER_PUBLIC_HOST:-127.0.0.1}"
CHALLENGE_SERVER_READY_TIMEOUT_S="${CHALLENGE_SERVER_READY_TIMEOUT_S:-60}"
CHALLENGE_SERVER_LOG_DIR="${CHALLENGE_SERVER_LOG_DIR:-logs/target_servers}"
START_CHALLENGE_SERVER="${START_CHALLENGE_SERVER:-1}"
CHALLENGE_SERVER_LOG_PATH_USER_SET="${CHALLENGE_SERVER_LOG_PATH_USER_SET:-}"
KEEP_CHALLENGE_SERVER="${KEEP_CHALLENGE_SERVER:-0}"
DRY_RUN="${DRY_RUN:-0}"
SKIP_MODEL_WAIT="${SKIP_MODEL_WAIT:-0}"
RUN_STAGES="${RUN_STAGES:-nyu,cvebench,autopenbench}"
CVE_SETTINGS="${CVE_SETTINGS:-zero_day,one_day}"

WATCH_LOG_DIR="${WATCH_LOG_DIR:-baseline/logs/watchers/${RUN_ID_PREFIX}}"
mkdir -p "${WATCH_LOG_DIR}"
BACKGROUND_PIDS=()
INTERRUPTED=0

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" | tee -a "${WATCH_LOG_DIR}/watcher.log"
}

register_background_pid() {
  BACKGROUND_PIDS+=("$1")
}

forget_background_pid() {
  local done_pid="$1"
  local remaining=()
  local pid
  for pid in "${BACKGROUND_PIDS[@]:-}"; do
    if [[ "${pid}" != "${done_pid}" ]]; then
      remaining+=("${pid}")
    fi
  done
  BACKGROUND_PIDS=("${remaining[@]}")
}

terminate_process_tree() {
  local pid="$1"
  local signal="${2:-TERM}"
  local child
  if [[ -z "${pid}" ]]; then
    return 0
  fi
  while read -r child; do
    if [[ -n "${child}" ]]; then
      terminate_process_tree "${child}" "${signal}"
    fi
  done < <(pgrep -P "${pid}" 2>/dev/null || true)
  if kill -0 "${pid}" 2>/dev/null; then
    kill "-${signal}" "${pid}" 2>/dev/null || true
  fi
}

cleanup_background_runs() {
  local pid
  for pid in "${BACKGROUND_PIDS[@]:-}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      log "Stopping background run tree pid=${pid}"
      terminate_process_tree "${pid}" TERM
    fi
  done
  sleep 2
  for pid in "${BACKGROUND_PIDS[@]:-}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      log "Force stopping background run tree pid=${pid}"
      terminate_process_tree "${pid}" KILL
    fi
  done
  for pid in "${BACKGROUND_PIDS[@]:-}"; do
    if [[ -n "${pid}" ]]; then
      wait "${pid}" 2>/dev/null || true
    fi
  done
}

run_registered_ace_evolution() {
  local __result_var="$1"
  shift
  local pid
  local code

  run_ace_evolution_with_isolated_ctfserver "$@" &
  pid=$!
  register_background_pid "${pid}"
  if wait "${pid}"; then
    code=0
  else
    code=$?
  fi
  forget_background_pid "${pid}"
  printf -v "${__result_var}" '%s' "${code}"
}

handle_interrupt() {
  if [[ "${INTERRUPTED}" == "1" ]]; then
    return 130
  fi
  INTERRUPTED=1
  trap - INT TERM
  trap - EXIT
  log "Interrupted; cleaning up active benchmark runs"
  cleanup_background_runs
  exit 130
}

trap handle_interrupt INT TERM
trap cleanup_background_runs EXIT

probe_model_once() {
  "${PYTHON_BIN}" - "${MODEL_CONFIG}" "${MODEL}" "${PROBE_TIMEOUT_S}" <<'PY'
import json
import sys
import urllib.error
import urllib.request

import yaml

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
    "max_tokens": 4,
    "temperature": 0,
}
req = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + str(cfg.get("openai_api_key", "")),
    },
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
    log "Skipping model probe because SKIP_MODEL_WAIT=1"
    return 0
  fi
  log "Watching model=${MODEL} via ${MODEL_CONFIG}"
  until probe_model_once >> "${WATCH_LOG_DIR}/probe.log" 2>&1; do
    log "Model probe failed; retrying in ${WATCH_INTERVAL_S}s"
    sleep "${WATCH_INTERVAL_S}"
  done
  log "Model probe succeeded"
}

run_ace_evolution_with_isolated_ctfserver() (
  local benchmark="$1"
  local workers="$2"
  local port="$3"
  local run_suffix="$4"
  local prompt_variant="${5:-}"
  local resume_run_dir="${6:-}"
  local namespace
  namespace="$(normalize_namespace_part "${RUN_ID_PREFIX}_${run_suffix}")"

  export CTF_NAMESPACE="${namespace}"
  export CHALLENGE_SERVER_PORT="${port}"
  export CHALLENGE_SERVER_URL="http://${CHALLENGE_SERVER_PUBLIC_HOST}:${CHALLENGE_SERVER_PORT}"
  export CHALLENGE_SERVER_LOG_PATH="${CHALLENGE_SERVER_LOG_DIR}/${CTF_NAMESPACE}_${CHALLENGE_SERVER_PORT}.log"
  export CHALLENGE_SERVER_PID=""
  export CHALLENGE_SERVER_STARTED_BY_SCRIPT=0

  readarray -t _challenge_server_url_parts < <(parse_url_host_port "${CHALLENGE_SERVER_URL}")
  CHALLENGE_SERVER_PUBLIC_HOST="${_challenge_server_url_parts[0]}"
  CHALLENGE_SERVER_PORT="${_challenge_server_url_parts[1]}"

  start_challenge_server

  local stdout_log="${WATCH_LOG_DIR}/${run_suffix}.stdout.log"
  local cmd_log="${WATCH_LOG_DIR}/${run_suffix}.cmd.txt"
  local cmd=(
    "${PYTHON_BIN}" "${BATCH_SCRIPT}"
    --agent "${ACE_AGENT}"
    --model "${MODEL}"
    --benchmark "${benchmark}"
    --challenge-server-url "${CHALLENGE_SERVER_URL}"
    --max-workers "${workers}"
    --step-limit "${STEP_LIMIT}"
    --run-id "${RUN_ID_PREFIX}_${run_suffix}"
  )
  if [[ "${ACE_RUN_MODE}" == "challenge" ]]; then
    cmd+=(--ace-evolve-mode challenge)
    if [[ -n "${ACE_EXTEND_DEPTH}" ]]; then
      cmd+=(--ace-extend-depth "${ACE_EXTEND_DEPTH}")
    else
      cmd+=(--ace-evolve-depth "${ACE_EVOLVE_DEPTH}")
    fi
    if [[ -n "${resume_run_dir}" ]]; then
      cmd+=(--resume-run-dir "${resume_run_dir}")
    fi
  else
    cmd+=(--ace-evolve-mode batch)
    cmd+=(--ace-playbook-scope "${ACE_PLAYBOOK_SCOPE}")
    cmd+=(--ace-batch-order "${ACE_BATCH_ORDER}")
    cmd+=(--ace-curate-mode "${ACE_CURATE_MODE}")
    cmd+=(--ace-worker-allocation "${ACE_WORKER_ALLOCATION}")
    cmd+=(--samples "${ACE_SAMPLES}")
    if [[ -n "${ACE_BATCH_SIZE}" ]]; then
      cmd+=(--ace-batch-size "${ACE_BATCH_SIZE}")
    fi
    if [[ -n "${resume_run_dir}" ]]; then
      cmd+=(--resume --resume-run-dir "${resume_run_dir}")
    fi
  fi
  if [[ -n "${prompt_variant}" ]]; then
    cmd+=(--prompt-variant "${prompt_variant}")
  fi
  if [[ -n "${ACE_PROMPT_PROFILE}" ]]; then
    cmd+=(--ace-prompt-profile "${ACE_PROMPT_PROFILE}")
  fi
  if [[ -n "${ACE_CHALLENGES}" ]]; then
    cmd+=(--challenges "${ACE_CHALLENGES}")
  fi

  printf '%q ' "${cmd[@]}" > "${cmd_log}"
  printf '\n' >> "${cmd_log}"
  if [[ "${ACE_RUN_MODE}" == "challenge" ]]; then
    log "Starting ${run_suffix}: agent=${ACE_AGENT} run_mode=challenge benchmark=${benchmark} evolve_depth=${ACE_EVOLVE_DEPTH} extend_depth=${ACE_EXTEND_DEPTH:-none} workers=${workers} prompt_variant=${prompt_variant:-default} prompt_profile=${ACE_PROMPT_PROFILE:-default} challenges=${ACE_CHALLENGES:-all} resume_run_dir=${resume_run_dir:-none} challenge_server=${CHALLENGE_SERVER_URL}"
  else
    log "Starting ${run_suffix}: agent=${ACE_AGENT} run_mode=online benchmark=${benchmark} workers=${workers} playbook_scope=${ACE_PLAYBOOK_SCOPE} batch_size=${ACE_BATCH_SIZE:-auto} batch_order=${ACE_BATCH_ORDER} curate_mode=${ACE_CURATE_MODE} worker_allocation=${ACE_WORKER_ALLOCATION} samples=${ACE_SAMPLES} prompt_variant=${prompt_variant:-default} prompt_profile=${ACE_PROMPT_PROFILE:-default} challenges=${ACE_CHALLENGES:-all} resume_run_dir=${resume_run_dir:-none} challenge_server=${CHALLENGE_SERVER_URL}"
  fi
  local code=0
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '+'
    printf ' %q' "${cmd[@]}"
    printf '\n'
  else
    set +e
    "${cmd[@]}" > "${stdout_log}" 2>&1
    code=$?
    set -e
  fi
  log "Finished ${run_suffix}: exit_code=${code} stdout=${stdout_log}"

  if [[ "${KEEP_CHALLENGE_SERVER}" != "1" ]] && [[ -n "${CHALLENGE_SERVER_PID}" ]] && kill -0 "${CHALLENGE_SERVER_PID}" 2>/dev/null; then
    log "Stopping challenge_server for ${run_suffix}: pid=${CHALLENGE_SERVER_PID}"
    kill "${CHALLENGE_SERVER_PID}" 2>/dev/null || true
    wait "${CHALLENGE_SERVER_PID}" 2>/dev/null || true
  fi
  return "${code}"
)

list_has_item() {
  local list="$1"
  local needle="$2"
  local item
  IFS=',' read -ra _items <<< "${list}"
  for item in "${_items[@]}"; do
    item="$(printf '%s' "${item}" | tr '[:upper:]' '[:lower:]' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    if [[ "${item}" == "${needle}" ]]; then
      return 0
    fi
  done
  return 1
}

should_run_stage() {
  local stage="$1"
  local normalized
  normalized="$(printf '%s' "${RUN_STAGES}" | tr '[:upper:]' '[:lower:]')"
  list_has_item "${normalized}" "all" || list_has_item "${normalized}" "${stage}"
}

should_run_cve_setting() {
  local setting="$1"
  local normalized
  normalized="$(printf '%s' "${CVE_SETTINGS}" | tr '[:upper:]' '[:lower:]')"
  list_has_item "${normalized}" "all" || list_has_item "${normalized}" "${setting}"
}

run_nyu_stage() {
  local run_suffix="$1"
  local title="$2"
  local nyu_code

  banner "${title}"
  run_registered_ace_evolution nyu_code "nyu_ctf" "${NYU_WORKERS}" 8300 "${run_suffix}" "" "${NYU_RESUME_RUN_DIR}"
  if [[ "${nyu_code}" != "0" ]]; then
    log "NYUCTFBench stage failed: ${run_suffix}=${nyu_code}"
    exit 1
  fi
  log "NYUCTFBench stage completed"
}

run_cvebench_stage() {
  local suffix_extra="$1"
  local cve_zero_day_code=0
  local cve_one_day_code=0
  local ran_any=0

  if should_run_cve_setting "zero_day"; then
    ran_any=1
    run_registered_ace_evolution cve_zero_day_code "cvebench" "${DOWNSTREAM_WORKERS}" 8320 "cvebench_zero_day${suffix_extra}" "zero_day" "${CVE_ZERO_DAY_RESUME_RUN_DIR}"
  else
    log "Skipping CVEBench zero_day because CVE_SETTINGS=${CVE_SETTINGS}"
  fi

  if should_run_cve_setting "one_day"; then
    ran_any=1
    run_registered_ace_evolution cve_one_day_code "cvebench" "${DOWNSTREAM_WORKERS}" 8340 "cvebench_one_day${suffix_extra}" "one_day" "${CVE_ONE_DAY_RESUME_RUN_DIR}"
  else
    log "Skipping CVEBench one_day because CVE_SETTINGS=${CVE_SETTINGS}"
  fi

  if [[ "${ran_any}" != "1" ]]; then
    log "CVEBench stage selected but no setting matched CVE_SETTINGS=${CVE_SETTINGS}"
    exit 1
  fi
  if [[ "${cve_zero_day_code}" != "0" || "${cve_one_day_code}" != "0" ]]; then
    log "CVEBench stage failed: cvebench_zero_day=${cve_zero_day_code} cvebench_one_day=${cve_one_day_code}"
    exit 1
  fi
}

run_autopenbench_stage() {
  local run_suffix="$1"
  local autopen_code=0

  run_registered_ace_evolution autopen_code "autopenbench" "${DOWNSTREAM_WORKERS}" 8330 "${run_suffix}" "" "${AUTOPEN_RESUME_RUN_DIR}"
  if [[ "${autopen_code}" != "0" ]]; then
    log "AutopenBench stage failed: ${run_suffix}=${autopen_code}"
    exit 1
  fi
}

wait_for_model

if [[ "${ACE_RUN_MODE}" == "challenge" ]]; then
  if should_run_stage "nyu"; then
    run_nyu_stage "nyu_challenge_evolve" "Stage 1: NYUCTFBench ACE runs"
  else
    log "Skipping NYUCTFBench because RUN_STAGES=${RUN_STAGES}"
  fi

  if should_run_stage "cvebench"; then
    banner "Stage 2: CVEBench ACE runs"
    run_cvebench_stage ""
  else
    log "Skipping CVEBench because RUN_STAGES=${RUN_STAGES}"
  fi

  if should_run_stage "autopenbench"; then
    banner "Stage 3: AutopenBench ACE runs"
    run_autopenbench_stage "autopenbench"
  else
    log "Skipping AutopenBench because RUN_STAGES=${RUN_STAGES}"
  fi
else
  if should_run_stage "nyu"; then
    run_nyu_stage "nyu_online" "Stage 1: NYUCTFBench ACE online runs"
  else
    log "Skipping NYUCTFBench because RUN_STAGES=${RUN_STAGES}"
  fi

  if should_run_stage "cvebench"; then
    banner "Stage 2: CVEBench ACE online runs"
    run_cvebench_stage "_online"
  else
    log "Skipping CVEBench because RUN_STAGES=${RUN_STAGES}"
  fi

  if should_run_stage "autopenbench"; then
    banner "Stage 3: AutopenBench ACE online runs"
    run_autopenbench_stage "autopenbench_online"
  else
    log "Skipping AutopenBench because RUN_STAGES=${RUN_STAGES}"
  fi
fi

banner "Completed"
log "All requested ACE benchmark runs completed. Logs: ${WATCH_LOG_DIR}"
