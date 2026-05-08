# Repository Reorganization Plan

> **Status:** All decisions D1–D11 accepted on 2026-05-02. D12 deferred. **No code changes have been made yet.** This document is the source of truth for the rewrite; it must remain in sync with what gets executed in §6.

> **For the executing session.** Read sections in this order: §1 (what is currently uncommitted), §2 (target structure), §5 (decision register — note that D11c has tentative answers but should be confirmed at Phase 7 file-move time), §6 (the 10 phases — execute strictly in order). Each phase produces exactly one commit (Phase 7 and Phase 8 produce ordered sub-commits as documented). Run `pytest tests/` after every phase. If a phase fails, stop and `git revert` rather than patching ad-hoc — the plan is designed so each phase is independently revertable. Before starting Phase 8, run `git tag pre-bench-hub-reorg` as a safety anchor (see Appendix C). The user has chosen to execute this in a fresh checkout; do not assume any work-in-progress branch state.

**Goal:** Decompose the current monolithic `evolve_ctf_agent` working tree into 5 clearly bounded segments where each segment is a **real top-level directory** that owns all of its members (configs, scripts, modules, seeds). The current uncommitted delta (102 commits ahead of origin, ~640 lines of modified Python, ~100 untracked entries totalling >12 GB on disk) is too large to land safely in one pass.

**Tech Stack:** No new dependencies. Pure structural refactor backed by `git mv`, `git rm`, `.gitignore`, `.gitmodules`, and mechanical text replacement of import paths and config-path strings.

**Guiding principle (per user):** *Each segment is a top-level directory, and all of its members — code, configs, scripts, seeds — live as subdirectories of it. Only files that are genuinely shared by every segment are allowed in `common/`.*

---

## 1. Current state (snapshot taken 2026-05-02)

### 1.1 Modified, real code work (must land — Phase 1)
- `baseline/agents/{cy_agent,dcipher,nyuctf_single,upstream_runner}.py`
- `baseline/batch/run_batch_baseline.py`
- `tests/test_{ace_batch_runner_helpers,cy_agent_upstream_adapter,run_script_helpers}.py`
- New: `scripts/run_pass3_campaign.bash`, `scripts/summarize_pass3.py`, `tests/test_pass3_summary.py`, `tests/test_upstream_runner_memory_guards.py`
- New: `docs/plans/*.md` (8 new plan docs, including this one)

### 1.2 Modified gitlinks (mode 160000 — Phase 3)
- `baseline/upstreams/autopenbench_autonomous/auto-pen-bench` — 1 file modified inside
- `baseline/upstreams/nyuctf_baseline/nyuctf_agents` — 3 files modified inside
- `baseline/upstreams/nyuctf_dcipher/nyuctf_agents` — 5 yaml files modified inside
- `baseline/upstreams/vulnbot/VulnBot` — only an untracked `.venv/` inside (ignore)

### 1.3 Large untracked artefacts
| Path | Size | Disposition (decided) |
|---|---|---|
| `baseline/upstreams/cy_agent/.venv/` | 5.7 G | **gitignore** |
| `baseline/upstreams/cy_agent/cybench/` | 3.9 G | **gitignore** (data) |
| `baseline/upstreams/cy_agent/cybench.partial/` | 975 M | **rm -rf** (clone leftover, D3) |
| `baseline/upstreams/cy_agent/cybench.tmpclone/` | 587 M | **rm -rf** (D3) |
| `baseline/upstreams/cy_agent/cybench.incomplete2/` | 975 M | **rm -rf** (D3) |
| `baseline/upstreams/HGM_cyber/output_hgm_cyber/*` (41 new run dirs) | 1.4 G | **stop tracking new outputs** (D2); the previously tracked 910 files stay as-is |
| `baseline/upstreams/{HGM, ace, dgm, discover, reflexion, ReasoningBank}/`, `baseline/upstreams/t_agent/HPTSA/` | 27 M – 346 M each | **convert to git submodules** (D1) |
| `baseline/upstreams/cy_agent/.venv/`, `baseline/upstreams/t_agent/.venv/` | 118 M – 5.7 G | gitignore |
| `.omc/state/checkpoints/*` (49 new), `.omc/sessions/`, `.omc/state/team/`, `.omc/notepad.md` | small but churny | gitignore |
| `reports/`, `figure/`, `paper_writing/`, `good_cases/`, `references/`, `benchmark_runtime_images.txt` | small | gitignore (D11a/b resolved) |

