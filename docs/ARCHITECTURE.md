# cybersec_arena — Architecture

This repository is organised into **5 top-level segments**. Each segment is a self-contained directory that owns its code, configs, scripts, and seeds. Only files genuinely shared by every segment live in `common/`.

## Segment overview

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

Direction of dependency: `(2)(3) → (1) → (5)` and `(4) → (5)`. No cycles.

## Top-level layout

```
cybersec_arena/
├── mini_cyberagent/           # (1) agent framework (was `agent/`)
│   ├── agent.py
│   ├── command.py
│   ├── skill.py
│   ├── benchmark_scorers.py   # local scorer registry (mirrors gen0_root/skill_based)
│   ├── commands/
│   ├── skills/
│   └── configs/               # mini_ctf*, mini_live_ctf_v0..v4, raw_ctf, autopenbench, engima, example
│
├── cyber_evolver/             # (2) evolution-driven self-improvement
│   ├── evolve/                # was `evolve/`
│   ├── gen0_root/             # was `gen0_root/`  (skill_based + prompt_based seeds)
│   ├── configs/
│   │   ├── evolve.yaml
│   │   └── self_improve.yaml
│   └── scripts/
│       ├── run_evolve.bash
│       ├── parse_evo_logs.py
│       ├── parse_raw_logs.py
│       └── plot_evo_vs_raw.py
│
├── baseline/                  # (3) baseline reproductions
│   ├── agents/                # cy_agent, dcipher, nyuctf_single, ace_*, reasoningbank_agent, t_agent, vulnbot, autopenbench, upstream_runner
│   ├── batch/                 # run_batch_baseline.py, ace_curator, worker
│   ├── runners/
│   ├── runtime_policy.py
│   ├── readme.md
│   ├── plans/                 # baseline integration plan docs
│   ├── research_context/      # tasks, dag, instructions
│   ├── upstreams/             # external baselines (mostly submodules)
│   │   ├── HGM/, ace/, dgm/, discover/, reflexion/  (submodules)
│   │   ├── ReasoningBank/                             (submodule, vendor mods carried as worktree)
│   │   ├── t_agent/HPTSA/                            (submodule)
│   │   ├── vulnbot/VulnBot/                          (submodule)
│   │   ├── autopenbench_autonomous/auto-pen-bench/   (regular dir, locally modified)
│   │   ├── nyuctf_baseline/nyuctf_agents/            (regular dir, locally modified)
│   │   ├── nyuctf_dcipher/nyuctf_agents/             (regular dir, locally modified)
│   │   ├── cy_agent/                                 (wrapper STATUS.md only; cybench data gitignored)
│   │   └── HGM_cyber/                                (regular wrapper code; output_hgm_cyber/ gitignored)
│   ├── configs/               # was `configs/baseline/*`
│   └── scripts/               # run_*_baseline, run_*_ours, run_hgm_*, run_pass3_*, summarize_pass3, watch_*, audit_batch_results, analyze_dcipher_rounds, task_scheduler, test_reasoningbank_memory
│
├── bench_hub/                 # (4) benchmark catalog + runtime control plane
│   ├── server/                # was `challenge_server/` (FastAPI dispatcher; bench_hub/server shim deleted)
│   ├── benchmarks/            # JSON specs + prompt_profiles + management scripts
│   │                          # (autopenbench/ cvebench/ cybench/ intercode_ctf/ nyu_ctf/ xbow-benchmark/ data dirs are gitignored)
│   ├── adapters/              # was `benchmark_adapters/`
│   ├── runtime/               # was `envs/` (DockerEnvironment, ChallengeClient)
│   └── scripts/               # run_autopenbench, run_cvebench, run_nyuctfbench, benchmark_*, pull_benchmark_base_images, cp_docker_img
│
├── common/                    # (5) genuinely shared
│   ├── utils/                 # was `utils/` (llm_dispatcher, prompt_profiles, runtime_policy, safe_logging, worker_diagnostics, util, ...)
│   ├── configs/
│   │   └── model.yml          # LLM model registry, used by every entrypoint
│   └── scripts/
│       ├── lib/challenge_run_helpers.sh   # shared bash helper sourced by run_*.bash across segments
│       ├── help.bash
│       ├── help_run.bash
│       ├── find_dir.py
│       ├── setup_remote.bash
│       ├── run_tmux_batch.sh
│       ├── wait_pids_then_start.sh
│       └── llm_load_test.py
│
├── tests/                     # flat, repo-rooted (Python convention)
├── docs/                      # plan docs + ARCHITECTURE.md (this file)
│
├── run_batch.py               # cross-segment entrypoint
├── run_evolve_batch_skill.py  # cross-segment entrypoint
├── run_single_debug.py        # cross-segment entrypoint
│
├── paper_writing/scripts/     # plot_intro_figure.py, plot_main_results.py
│   └── (rest of paper_writing/ is gitignored)
│
└── (gitignored) logs/, reports/, figure/, .omc/, baseline/upstreams/cy_agent/cybench/, ...
```

## Module name mapping (vs predecessor `evolve_ctf_agent/`)

| Old top-level module / dir       | New module / dir                          |
|----------------------------------|-------------------------------------------|
| `agent/`                         | `mini_cyberagent/`                        |
| `evolve/`                        | `cyber_evolver/evolve/`                   |
| `gen0_root/`                     | `cyber_evolver/gen0_root/`                |
| `utils/`                         | `common/utils/`                           |
| `challenge_server/`                    | `bench_hub/server/`                       |
| `bench_hub/server/` (shim)              | (deleted)                                 |
| `benchmark_adapters/`            | `bench_hub/adapters/`                     |
| `envs/`                          | `bench_hub/runtime/`                      |
| `benchmarks/`                    | `bench_hub/benchmarks/`                   |
| `configs/model.yml`              | `common/configs/model.yml`                |
| `configs/baseline/*`             | `baseline/configs/*`                      |
| `configs/evolve.yaml`            | `cyber_evolver/configs/evolve.yaml`       |
| `configs/self_improve.yaml`      | `cyber_evolver/configs/self_improve.yaml` |
| `configs/{mini_ctf*,mini_live_ctf*,raw_ctf,autopenbench,engima,example}.yaml` | `mini_cyberagent/configs/*.yaml` |
| `scripts/run_evolve.bash`        | `cyber_evolver/scripts/run_evolve.bash`   |
| `scripts/run_(autopenbench\|cvebench\|nyuctfbench).bash` | `bench_hub/scripts/`              |
| `scripts/run_*_(baseline\|ours).bash` | `baseline/scripts/`                  |
| `scripts/help.bash` etc.         | `common/scripts/`                         |
| `scripts/plot_*.py` (paper)      | `paper_writing/scripts/`                  |

## Excluded data (gitignored, not migrated)

| Path | Reason |
|---|---|
| `logs/`, `reports/`, `figure/`, `good_cases/`, `references/`, `evolution_data/` | runtime artefacts |
| `paper_writing/{1_intro_outlines,references}/` | paper drafting artefacts |
| `bench_hub/benchmarks/{autopenbench,cvebench,cybench,intercode_ctf,nyu_ctf,xbow-benchmark}/` | large benchmark fixture trees (~3.2 GB); place at runtime |
| `baseline/upstreams/cy_agent/cybench/`, `cybench.{partial,tmpclone,incomplete2}/` | clone leftovers + 3.9 GB benchmark data |
| `baseline/upstreams/HGM_cyber/output_hgm_cyber/` | run outputs (1.4 GB) |
| `baseline/upstreams/*/.venv/` | virtual environments |
| `.omc/` | Claude Code orchestration state |
| `__pycache__/`, `.pytest_cache/`, `*.pyc` | Python caches |
| `traj/`, `benchmark_runtime_images.txt`, `configs/.llm_proxy_mapping.json` | per-run artefacts / sensitive |
