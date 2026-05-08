#!/usr/bin/env bash
#
# HGM Cyber on NYU-CTF, per-category evolution.
# Runs evolution + final-eval separately for each category in CATS.
# Designed for crypto / rev / pwn (the three "single-skill" categories).
#
# Usage:
#   bash baseline/scripts/run_hgm_nyuctf_per_category.bash
#
#   # Run only one category
#   CATS=pwn bash baseline/scripts/run_hgm_nyuctf_per_category.bash
#
#   # Skip categories whose evolve dir already exists (resume across categories)
#   SKIP_EXISTING=1 bash baseline/scripts/run_hgm_nyuctf_per_category.bash
#
#   # Customize budget per category
#   MAX_TASK_EVALS=150 MAX_WORKERS=8 STEP_LIMIT=30 \
#     bash baseline/scripts/run_hgm_nyuctf_per_category.bash
#
#   # Dry run (no actual work)
#   DRY_RUN=1 bash baseline/scripts/run_hgm_nyuctf_per_category.bash
#
# Note: nyu_ctf uses 'rev' (not 'reverse') as the category name.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ── Categories to run (comma-separated) ──
CATS="${CATS:-crypto,rev,pwn}"

# ── Common HGM params (forwarded to run_hgm_cyber.bash) ──
export MODEL="${MODEL:-Kimi-K2.5-sii}"
export BENCHMARK="${BENCHMARK:-nyu_ctf}"
export MAX_TASK_EVALS="${MAX_TASK_EVALS:-600}"
export MAX_WORKERS="${MAX_WORKERS:-24}"
export STEP_LIMIT="${STEP_LIMIT:-30}"
export ALPHA="${ALPHA:-0.6}"
export EVAL_TIMEOUT="${EVAL_TIMEOUT:-1500}"
export SELF_IMPROVE_TIMEOUT="${SELF_IMPROVE_TIMEOUT:-1800}"
export PASS_N="${PASS_N:-1}"
export TOP_K="${TOP_K:-1}"
export EVAL_WORKERS="${EVAL_WORKERS:-${MAX_WORKERS}}"

SKIP_EXISTING="${SKIP_EXISTING:-0}"
DRY_RUN="${DRY_RUN:-0}"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { printf '[%s] %s\n' "$(ts)" "$*"; }
banner() { printf '\n========================================\n%s\n========================================\n' "$*"; }

slugify() {
  local s="${1,,}"
  s="${s//[^a-z0-9]/_}"
  printf '%s' "$s" | sed -E 's/_+/_/g; s/^_+//; s/_+$//'
}

find_existing_dir() {
  # Find the most-recent evolve dir matching MODEL__BENCHMARK__cat
  local model_slug bench_slug cat_slug
  model_slug="$(slugify "${MODEL}")"
  bench_slug="$(slugify "${BENCHMARK}")"
  cat_slug="$(slugify "$1")"
  local pattern="${REPO_ROOT}/baseline/upstreams/HGM_cyber/output_hgm_cyber/${MODEL//\//_}__${BENCHMARK}__${cat_slug}__*"
  ls -td ${pattern} 2>/dev/null | head -1
}

run_one_category() {
  local cat="$1"
  banner "Category: ${cat}"

  local existing
  existing="$(find_existing_dir "${cat}")"

  if [[ "${SKIP_EXISTING}" == "1" && -n "${existing}" ]]; then
    log "Existing evolve dir found and SKIP_EXISTING=1: ${existing}"
    log "Skipping category=${cat}"
    return 0
  fi

  if [[ -n "${existing}" ]]; then
    log "Note: existing dir found at ${existing} (will create a NEW one)"
  fi

  CATEGORIES="${cat}" \
    bash "${SCRIPT_DIR}/run_hgm_cyber.bash"
}

# ─── Main ───
banner "HGM Cyber Per-Category Evolution"
log "Model:       ${MODEL}"
log "Benchmark:   ${BENCHMARK}"
log "Categories:  ${CATS}"
log "Budget:      max_evals=${MAX_TASK_EVALS}  workers=${MAX_WORKERS}  step=${STEP_LIMIT}  alpha=${ALPHA}"
log "Eval:        pass@${PASS_N}  top_k=${TOP_K}"
[[ "${DRY_RUN}" == "1" ]] && log "DRY_RUN mode: no actual work"
[[ "${SKIP_EXISTING}" == "1" ]] && log "SKIP_EXISTING=1: will skip categories with existing output dirs"

IFS=',' read -ra CAT_ARR <<<"${CATS}"
declare -a SUCCESS=()
declare -a FAILED=()

for cat in "${CAT_ARR[@]}"; do
  cat="$(echo "${cat}" | xargs)"  # trim whitespace
  [[ -z "${cat}" ]] && continue

  log ""
  if DRY_RUN="${DRY_RUN}" run_one_category "${cat}"; then
    SUCCESS+=("${cat}")
  else
    code=$?
    log "Category ${cat} FAILED (exit=${code})"
    FAILED+=("${cat}")
    # Continue with next category — don't abort the whole run
  fi
done

banner "Per-Category Run Complete"
log "Success: ${SUCCESS[*]:-none}"
log "Failed:  ${FAILED[*]:-none}"

# Print result paths
log ""
log "Result directories:"
for cat in "${SUCCESS[@]}"; do
  d="$(find_existing_dir "${cat}")"
  [[ -n "${d}" ]] && log "  [${cat}] ${d}"
done

# Exit non-zero if any failed
if [[ "${#FAILED[@]}" -gt 0 ]]; then
  exit 1
fi