### 1.4 Risk if we just `git add -A`
- Accidentally commits ~10 GB of venvs and incomplete clones.
- Locks in a giant ad-hoc commit that will be impossible to bisect.
- Hides actionable code changes (the 640-line delta in `baseline/`) under noise.
- Leaves `.omc/` state files churning forever.

---

## 2. Final decomposition

### 2.1 Dependency DAG (logical)

```
+---------------------------+        +---------------------------+
| (1) mini_cyberagent       |<-------| (3) baseline              |
|     framework, no children|        |     baseline reproductions|
+--------------+------------+        +--------------+------------+
               ^                                     |
               |                                     v
+--------------+------------+        +---------------------------+
| (2) cyber_evolver         |        | (4) bench_hub             |
|     evolve/ + gen0_root/  |        |     server + benchmarks   |
+--------------+------------+        |     + adapters + runtime  |
               |                     +--------------+------------+
               |                                    |
               +--------------+---------------------+
                              v
                  +---------------------------+
                  | (5) common                |
                  |     utils + shared config |
                  |     no downstream deps    |
                  +---------------------------+
```

Direction: `(2)(3) -> (1) -> (5)`, `(4) -> (5)`. No cycles.

### 2.2 Final directory layout

```
evolve_ctf_agent/
├── mini_cyberagent/                    # was agent/  (D6 = a)
│   ├── agent.py
│   ├── command.py
│   ├── skill.py
│   ├── commands/
│   └── skills/
│
├── cyber_evolver/                      # NEW container (D5)
│   ├── __init__.py
│   ├── evolve/                         # was evolve/
│   ├── gen0_root/                      # was gen0_root/
│   ├── configs/
│   │   ├── evolve.yaml                 # was configs/evolve.yaml
│   │   └── self_improve.yaml           # was configs/self_improve.yaml (TBD: confirm owner)
│   └── scripts/
│       ├── run_evolve.bash             # was scripts/run_evolve.bash
│       ├── parse_evo_logs.py
│       └── plot_evo_vs_raw.py
│
├── baseline/
│   ├── agents/, batch/, runners/
│   ├── upstreams/                      # 7 of these become submodules (D1)
│   ├── configs/                        # was configs/baseline/*
│   │   ├── ace_agent.yaml
│   │   ├── ace_bash_agent.yaml
│   │   ├── cy_agent.yaml
│   │   ├── dcipher.yaml
│   │   ├── nyuctf_single.yaml
│   │   ├── reasoningbank_agent.yaml
│   │   ├── t_agent.yaml
│   │   ├── vulnbot.yaml
│   │   ├── autopenbench.yaml
│   │   └── prompt.yml
│   └── scripts/
│       ├── task_scheduler.py           # already there
│       ├── run_*_baseline.bash, run_*_ours.bash
│       ├── run_hgm_*.bash
│       ├── run_pass3_campaign.bash
│       ├── summarize_pass3.py
│       ├── analyze_dcipher_rounds.py
│       ├── audit_batch_results.py
│       ├── watch_*.bash, watch_*.py
│       └── test_reasoningbank_memory.py
│
├── bench_hub/                          # was challenge_server/  (D4 + D7 = b)
│   ├── server/                         # was challenge_server/  contents (FastAPI dispatcher)
│   ├── benchmarks/                     # was benchmarks/  (JSON specs, prompt_profiles)
│   ├── adapters/                       # was benchmark_adapters/
│   ├── runtime/                        # was envs/  (DockerEnvironment, ChallengeClient)
│   ├── configs/
│   │   ├── mini_ctf.yaml, mini_ctf_budget.yaml
│   │   ├── mini_live_ctf_v{0..4}.yaml
│   │   ├── raw_ctf.yaml
│   │   ├── autopenbench.yaml
│   │   ├── engima.yaml, example.yaml   # TBD: keep or retire
│   └── scripts/
│       ├── run_autopenbench.bash, run_cvebench.bash, run_nyuctfbench.bash
│       ├── benchmark_autopenbench_nmap.py
│       ├── benchmark_base_images.py, benchmark_base_images.txt
│       ├── pull_benchmark_base_images.sh
│       └── cp_docker_img.bash
│
├── common/                             # NEW container (D8 + configs A)
│   ├── utils/                          # was utils/
│   ├── configs/
│   │   └── model.yml                   # was configs/model.yml  (only truly shared file)
│   └── scripts/
│       ├── help.bash, help_run.bash
│       ├── find_dir.py
│       ├── setup_remote.bash
│       ├── run_tmux_batch.sh
│       ├── wait_pids_then_start.sh
│       └── llm_load_test.py
│
├── docs/                               # unchanged
├── tests/                              # unchanged at top level (D9)
│
├── run_batch.py                        # cross-segment entrypoint (D10)
├── run_evolve_batch_skill.py           # cross-segment entrypoint
├── run_single_debug.py                 # cross-segment entrypoint
│
├── paper_writing/                      # gitignored (artefacts)
│   └── (relocate plot_intro_figure.py + plot_main_results.py here)
│
└── (gitignored: logs/, reports/, figure/, __pycache__/, .omc/state/*, ...)
```

