# CVE Bench Benchmark Layout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Integrate CVE Bench into the repo-local benchmark workflow so `challenge_server`, `ChallengeClient`, and `test_challenge_server.py` can discover and launch CVE Bench challenges the same way they already handle the generated AutoPenBench benchmark.

**Architecture:** Add a CVE Bench layout generator that maps upstream `src/critical/challenges`, `metadata`, and prompt variants into repo-local `benchmarks/cvebench.json` plus per-challenge `challenge.json`. Extend challenge-json launch handling so CVE Bench compose stacks, prompt variants, and exposed services can be launched through the existing runtime flow with challenge-local runtime compose files.

**Tech Stack:** Python, unittest, YAML metadata generation, Docker Compose runtime materialization, FastAPI server runtime

---

### Task 1: Add failing tests for CVE Bench layout generation and discovery

**Files:**
- Create: `tests/fixtures/cvebench_minimal/src/critical/challenges/CVE-2024-0001/compose.yml`
- Create: `tests/fixtures/cvebench_minimal/src/critical/challenges/CVE-2024-0001/eval.yml`
- Create: `tests/fixtures/cvebench_minimal/src/critical/challenges/CVE-2024-0001/.env`
- Create: `tests/fixtures/cvebench_minimal/src/critical/metadata/CVE-2024-0001.yml`
- Create: `tests/test_cvebench_layout.py`
- Modify: `tests/test_benchmark_adapters.py`

**Step 1: Write the failing test**

Add a minimal CVE Bench fixture and tests that assert:

- the fixture can be transformed into `benchmarks/cvebench.json`
- a generated challenge directory contains `challenge.json`
- the generated metadata preserves `benchmark_family`, `task_profile`, `default_variant`, and both prompt variants
- `ChallengeJsonAdapter` can discover the generated CVE Bench challenge through the repo-local index

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_cvebench_layout tests.test_benchmark_adapters -v`
Expected: FAIL because no CVE Bench layout generator exists yet.

**Step 3: Write minimal implementation**

Create the CVE Bench layout generator and any helper parsing needed to turn the upstream fixture into challenge-json-compatible metadata.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_cvebench_layout tests.test_benchmark_adapters -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/fixtures/cvebench_minimal tests/test_cvebench_layout.py tests/test_benchmark_adapters.py benchmark_adapters/cvebench_layout.py
git commit -m "feat(cvebench): generate benchmark metadata"
```

### Task 2: Add failing tests for CVE Bench compose launch semantics

**Files:**
- Modify: `tests/test_launch_runtime.py`
- Modify: `tests/test_challenge_server_registry.py`
- Modify: `tests/test_challenge_client_registry.py`
- Modify: `benchmark_adapters/challenge_json.py`
- Modify: `bench_hub/server/launch_runtime.py`
- Modify: `bench_hub/server/challenge_server.py`
- Modify: `common/agent_runtime/challenge_client.py`

**Step 1: Write the failing test**

Add tests that assert:

- a generated CVE Bench `challenge.json` produces a compose launch spec
- runtime compose is written inside the challenge directory as `docker-compose.runtime.<namespace>.yml`
- launch metadata exposes the application endpoint and preserves evaluator-facing services like the upload port when present
- both the server and manager registries can discover CVE Bench from repo-local `benchmark/`

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_launch_runtime tests.test_challenge_server_registry tests.test_challenge_client_registry -v`
Expected: FAIL because current challenge-json runtime handling does not yet understand CVE Bench compose metadata and port exposure rules.

**Step 3: Write minimal implementation**

Extend challenge-json launch handling to support CVE Bench fields such as:

- `compose_files`
- `compose_target_services`
- `compose_dependency_services`
- `exposure_mode`
- prompt-driven application target metadata

Resolve compose paths so CVE Bench `include`, `extends`, and environment-backed paths can be launched from the repo-local challenge directory.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_launch_runtime tests.test_challenge_server_registry tests.test_challenge_client_registry -v`
Expected: PASS

**Step 5: Commit**

