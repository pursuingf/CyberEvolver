#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"
source "${REPO_ROOT}/common/scripts/lib/challenge_run_helpers.sh"

PYTHON_BIN="${PYTHON_BIN:-/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python}"
BASELINE_SCRIPT="${BASELINE_SCRIPT:-baseline/batch/run_batch_baseline.py}"
ACE_WATCHER_SCRIPT="${ACE_WATCHER_SCRIPT:-scripts/watch_model_and_run_ace_benchmarks.bash}"
CHALLENGE_SERVER_SCRIPT="${CHALLENGE_SERVER_SCRIPT:-bench_hub/server/challenge_server.py}"
MODEL_CONFIG="${MODEL_CONFIG:-common/configs/model.yml}"

MODELS="${MODELS:-DeepSeek-V3.1-sii,Kimi-K2.5-sii,Minimax-2.5-sii,Qwen3-235B-A22B-Instruct-2507-sii}"
RUN_STAGES="${RUN_STAGES:-autopenbench,nyuctfbench,ace_online,cvebench}"

BASELINE_SAMPLES="${BASELINE_SAMPLES:-3}"
MAX_WORKERS="${MAX_WORKERS:-24}"
STEP_LIMIT="${STEP_LIMIT:-30}"
ACE_AGENT="${ACE_AGENT:-ace_bash_agent}"
ACE_ONLINE_RUNS="${ACE_ONLINE_RUNS:-3}"
FORCE_DISABLE_THINKING="${FORCE_DISABLE_THINKING:-1}"

DRY_RUN="${DRY_RUN:-0}"
START_CHALLENGE_SERVER="${START_CHALLENGE_SERVER:-1}"
KEEP_CHALLENGE_SERVER="${KEEP_CHALLENGE_SERVER:-0}"
SKIP_MODEL_WAIT="${SKIP_MODEL_WAIT:-0}"
WATCH_INTERVAL_S="${WATCH_INTERVAL_S:-60}"
PROBE_TIMEOUT_S="${PROBE_TIMEOUT_S:-30}"

CHALLENGE_SERVER_BIND_HOST="${CHALLENGE_SERVER_BIND_HOST:-0.0.0.0}"
CHALLENGE_SERVER_PUBLIC_HOST="${CHALLENGE_SERVER_PUBLIC_HOST:-127.0.0.1}"
CHALLENGE_SERVER_READY_TIMEOUT_S="${CHALLENGE_SERVER_READY_TIMEOUT_S:-60}"
CHALLENGE_SERVER_LOG_DIR="${CHALLENGE_SERVER_LOG_DIR:-logs/target_servers}"

CAMPAIGN_ID="${CAMPAIGN_ID:-pass3_$(date +%Y%m%d_%H%M%S)}"
CAMPAIGN_DIR="${CAMPAIGN_DIR:-baseline/logs/batch/pass3_campaign/${CAMPAIGN_ID}}"
MANIFEST_PATH="${CAMPAIGN_DIR}/manifest.tsv"
mkdir -p "${CAMPAIGN_DIR}"
declare -A MODEL_READY_CACHE=()

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" | tee -a "${CAMPAIGN_DIR}/campaign.log"
}

append_manifest() {
  local model="$1"
  local stage="$2"
  local benchmark="$3"
  local agent="$4"
  local run_id="$5"
  local details="${6:-}"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "${model}" "${stage}" "${benchmark}" "${agent}" "${run_id}" "${details}" >> "${MANIFEST_PATH}"
}

probe_model_once() {
  local model="$1"
  "${PYTHON_BIN}" - "${MODEL_CONFIG}" "${model}" "${PROBE_TIMEOUT_S}" <<'PY'
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
  local model="$1"
  local model_slug probe_log
  if [[ "${DRY_RUN}" == "1" ]]; then
    log "Skipping model probe for ${model} because DRY_RUN=1"
    MODEL_READY_CACHE["${model}"]=1
    return 0
  fi
  if [[ "${SKIP_MODEL_WAIT}" == "1" ]]; then
    log "Skipping model probe for ${model} because SKIP_MODEL_WAIT=1"
    MODEL_READY_CACHE["${model}"]=1
    return 0
  fi
  if [[ "${MODEL_READY_CACHE[${model}]:-}" == "1" ]]; then
    return 0
  fi

  model_slug="$(normalize_namespace_part "${model}")"
  probe_log="${CAMPAIGN_DIR}/probe_${model_slug}.log"
  log "Watching model=${model} via ${MODEL_CONFIG}"
  until probe_model_once "${model}" >> "${probe_log}" 2>&1; do
    log "Model probe failed for ${model}; retrying in ${WATCH_INTERVAL_S}s"
    sleep "${WATCH_INTERVAL_S}"
  done
  log "Model probe succeeded for ${model}"
  MODEL_READY_CACHE["${model}"]=1
}

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