### 2.3 Why a 5th `common/` segment exists

User originally proposed 4 segments. Concrete grep shows `utils.*` is consumed by all of (1)(2)(3)(4) and `configs/model.yml` is read by 18 entrypoints/tests across all four. Forcing them into any single segment creates a circular dependency. Hence (5) `common/`.

`common/` holds **only** what every segment shares:
- `utils/` — `llm_dispatcher`, `prompt_profiles`, `runtime_policy`, `safe_logging`, `worker_diagnostics`, `util.py`, etc.
- `configs/model.yml` — LLM credential/endpoint registry; sole truly-shared config.
- `scripts/` — generic operational helpers (`help.bash`, tmux launchers, `llm_load_test.py`).

Everything else lives in the segment that owns it.

---

## 3. Evidence: actual import graph (verified by grep)

| Segment | Concretely imports from |
|---|---|
| (1) `agent/` (post-rename `mini_cyberagent/`) | only stdlib + langchain |
| (2) `evolve/` (post-move `cyber_evolver/evolve/`) | `utils.{llm_dispatcher,prompt_profiles,safe_logging,worker_diagnostics,util}`, langchain |
| (3) `baseline/agents/` | `agent.agent` (i.e. (1)), `gen0_root.skill_based.benchmark_scorers` (reverse-borrow into (2)), upstreams (`nyuctf_*`, `autopenbench`) |
| (4) `challenge_server/` (post-rename+restructure `bench_hub/server/`) | `benchmark_adapters.*`, `bench_hub/server.*` (load-bearing typo shim — see Appendix A), `utils.runtime_policy`, `envs.*` indirectly |
| (5) `utils/` (post-move `common/utils/`) | only stdlib + 3rd-party |

Two notable findings:

- **`gen0_root/` is the evolution seed, not a baseline asset.** Earlier draft of this plan put it under `baseline/`. That was wrong. `run_evolve_batch_skill.py --base_seed_path` defaults to `./gen0_root/skill_based`; multiple 2026-03 design docs describe `gen0_root/skill_based` as evolution's default template source. `baseline/agents/ace_*.py` only imports `benchmark_scorer_registry` from it — that's a reverse-borrow, not ownership. Correct owner: segment (2). See §5 D5.

- **`bench_hub/server/` is not a stale typo, it's a load-bearing `__path__` redirect.** Its `__init__.py` does `__path__ = [.../challenge_server]`, so `from bench_hub/server.X` is the real import path used by 8 call sites. Once `challenge_server/` is renamed and absorbed into `bench_hub/server/`, the shim becomes dead code and must be deleted. See Appendix A.

---

## 4. File-by-file target mapping

### 4.1 Renames and moves

| From | To | Phase |
|---|---|---|
| `agent/` | `mini_cyberagent/` | 5 |
| `evolve/` | `cyber_evolver/evolve/` | 6 |
| `gen0_root/` | `cyber_evolver/gen0_root/` | 6 |
| `utils/` | `common/utils/` | 7 |
| `configs/model.yml` | `common/configs/model.yml` | 7 |
| `configs/evolve.yaml`, `configs/self_improve.yaml` | `cyber_evolver/configs/` | 7 |
| `configs/baseline/*` (10 files) | `baseline/configs/*` | 7 |
| `configs/{mini_ctf,mini_ctf_budget,mini_live_ctf_v0..v4,raw_ctf,autopenbench,engima,example}.yaml` | `mini_cyberagent/configs/` | 7 |
| `challenge_server/` (root, directory) | `bench_hub/server/` | 8 |
| `bench_hub/server/` (root, shim) | **delete** | 8 |
| `benchmarks/` (root) | `bench_hub/benchmarks/` | 8 |
| `benchmark_adapters/` (root) | `bench_hub/adapters/` | 8 |
| `envs/` (root) | `bench_hub/runtime/` | 8 |
| `scripts/run_evolve.bash`, `parse_evo_logs.py`, `plot_evo_vs_raw.py` | `cyber_evolver/scripts/` | 9 |
| `scripts/run_*_baseline.bash`, `run_*_ours.bash`, `run_hgm_*`, `run_pass3_*`, `summarize_pass3.py`, `analyze_dcipher_rounds.py`, `audit_batch_results.py`, `watch_*`, `test_reasoningbank_memory.py` | `baseline/scripts/` | 9 |
| `scripts/run_autopenbench.bash`, `run_cvebench.bash`, `run_nyuctfbench.bash`, `benchmark_autopenbench_nmap.py`, `benchmark_base_images.{py,txt}`, `pull_benchmark_base_images.sh`, `cp_docker_img.bash` | `bench_hub/scripts/` | 9 |
| `scripts/help.bash`, `help_run.bash`, `find_dir.py`, `setup_remote.bash`, `run_tmux_batch.sh`, `wait_pids_then_start.sh`, `llm_load_test.py` | `common/scripts/` | 9 |
| `scripts/plot_intro_figure.py`, `plot_main_results.py` | `paper_writing/scripts/` | 9 |

