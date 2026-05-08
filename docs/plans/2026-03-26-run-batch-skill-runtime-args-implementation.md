# Run Batch Skill Runtime Args Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Thread explicit benchmark runtime args from `run_evolve_batch_skill.py` into `ChallengeClient` so CVE Bench can launch and recover with the configured `parallel_mode` while sandbox reuse continues to be governed by `sandbox_policy`.

**Architecture:** Reuse the existing `resolve_benchmark_runtime_args(...)` output as the only configuration source. Filter a tiny `ChallengeClient` subset from that map, pass it during lazy challenge initialization, and seed the worker-local `ChallengeClient` with the same args for recovery.

**Tech Stack:** Python, `unittest`, existing runner/runtime managers

---

### Task 1: Add failing scheduler tests for CTF runtime args

**Files:**
- Modify: `tests/test_run_evolve_batch_skill_scheduler.py`
- Test: `tests/test_run_evolve_batch_skill_scheduler.py`

**Step 1: Write the failing test**

Add a test showing that `fill_available_challenge_slots(...)` passes explicit `parallel_mode` into `ChallengeClient.get_challenge_data(...)` when `benchmark_runtime_args` contains it.

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_evolve_batch_skill_scheduler.RunBatchSkillSchedulerTests.test_fill_available_challenge_slots_passes_ctf_runtime_args -v`

Expected: FAIL because the scheduler does not yet pass `runtime_args`.

**Step 3: Write minimal implementation**

Update the scheduler helper path in `run_evolve_batch_skill.py` so lazy challenge init can resolve and pass the `ChallengeClient` runtime-args subset.

**Step 4: Run test to verify it passes**

Run the same command and confirm PASS.

**Step 5: Commit**

```bash
git add tests/test_run_evolve_batch_skill_scheduler.py run_evolve_batch_skill.py
git commit -m "feat(runtime): pass ctf launch args in scheduler"
```

### Task 2: Preserve runtime args for worker recovery

**Files:**
- Modify: `run_evolve_batch_skill.py`
- Modify: `common/agent_runtime/challenge_client.py`
- Test: `tests/test_run_evolve_batch_skill_scheduler.py`

**Step 1: Write the failing test**

Add a test around `evolve_single_challenge(...)` showing the worker-local `ChallengeClient` receives or remembers the same filtered runtime args that the main scheduler resolved for the challenge.

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_evolve_batch_skill_scheduler.RunBatchSkillSchedulerTests.test_evolve_single_challenge_seeds_ctf_runtime_args_for_recovery -v`

Expected: FAIL because the worker-local manager is currently created without those args.

**Step 3: Write minimal implementation**

Seed the worker-local `ChallengeClient` with the filtered runtime args before wiring `ChallengeRuntimeCoordinator`.

**Step 4: Run test to verify it passes**

Run the same command and confirm PASS.

**Step 5: Commit**

```bash
git add tests/test_run_evolve_batch_skill_scheduler.py run_evolve_batch_skill.py common/agent_runtime/challenge_client.py
git commit -m "feat(runtime): preserve ctf args for recovery"
```

### Task 3: Run focused regression

**Files:**
- Test: `tests/test_run_evolve_batch_skill_scheduler.py`
- Test: `tests/test_challenge_client_registry.py`
- Test: `tests/test_target_runtime_recovery.py`

**Step 1: Run focused regression**

Run:

```bash
python -m unittest \
  tests.test_run_evolve_batch_skill_scheduler \
  tests.test_challenge_client_registry \
  tests.test_target_runtime_recovery \
  -v
```

Expected: all tests pass.

**Step 2: Review diff**

Inspect only the touched runner and manager files to ensure no benchmark source tree changes were swept in.

**Step 3: Commit**

```bash
git add run_evolve_batch_skill.py common/agent_runtime/challenge_client.py tests/test_run_evolve_batch_skill_scheduler.py tests/test_challenge_client_registry.py
git commit -m "feat(runtime): sync runner ctf launch args"
```