```bash
git add benchmark_adapters/challenge_json.py bench_hub/server/launch_runtime.py bench_hub/server/challenge_server.py common/agent_runtime/challenge_client.py tests/test_launch_runtime.py tests/test_challenge_server_registry.py tests/test_challenge_client_registry.py
git commit -m "feat(runtime): launch cvebench challenges"
```

### Task 3: Add failing tests for prompt variant persistence and rendering

**Files:**
- Modify: `tests/test_agent_prompt_profiles.py`
- Modify: `gen0_root/skill_based/system_template.txt`
- Modify: `gen0_root/skill_based/instance_template.txt`
- Modify: `gen0_root/skill_based/agent.py`

**Step 1: Write the failing test**

Add tests that assert a CVE Bench challenge with `task_profile == pentest_remote`:

- defaults to the `zero_day` prompt variant
- keeps `one_day` available in metadata
- renders the actual reachable host and port in the instance prompt
- keeps benchmark-specific details out of the system prompt when they belong in the instance prompt

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_agent_prompt_profiles -v`
Expected: FAIL because current prompt assembly does not yet read CVE Bench prompt variant fields.

**Step 3: Write minimal implementation**

Update prompt assembly so `pentest_remote` can consume CVE Bench prompt fields from `challenge.json`, default to `zero_day`, and continue rendering target reachability the same way AutoPenBench does.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_agent_prompt_profiles -v`
Expected: PASS

**Step 5: Commit**

```bash
git add gen0_root/skill_based/system_template.txt gen0_root/skill_based/instance_template.txt gen0_root/skill_based/agent.py tests/test_agent_prompt_profiles.py
git commit -m "feat(prompt): render cvebench variants"
```

### Task 4: Materialize repo-local CVE Bench benchmark content

**Files:**
- Create: `benchmarks/cvebench.json`
- Create: `benchmarks/cvebench/*/challenge.json`
- Modify: `.gitignore`

**Step 1: Write the failing test**

Add or extend tests so the tracked repo-local benchmark tree is treated as a normal source root and runtime compose files under `benchmarks/cvebench/` remain ignored.

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_cvebench_layout tests.test_challenge_server_script -v`
Expected: FAIL until the repo-local benchmark layout is generated and ignore rules match the new runtime files.

**Step 3: Write minimal implementation**

Generate the real CVE Bench index and challenge metadata under `benchmark/`, and update `.gitignore` if needed so runtime compose files are ignored while tracked benchmark metadata remains visible to git.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_cvebench_layout tests.test_challenge_server_script -v`
Expected: PASS

**Step 5: Commit**

```bash
git add .gitignore benchmarks/cvebench.json benchmarks/cvebench
git commit -m "feat(benchmark): add cvebench index"
```

### Task 5: Run focused verification and smoke checks

**Files:**
- Modify: none if all prior tasks are correct

**Step 1: Run focused regression**

Run:

```bash
python -m unittest tests.test_cvebench_layout tests.test_benchmark_adapters tests.test_launch_runtime tests.test_challenge_server_registry tests.test_challenge_client_registry tests.test_agent_prompt_profiles tests.test_challenge_server_script -v
```

Expected: PASS

**Step 2: Run broader regression**

Run:

```bash
python -m unittest tests.test_autopenbench_adapter tests.test_autopenbench_layout tests.test_cvebench_layout tests.test_benchmark_adapters tests.test_agent_prompt_profiles tests.test_launch_runtime tests.test_challenge_server_registry tests.test_challenge_client_registry tests.test_challenge_server_script tests.test_target_runtime_recovery tests.test_challenge_server_runtime_locking -v
```

Expected: PASS

**Step 3: Run server smoke**

Run:

```bash
python bench_hub/server/challenge_server.py
python bench_hub/server/test_challenge_server.py
```

Expected: CVE Bench challenges discovered from `benchmarks/cvebench.json` are launched through the normal index-file workflow, with runtime compose files created inside their challenge directories.

**Step 4: Commit**

```bash
git add -A
git commit -m "feat(cvebench): integrate benchmark layout"
```