### 4.2 Stays at repo root

| Path | Reason |
|---|---|
| `run_batch.py`, `run_evolve_batch_skill.py`, `run_single_debug.py` | cross-segment orchestrators (D10) |
| `tests/` | flat layout per Python convention (D9) |
| `docs/` | meta-documentation, segment-agnostic |
| `requirements.txt`, `.gitignore`, `.gitmodules`, etc. | repo-level config |

### 4.3 Cleanup deletions (D3)

```
rm -rf baseline/upstreams/cy_agent/cybench.partial/      # 975 M
rm -rf baseline/upstreams/cy_agent/cybench.tmpclone/     # 587 M
rm -rf baseline/upstreams/cy_agent/cybench.incomplete2/  # 975 M
```

### 4.4 `.gitignore` additions (Phase 0)

```
# venvs and clone artefacts
**/.venv/
**/cybench.partial/
**/cybench.tmpclone/
**/cybench.incomplete*/

# OMC orchestration state
.omc/state/checkpoints/
.omc/state/agent-replay-*.jsonl
.omc/state/team/
.omc/sessions/
.omc/notepad.md
.omc/prd.json
.omc/project-memory.json
.omc/state/idle-notif-cooldown.json
.omc/state/last-tool-error.json
.omc/state/mission-state.json
.omc/state/subagent-tracking.json

# experiment artefacts
logs/
reports/
figure/
paper_writing/
good_cases/
references/
benchmark_runtime_images.txt
configs/.llm_proxy_mapping.json   # contains internal endpoints

# upstream large outputs (D2: stop tracking new outputs)
baseline/upstreams/HGM_cyber/output_hgm_cyber/   # only the new run dirs; previously tracked files remain in git history
baseline/upstreams/cy_agent/cybench/

# python build artefacts
__pycache__/
*.pyc
```

### 4.5 New submodules (D1, Phase 4)

Convert each of the following from a plain directory into a git submodule:

- `baseline/upstreams/HGM/`
- `baseline/upstreams/ace/`
- `baseline/upstreams/dgm/`
- `baseline/upstreams/discover/`
- `baseline/upstreams/reflexion/`
- `baseline/upstreams/ReasoningBank/`
- `baseline/upstreams/t_agent/HPTSA/`

Each requires: a known upstream URL, a pinned commit SHA, an entry in `.gitmodules`, plus the gitlink commit in the parent.

---

## 5. Decision register (final)

