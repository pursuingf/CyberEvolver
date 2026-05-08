# AutoPenBench Benchmark Layout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move AutoPenBench onto the repo-local benchmark index workflow so `challenge_server` and `test_challenge_server.py` can discover and launch it like the existing benchmarks, with runtime compose files written into the selected challenge directory.

**Architecture:** Generate challenge-json-compatible AutoPenBench metadata under `benchmark/`, make the server resolve `benchmark/` as the primary benchmark root, and adjust compose launching so challenge-local runtime files can reference the full AutoPenBench compose stack safely. Keep runtime semantics single-target only.

**Tech Stack:** Python, unittest, Docker Compose metadata generation, FastAPI server runtime

---

### Task 1: Capture benchmark-root and index-file expectations in tests

**Files:**
- Modify: `tests/test_challenge_server_registry.py`
- Modify: `tests/test_challenge_client_registry.py`
- Modify: `bench_hub/server/test_challenge_server.py`

**Step 1: Write the failing test**

Add tests that assert:

- the server can load challenges from a repo-local `benchmark/` root
- `test_challenge_server.py` enumerates all challenge IDs from `autopenbench.json` when that index file is listed

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_challenge_server_registry tests.test_challenge_client_registry -v`
Expected: FAIL because current code still assumes `benchmarks/` and lacks AutoPenBench index-file coverage.

**Step 3: Write minimal implementation**

Update benchmark root resolution and the `INDEX_FILES`-based loader to cover AutoPenBench through the repo-local index root.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_challenge_server_registry tests.test_challenge_client_registry -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_challenge_server_registry.py tests/test_challenge_client_registry.py bench_hub/server/test_challenge_server.py bench_hub/server/challenge_server.py common/agent_runtime/challenge_client.py
git commit -m "feat(benchmark): prefer repo-local benchmark root"
```

### Task 2: Add failing tests for AutoPenBench metadata generation

**Files:**
- Create: `benchmark_adapters/autopenbench_layout.py`
- Modify: `tests/test_autopenbench_adapter.py`
- Create: `tests/test_autopenbench_layout.py`

**Step 1: Write the failing test**

Add tests that assert:

- `data/games.json` can be transformed into `benchmark/autopenbench.json`
- each VM directory receives a generated `challenge.json`
- generated metadata preserves AutoPenBench-native fields and framework-compatible launch metadata

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_autopenbench_adapter tests.test_autopenbench_layout -v`
Expected: FAIL because no generator exists yet.

**Step 3: Write minimal implementation**

Implement a small generator module that reads `games.json`, derives challenge IDs, emits challenge metadata, and builds the top-level index.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_autopenbench_adapter tests.test_autopenbench_layout -v`
Expected: PASS

**Step 5: Commit**

```bash
git add benchmark_adapters/autopenbench_layout.py tests/test_autopenbench_adapter.py tests/test_autopenbench_layout.py
git commit -m "feat(autopenbench): generate repo-local benchmark metadata"
```

### Task 3: Route launch runtime through challenge-local compose files

**Files:**
- Modify: `benchmark_adapters/challenge_json.py`
- Modify: `bench_hub/server/challenge_server.py`
- Modify: `bench_hub/server/launch_runtime.py`
- Modify: `tests/test_challenge_server_registry.py`
- Modify: `tests/test_launch_runtime.py`

**Step 1: Write the failing test**

Add tests that assert:

- AutoPenBench launch specs can reference both base and category compose files from a challenge directory
- `docker-compose.runtime.<namespace>.yml` is written under the selected `vmX` directory

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_launch_runtime tests.test_challenge_server_registry -v`
Expected: FAIL because runtime compose currently follows `working_directory` too directly and does not encode challenge-local compose intent.

**Step 3: Write minimal implementation**

Extend challenge metadata and launch-spec handling so compose files can be resolved relative to the challenge directory while runtime compose stays local to that directory.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_launch_runtime tests.test_challenge_server_registry -v`
Expected: PASS

**Step 5: Commit**

```bash
git add benchmark_adapters/challenge_json.py bench_hub/server/challenge_server.py bench_hub/server/launch_runtime.py tests/test_challenge_server_registry.py tests/test_launch_runtime.py
git commit -m "feat(runtime): support challenge-local autopenbench compose files"
```

### Task 4: Materialize repo-local AutoPenBench benchmark content

**Files:**
- Create: `benchmark/autopenbench.json`
- Create: `benchmark/autopenbench/.../challenge.json`
- Modify: `.gitignore`

**Step 1: Write the failing test**

Add tests that use the generated benchmark root and confirm `ChallengeJsonAdapter` can discover AutoPenBench through the same repo-local path shape used by the server.

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_benchmark_adapters tests.test_autopenbench_layout -v`
Expected: FAIL until the benchmark root is materialized and tracked.

**Step 3: Write minimal implementation**

Create the tracked benchmark root, keep `benchmark/` available in git, and ensure `benchmarks/` remains only as an optional legacy fallback.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_benchmark_adapters tests.test_autopenbench_layout -v`
Expected: PASS

**Step 5: Commit**

```bash
git add .gitignore benchmark tests/test_benchmark_adapters.py tests/test_autopenbench_layout.py
git commit -m "feat(benchmark): add repo-local autopenbench index"
```

### Task 5: Run verification and smoke tests

**Files:**
- Modify: none if all prior tasks are correct

**Step 1: Run focused verification**

Run:

```bash
python -m unittest tests.test_autopenbench_adapter tests.test_autopenbench_layout tests.test_launch_runtime tests.test_challenge_server_registry tests.test_challenge_client_registry -v
```

Expected: PASS

**Step 2: Run broader regression**

Run:

```bash
python -m unittest tests.test_autopenbench_adapter tests.test_autopenbench_layout tests.test_agent_prompt_profiles tests.test_launch_runtime tests.test_challenge_server_registry tests.test_challenge_client_registry tests.test_target_runtime_recovery tests.test_challenge_server_runtime_locking -v
```

Expected: PASS

**Step 3: Run server smoke**

Run:

```bash
python bench_hub/server/challenge_server.py 127.0.0.1 8000
python bench_hub/server/test_challenge_server.py
```

Expected: AutoPenBench challenges present in the chosen index file are enumerated and exercised through the existing server workflow.

**Step 4: Commit**

```bash
git add -A
git commit -m "feat(autopenbench): align benchmark layout with challenge server workflow"
```