prepare_stage_challenge_server() {
  local namespace="$1"
  local start_port="$2"

  CTF_NAMESPACE="${namespace}"
  CHALLENGE_SERVER_PORT="${start_port}"
  CHALLENGE_SERVER_URL="http://${CHALLENGE_SERVER_PUBLIC_HOST}:${CHALLENGE_SERVER_PORT}"
  CHALLENGE_SERVER_LOG_PATH_USER_SET=""
  CHALLENGE_SERVER_LOG_PATH="${CHALLENGE_SERVER_LOG_DIR}/${CTF_NAMESPACE}_${CHALLENGE_SERVER_PORT}.log"
  CHALLENGE_SERVER_PID=""
  CHALLENGE_SERVER_STARTED_BY_SCRIPT=0
  readarray -t _challenge_server_url_parts < <(parse_url_host_port "${CHALLENGE_SERVER_URL}")
  CHALLENGE_SERVER_PUBLIC_HOST="${_challenge_server_url_parts[0]}"
  CHALLENGE_SERVER_PORT="${_challenge_server_url_parts[1]}"
}

cleanup_stage_challenge_server() {
  cleanup_challenge_server || true
  CHALLENGE_SERVER_PID=""
  CHALLENGE_SERVER_STARTED_BY_SCRIPT=0
}

run_batch_stage() {
  local model="$1"
  local stage="$2"
  local benchmark="$3"
  local agent="$4"
  local run_id="$5"
  local start_port="$6"
  local prompt_variant="${7:-}"

  local namespace
  wait_for_model "${model}"
  namespace="$(normalize_namespace_part "${CAMPAIGN_ID}_${model}_${stage}_${agent}_${prompt_variant:-default}")"
  prepare_stage_challenge_server "${namespace}" "${start_port}"
  start_challenge_server

  local cmd=(
    env
    FORCE_DISABLE_THINKING="${FORCE_DISABLE_THINKING}"
    "${PYTHON_BIN}" "${BASELINE_SCRIPT}"
    --agent "${agent}"
    --model "${model}"
    --benchmark "${benchmark}"
    --max-workers "${MAX_WORKERS}"
    --samples "${BASELINE_SAMPLES}"
    --step-limit "${STEP_LIMIT}"
    --challenge-server-url "${CHALLENGE_SERVER_URL}"
    --run-id "${run_id}"
  )
  if [[ -n "${prompt_variant}" ]]; then
    cmd+=(--prompt-variant "${prompt_variant}")
  fi

  log "Starting ${stage}: model=${model} benchmark=${benchmark} agent=${agent} run_id=${run_id}"
  run_cmd "${cmd[@]}"
  append_manifest "${model}" "${stage}" "${benchmark}" "${agent}" "${run_id}" "${prompt_variant:-default}"
  cleanup_stage_challenge_server
}

run_autopenbench_baselines() {
  banner "Stage 1: AutoPenBench baselines"
  local model slug
  IFS=',' read -ra _models <<< "${MODELS}"
  for model in "${_models[@]}"; do
    slug="$(normalize_namespace_part "${model}")"
    run_batch_stage "${model}" "autopenbench" "autopenbench" "autopenbench" "${slug}_pass3_autopenbench_autopenbench" 8500
    run_batch_stage "${model}" "autopenbench" "autopenbench" "vulnbot" "${slug}_pass3_autopenbench_vulnbot" 8510
  done
}