| ID | Topic | Status | Decision |
|---|---|---|---|
| D1 | New upstream baselines (HGM, ace, dgm, discover, reflexion, ReasoningBank, t_agent/HPTSA) | resolved 2026-05-02 | convert to git submodules |
| D2 | `output_hgm_cyber/` new 1.4 G outputs | resolved 2026-05-02 | stop tracking new outputs; previously tracked 910 files stay |
| D3 | `cybench.{partial,tmpclone,incomplete2}` (~2.5 G clone leftovers) | resolved 2026-05-02 | `rm -rf` |
| D4 | `bench_hub/server/` typo shim | resolved 2026-05-02 | rename `challenge_server/` and delete shim. See Appendix A |
| D5 | `gen0_root/` ownership | resolved 2026-05-02 | belongs to (2); move to `cyber_evolver/gen0_root/` (it is the evolution seed, not a baseline asset) |
| D6 | `agent/` rename | resolved 2026-05-02 | (a) direct rename to `mini_cyberagent/` — no extra `agent/` container layer |
| D7 | `bench_hub/` physical scope | resolved 2026-05-02 | (b) full physical reorg — `challenge_server/` → `bench_hub/server/`, `benchmarks/` → `bench_hub/benchmarks/`, `benchmark_adapters/` → `bench_hub/adapters/`, `envs/` → `bench_hub/runtime/` |
| D8 | `scripts/` per-segment split | resolved 2026-05-02 | yes, split per §4.1 |
| D9 | `tests/` flat vs split | resolved 2026-05-02 | keep flat at repo root |
| D10 | Top-level entrypoints | resolved 2026-05-02 | `run_batch.py`, `run_evolve_batch_skill.py`, `run_single_debug.py` stay at root |
| D11a | `good_cases/` directory | resolved 2026-05-02 | gitignore (no in-tree retention) |
| D11b | `references/` directory | resolved 2026-05-02 | gitignore (no in-tree retention) |
| D11c | `configs/self_improve.yaml`, `configs/engima.yaml`, `configs/example.yaml` | TBD at Phase 7 | tentative owner = `cyber_evolver/configs/` for `self_improve.yaml`, `mini_cyberagent/configs/` for the other two; confirm at file-move time |
| D12 | Two `runtime_policy.py` files (`utils/` vs `baseline/`) | deferred | flagged for follow-up audit; not touched in this reorg. See §7 |

---

## 6. Migration phases

Each phase is **one commit** (sub-phases noted explicitly) and is independently revertable. Run `pytest tests/` after every phase.

### Phase 0 — Land `.gitignore`
- Update `.gitignore` per §4.4. (`good_cases/` and `references/` are gitignored per D11a/b.)
- Commit: `chore: tighten gitignore, decompose state and artefact paths`
- Verify: `git status` shrinks to genuinely actionable diffs only.

### Phase 1 — Commit the in-flight code work (no structure changes)
- Add §1.1 files (modified `baseline/agents/*`, modified `baseline/batch/*`, new tests, new scripts, new docs).
- Commit: `feat(baseline): pass3 campaign + upstream runner memory guards`
- Verify: targeted `pytest` for the new/modified test files passes.

### Phase 2 — Filesystem cleanup (D3)
- `rm -rf baseline/upstreams/cy_agent/{cybench.partial,cybench.tmpclone,cybench.incomplete2}`
- No commit needed (these were never tracked).

### Phase 3 — Resolve nested upstream gitlink drift (D2 + §1.2)
- For each of `autopenbench_autonomous/auto-pen-bench`, `nyuctf_baseline/nyuctf_agents`, `nyuctf_dcipher/nyuctf_agents`: cd in, decide (commit upstream / revert), update gitlink in parent.
- Commit: `chore(upstreams): pin nyuctf and autopenbench upstream commits`
- Verify: `git ls-files --stage | grep ^160000` shows clean SHAs.

### Phase 4 — Onboard new upstreams as submodules (D1)
- For each of HGM, ace, dgm, discover, reflexion, ReasoningBank, t_agent/HPTSA: confirm upstream URL, run `git submodule add` with a pinned commit, update `.gitmodules`.
- Commit: `chore(upstreams): add HGM/ace/dgm/discover/reflexion/ReasoningBank/HPTSA as submodules`
- Verify: `git submodule status` shows 7 new entries with explicit SHAs.

### Phase 5 — Rename `agent/` → `mini_cyberagent/` (D6)
- `git mv agent mini_cyberagent`
- Replace `from agent.` → `from mini_cyberagent.` and `import agent` → `import mini_cyberagent` everywhere (~10 sites — `run_batch.py`, `run_evolve_batch_skill.py`, `run_single_debug.py`, `baseline/agents/cy_agent.py`, plus tests).
- Sweep: `git grep -n '\bagent\.\(agent\|command\|skill\)\b'` → confirm only legacy paper_writing/ matches remain.
- Commit: `refactor(mini_cyberagent): rename agent/ -> mini_cyberagent/`
- Verify: full `pytest tests/`.

### Phase 6 — Build `cyber_evolver/` container (D5)
- `mkdir cyber_evolver && touch cyber_evolver/__init__.py`
- `git mv evolve cyber_evolver/evolve`
- `git mv gen0_root cyber_evolver/gen0_root`
- Replace imports:
  - `from evolve.X` → `from cyber_evolver.evolve.X` (~15 sites in `run_evolve_batch_skill.py` + tests)
  - `from gen0_root.X` → `from cyber_evolver.gen0_root.X` (4 sites in `baseline/agents/ace_*.py`, tests)
- Replace path strings:
  - `evolve/prompt*.yml` → `cyber_evolver/evolve/prompt*.yml` in default args of `evolve/{loganalyzer,refiner_agent,orchestrator}.py` (now `cyber_evolver/evolve/...`) and tests
  - `./gen0_root/skill_based` → `./cyber_evolver/gen0_root/skill_based` in `run_evolve_batch_skill.py` and `configs/raw_ctf.yaml`
- Sweep: `git grep -nE '(^|[^/.])(evolve|gen0_root)/'` to catch stragglers.
- Commit: `refactor(cyber_evolver): consolidate evolve/ + gen0_root/ under cyber_evolver/`
- Verify: full `pytest tests/`, plus a smoke run of `python run_evolve_batch_skill.py --help`.

### Phase 7 — Build `common/` and distribute `configs/` (configs option A)
Sub-commits, in order:

1. `mkdir -p common/configs common/utils common/scripts && git mv utils/* common/utils/ && rmdir utils && git mv configs/model.yml common/configs/model.yml`
2. Replace `from utils.X` → `from common.utils.X` (project-wide, ~80 sites). Replace string `configs/model.yml` → `common/configs/model.yml` (~18 sites).
3. `mkdir cyber_evolver/configs && git mv configs/evolve.yaml cyber_evolver/configs/`. Update `--config` defaults in `run_evolve_batch_skill.py`, `tests/run_evolve.py`, `tests/test_run_evolve_batch_skill_scheduler.py`. Resolve D11c for `self_improve.yaml`.
4. `mkdir baseline/configs && git mv configs/baseline/* baseline/configs/ && rmdir configs/baseline`. Update `configs/baseline/X` → `baseline/configs/X` strings (~30 sites in `baseline/batch/run_batch_baseline.py`, `tests/test_ace_*.py`).
5. `mkdir bench_hub/configs && git mv configs/{mini_ctf*,mini_live_ctf*,raw_ctf,autopenbench,engima,example}.yaml mini_cyberagent/configs/`. Update `configs/X` → `mini_cyberagent/configs/X` strings (~20 sites in `run_batch.py`, `run_single_debug.py`, `scripts/help.bash`, tests). Resolve D11c for `engima.yaml` and `example.yaml`.
6. `rmdir configs` (verify empty first; `configs/.llm_proxy_mapping.json` is gitignored and remains on disk if present).

- Commit: `refactor(common,configs): introduce common/, distribute configs per segment`
- Verify: full `pytest tests/`. Spot-check: `python run_batch.py --help` and `python run_evolve_batch_skill.py --help`.

### Phase 8 — Build `bench_hub/` and absorb its members (D4 + D7)
Sub-commits, in order:

1. **Rename + drop shim:** `git mv challenge_server bench_hub` then `git rm -r bench_hub/server` (the shim was load-bearing only because the dir was named `challenge_server`; with the rename we will switch all imports to `bench_hub.X` directly).
2. **Reshape internal layout:** `git mv bench_hub bench_hub/server` is illegal in one step; the working approach is:
   - `git mv bench_hub _bh_tmp`
   - `mkdir bench_hub`
   - `git mv _bh_tmp bench_hub/server`
3. `git mv benchmarks bench_hub/benchmarks`
4. `git mv benchmark_adapters bench_hub/adapters`
5. `git mv envs bench_hub/runtime`
6. **Mechanical import + path rewrite** (the largest rewrite in the plan):
   - `from bench_hub/server.X` and `from challenge_server.X` → `from bench_hub.server.X` (the 8 known sites in Appendix A, plus any stragglers)
   - `from benchmark_adapters.X` → `from bench_hub.adapters.X`
   - `from benchmarks.X` (rare) → `from bench_hub.benchmarks.X`
   - `from envs.X` → `from bench_hub.runtime.X` (many sites: `run_batch.py`, `run_evolve_batch_skill.py`, `run_single_debug.py`, `baseline/batch/*`, tests)
   - String paths `benchmarks/<family>.json`, `benchmarks/prompt_profiles/...` → `bench_hub/benchmarks/...`
   - String paths referencing `envs/autopenenv` etc. → `bench_hub/runtime/autopenenv`
7. Sweep: `git grep -nE '\b(challenge_server|bench_hub/server|envs|benchmark_adapters)\b'` until only intentional historical strings remain.
- Commit: `refactor(bench_hub): collapse challenge_server/benchmarks/adapters/envs into bench_hub/`
- Verify: full `pytest tests/`. Smoke test: `python -m bench_hub.server.challenge_server --help` (or whatever the entrypoint becomes).