run_nyuctfbench_baselines() {
  banner "Stage 2: NYUCTFBench baselines"
  local model slug
  IFS=',' read -ra _models <<< "${MODELS}"
  for model in "${_models[@]}"; do
    slug="$(normalize_namespace_part "${model}")"
    run_batch_stage "${model}" "nyuctfbench" "nyu_ctf" "nyuctf_single" "${slug}_pass3_nyuctfbench_nyuctf_single" 8520
    run_batch_stage "${model}" "nyuctfbench" "nyu_ctf" "dcipher" "${slug}_pass3_nyuctfbench_dcipher" 8530
  done
}

run_ace_online_pass3() {
  banner "Stage 3: ACE online pass@3"
  local model slug run_idx run_prefix
  IFS=',' read -ra _models <<< "${MODELS}"
  for model in "${_models[@]}"; do
    slug="$(normalize_namespace_part "${model}")"
    wait_for_model "${model}"
    for run_idx in $(seq 1 "${ACE_ONLINE_RUNS}"); do
      run_prefix="${slug}_ace_online_r${run_idx}"
      log "Starting ace_online: model=${model} run=${run_prefix}"
      run_cmd env \
        FORCE_DISABLE_THINKING="${FORCE_DISABLE_THINKING}" \
        MODEL="${model}" \
        ACE_AGENT="${ACE_AGENT}" \
        ACE_RUN_MODE="online" \
        ACE_SAMPLES="1" \
        NYU_WORKERS="${MAX_WORKERS}" \
        DOWNSTREAM_WORKERS="${MAX_WORKERS}" \
        STEP_LIMIT="${STEP_LIMIT}" \
        RUN_ID_PREFIX="${run_prefix}" \
        RUN_STAGES="nyu,cvebench,autopenbench" \
        CVE_SETTINGS="zero_day,one_day" \
        START_CHALLENGE_SERVER="${START_CHALLENGE_SERVER}" \
        KEEP_CHALLENGE_SERVER="${KEEP_CHALLENGE_SERVER}" \
        SKIP_MODEL_WAIT="${SKIP_MODEL_WAIT}" \
        WATCH_INTERVAL_S="${WATCH_INTERVAL_S}" \
        PROBE_TIMEOUT_S="${PROBE_TIMEOUT_S}" \
        bash "${ACE_WATCHER_SCRIPT}"
      append_manifest "${model}" "ace_online" "all" "${ACE_AGENT}" "${run_prefix}" "independent_run"
    done
  done
}

run_cvebench_baselines() {
  banner "Stage 4: CVEBench baselines"
  local model slug
  IFS=',' read -ra _models <<< "${MODELS}"
  for model in "${_models[@]}"; do
    slug="$(normalize_namespace_part "${model}")"
    run_batch_stage "${model}" "cvebench" "cvebench" "cy_agent" "${slug}_pass3_cvebench_zero_day_cy_agent" 8540 "zero_day"
    run_batch_stage "${model}" "cvebench" "cvebench" "t_agent" "${slug}_pass3_cvebench_zero_day_t_agent" 8550 "zero_day"
    run_batch_stage "${model}" "cvebench" "cvebench" "cy_agent" "${slug}_pass3_cvebench_one_day_cy_agent" 8560 "one_day"
    run_batch_stage "${model}" "cvebench" "cvebench" "t_agent" "${slug}_pass3_cvebench_one_day_t_agent" 8570 "one_day"
  done
}

printf 'model\tstage\tbenchmark\tagent\trun_id\tdetails\n' > "${MANIFEST_PATH}"
log "Campaign dir: ${CAMPAIGN_DIR}"
log "Models: ${MODELS}"
log "RUN_STAGES=${RUN_STAGES} MAX_WORKERS=${MAX_WORKERS} BASELINE_SAMPLES=${BASELINE_SAMPLES} ACE_ONLINE_RUNS=${ACE_ONLINE_RUNS} FORCE_DISABLE_THINKING=${FORCE_DISABLE_THINKING}"

if should_run_stage "autopenbench"; then
  run_autopenbench_baselines
fi
if should_run_stage "nyuctfbench"; then
  run_nyuctfbench_baselines
fi
if should_run_stage "ace_online"; then
  run_ace_online_pass3
fi
if should_run_stage "cvebench"; then
  run_cvebench_baselines
fi

banner "Completed"
log "Manifest: ${MANIFEST_PATH}"