> **Risk note:** Phase 8 is the largest single rewrite. Preserve a checkpoint by tagging the post-Phase-7 commit (`git tag pre-bench-hub-reorg`) so the entire phase can be reverted with one command if anything explodes.

### Phase 9 — Split `scripts/` per segment (D8)
- `git mv` each file per the table in §4.1 into the right segment's `scripts/`.
- Inside each moved bash script, fix relative paths (most resolve `MODEL_CONFIG="${MODEL_CONFIG:-configs/model.yml}"` → now `common/configs/model.yml`; some resolve config files already moved in Phase 7).
- `git mv scripts/plot_{intro_figure,main_results}.py paper_writing/scripts/` (paper artefact).
- After the moves, `rmdir scripts` only if empty.
- Commit: `refactor(scripts): distribute scripts to owning segments`
- Verify: a sanity run of one bash script per segment that doesn't actually launch a benchmark (e.g. `bash baseline/scripts/help_run.bash --help`-equivalent).

### Phase 10 — Documentation
- Update `README.md` (or add `docs/ARCHITECTURE.md`) to describe the 5-segment structure. The diagram in §2.1 / §2.2 is the canonical version.
- Add a one-sentence header in each segment's directory pointing back to this plan.
- Commit: `docs: describe 5-segment repo layout`

---

## 7. Out of scope

- No behavioural code changes. No refactors inside any segment beyond import-path edits.
- No `tests/` internal reorganisation.
- No resolution of D12 (duplicate `runtime_policy.py`); flagged for separate plan.
- No touching of `.omc/` configuration; only state files are gitignored.
- No changes to upstream submodule contents beyond Phase 3's gitlink updates.

---

## 8. Rollback

Every phase is one commit. If a phase breaks something:

```
git revert <commit>
```

Phase 8 (the bench_hub rewrite) is large enough that it gets a defensive tag at the end of Phase 7 (`git tag pre-bench-hub-reorg`) — recovering is `git reset --hard pre-bench-hub-reorg` if needed.

The Phase 0 `.gitignore` is the only piece that, once landed, hides untracked files from `git status` — but the files themselves are untouched and can be tracked again by reverting the ignore line.

---

## Appendix A. Naming decision: segment (4)

**Decided 2026-05-02.**

### Rejected names and why
| Candidate | Verdict | Reason |
|---|---|---|
| `ctf_runtime` (initial draft) | rejected | "ctf" is too narrow — segment also serves cvebench, autopenbench, cybench, xbow-benchmark, intercode_ctf. "runtime" misnames the function: this segment is a **catalog + dispatcher**, not a runtime. The actual runtime is inside `bench_hub/runtime/` (Docker) and inside the launched containers themselves. |
| `cyber_bench` | rejected | conflicts with the existing benchmark `cybench/` inside `baseline/upstreams/cy_agent/cybench/`. |
| `bench_dispatch` | rejected | accurately captures dispatch but undersells the catalog and adapter responsibilities. |
| `benchsuite` | rejected | implies a runner/harness; doesn't communicate "central control plane". |

### Accepted name: `bench_hub`
- Captures **catalog** (`benchmarks/` inside), **adapters** (`adapters/` inside), **dispatch** (`server/` inside), and **runtime** (`runtime/` inside).
- Fits the existing naming cadence (`mini_cyberagent`, `cyber_evolver`, `baseline`, `bench_hub`, `common`).
- No collision with anything inside `baseline/upstreams/`.

### Why the shim disappears
`bench_hub/server/__init__.py` does `__path__ = [.../challenge_server]`. It exists solely so that `from bench_hub/server.X` resolves to files in `challenge_server/`. Once `challenge_server/` is renamed and absorbed into `bench_hub/server/`, all 8 import sites are rewritten to `from bench_hub.server.X` — the shim has no callers and is deleted in Phase 8 step 1.

### Import sites to rewrite (snapshot 2026-05-02)
```
challenge_server/check_benchmark_health.py:    from bench_hub/server.path_bootstrap import ensure_repo_root_on_sys_path
challenge_server/challenge_server.py:                 from bench_hub/server.path_bootstrap import ensure_repo_root_on_sys_path
challenge_server/challenge_server.py:                 from bench_hub/server.launch_runtime import materialize_compose_runtime, release_reserved_project_local_subnet
challenge_server/challenge_server.py:                 from bench_hub/server.runtime_guards import ChallengeLockRegistry, ChallengeRecoveryCoordinator
challenge_server/test_challenge_server.py:            from bench_hub/server.path_bootstrap import ensure_repo_root_on_sys_path
challenge_server/test_challenge_server.py:            from bench_hub/server import challenge_server
challenge_server/test_launch_runtime_regression.py: (4 imports of bench_hub/server.launch_runtime)
tests/test_challenge_server_registry.py:       from bench_hub/server import challenge_server
tests/test_cvebench_parallel_runtime.py: from bench_hub/server import challenge_server
```
All become `from bench_hub.server.X` after Phase 8.

---

## Appendix B. Configs reorganisation: option A

**Decided 2026-05-02.**

### Considered
| Option | Layout | Verdict |
|---|---|---|
| **A** (chosen) | per-segment `configs/`; `common/configs/model.yml` is the only shared file | matches the "segment owns its members" principle; 70–80 string replacements |
| B | top-level `configs/{common,evolver,baseline,bench_hub}/` | single config root; 40–50 replacements; rejected because it creates a parallel "6th segment" that drifts over time |
| C | hybrid: `configs/model.yml` stays at root, segment-specific configs move into segment | 50 replacements; rejected because the residual `configs/` weakens the principle |

### Why A
- The principle "each segment is a real directory and owns its members" applies to configs too.
- Only `model.yml` is genuinely shared (read by 18 sites across all 4 functional segments). The remaining 24 files in `configs/` each have a single segment as the dominant consumer.
- Option B leaves a dangling `configs/` tree at root — over time, future configs go there by inertia, and the segmentation rots.
- The string-replacement cost (mostly in entrypoints, scripts, and tests) is mechanical and grep-verifiable.

### Open sub-decisions (D11c)
At Phase 7 file-move time, confirm the following with the user:
- `configs/self_improve.yaml` — tentative owner: `cyber_evolver/configs/`
- `configs/engima.yaml`, `configs/example.yaml` — tentative owner: `mini_cyberagent/configs/` if still in use, otherwise retire

---

## Appendix C. `bench_hub/` physical layout: option (b)

**Decided 2026-05-02.**

### Why full restructure (b) instead of name-only rename (a)
- The user's principle is uniform: each segment is a directory and owns its members. Leaving `benchmarks/`, `benchmark_adapters/`, `envs/` at the repo root would create the same "ambiguous ownership" anti-pattern that motivated this whole reorganisation.
- Mechanical cost is large but bounded: ~100 import sites and an unknown number of path strings, all grep-locatable. Tagging `pre-bench-hub-reorg` before Phase 8 makes a single-command rollback available.
- After Phase 8, `bench_hub/` reads as a self-contained package: `from bench_hub.runtime.docker_env import DockerEnvironment`, `from bench_hub.adapters.autopenbench import ...`, `from bench_hub.server.challenge_server import app`. Each prefix tells the reader which sub-responsibility the import touches.

### Sub-segment naming choices
| Old | New | Reason |
|---|---|---|
| `challenge_server/` | `bench_hub/server/` | "server" captures the FastAPI dispatcher role |
| `benchmarks/` | `bench_hub/benchmarks/` | unchanged name, just relocated |
| `benchmark_adapters/` | `bench_hub/adapters/` | shortened (the `bench_hub.` prefix already disambiguates) |
| `envs/` | `bench_hub/runtime/` | "envs" is overloaded (looked like environment vars) — `runtime` matches what the module actually does (DockerEnvironment, ChallengeClient) |

### Tag for safety
At the end of Phase 7, before starting Phase 8: `git tag pre-bench-hub-reorg`. If Phase 8 turns out to be infeasible, `git reset --hard pre-bench-hub-reorg` reverts to a structurally clean checkpoint.

---

## Appendix D. Things to clean up later (deferred)

- **D12.** `utils/runtime_policy.py` (~72 lines, generic scope resolution) and `baseline/runtime_policy.py` (commented "Runtime isolation policy shared by baseline runners") are both small but functionally adjacent. They should be audited together — likely merged into `common/utils/runtime_policy.py` with a thin baseline-specific helper, but that's a behaviour-level decision and is intentionally out of scope here.
- `baseline/upstreams/cy_agent/cybench/` (3.9 G of benchmark data) is gitignored in this plan but remains on disk. Confirm whether it should be a submodule pointing at the Cybench data repo.
- `paper_writing/scripts/plot_*.py` may eventually want a proper `paper_writing/figures/` substructure; out of scope for this reorg.
