# CVE Bench Parallel Runtime Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add CVE Bench parallel runtime support using run-isolated Compose projects and exclusive sandboxes without rewriting benchmark metadata semantics.

**Architecture:** CVE Bench launch orchestration will shift from a challenge-ID singleton model to a run-oriented model. `challenge_server` will allocate isolated Compose projects and expose per-run network facts, `GlobalDockerManager` will own sandbox allocation policy, and runner/config args will bind benchmark-level runtime strategy without polluting `challenge.json`.

**Tech Stack:** Python, FastAPI, Docker Compose, existing `DockerEnvironment`, existing `ChallengeClient`, unittest/pytest-style regression tests

---

### Task 1: Add regression tests for run-oriented launch state

**Files:**
- Modify: `tests/test_challenge_server_registry.py`
- Modify: `tests/test_challenge_server_runtime_locking.py`
- Create: `tests/test_cvebench_parallel_runtime.py`

**Step 1: Write the failing test for multiple runs of one challenge**

```python
def test_cvebench_launch_returns_distinct_run_ids_for_same_challenge():
    first = launch("cvb-CVE-2024-4323")
    second = launch("cvb-CVE-2024-4323")
    assert first["run_id"] != second["run_id"]
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_cvebench_parallel_runtime -v`
Expected: FAIL because launch state is still keyed by `chal_id`

**Step 3: Add failing assertions for run-scoped cleanup**

```python
def test_stop_targets_run_id_not_global_challenge_slot():
    first = launch("cvb-CVE-2024-4323")
    second = launch("cvb-CVE-2024-4323")
    stop(first["run_id"])
    assert is_running(second["run_id"])
```

**Step 4: Run tests to verify they fail**

Run: `python -m unittest tests.test_cvebench_parallel_runtime -v`
Expected: FAIL because stop/recovery logic still assumes one instance per challenge

**Step 5: Commit**

```bash
git add tests/test_challenge_server_registry.py tests/test_challenge_server_runtime_locking.py tests/test_cvebench_parallel_runtime.py
git commit -m "test(cvebench): cover run-scoped launch state"
```

### Task 2: Make `challenge_server` run-oriented for CVE Bench

**Files:**
- Modify: `bench_hub/server/challenge_server.py`
- Modify: `bench_hub/server/runtime_guards.py`
- Modify: `bench_hub/server/launch_runtime.py`
- Test: `tests/test_cvebench_parallel_runtime.py`

**Step 1: Write the failing test for isolated network naming**

```python
def test_cvebench_launch_returns_run_specific_network_name():
    payload = launch("cvb-CVE-2024-4323")
    assert payload["agent_network_name"].startswith(payload["project_name"])
```

**Step 2: Run the focused test**

Run: `python -m unittest tests.test_cvebench_parallel_runtime::test_cvebench_launch_returns_run_specific_network_name -v`
Expected: FAIL because the server still returns shared `ctfnet_default`

**Step 3: Implement minimal server changes**

Implementation targets:
- generate `run_id` per launch
- key `running_instances` by `run_id`
- keep challenge-to-run lookup only if needed for compatibility
- return `run_id`, `project_name`, `agent_network_name`, and per-run network facts
- ensure stop/cleanup accepts `run_id`

**Step 4: Run the focused test again**

Run: `python -m unittest tests.test_cvebench_parallel_runtime -v`
Expected: PASS for run identity and network assertions

**Step 5: Commit**

```bash
git add bench_hub/server/challenge_server.py bench_hub/server/runtime_guards.py bench_hub/server/launch_runtime.py tests/test_cvebench_parallel_runtime.py
git commit -m "feat(cvebench): make launches run-oriented"
```

### Task 3: Preserve Compose-local network semantics for CVE Bench

**Files:**
- Modify: `bench_hub/server/launch_runtime.py`
- Modify: `benchmark_adapters/challenge_json.py`
- Modify: `benchmark_adapters/cvebench_layout.py`
- Test: `tests/test_launch_runtime.py`
- Test: `tests/test_cvebench_layout.py`

**Step 1: Write the failing test for CVE Bench isolated network mode**

```python
def test_cvebench_runtime_keeps_compose_project_local_networks():
    spec = build_cvebench_launch_spec("cvb-CVE-2024-4323")
    plan = materialize_compose_runtime(...)
    assert plan.agent_network_name.endswith("_target_network")
```

**Step 2: Run the focused runtime test**

Run: `python -m unittest tests.test_launch_runtime -v`
Expected: FAIL because runtime materialization still injects the shared external network

**Step 3: Implement minimal runtime changes**

Implementation targets:
- keep CVE Bench Compose-defined challenge networks intact
- identify the agent-reachable network from benchmark/runtime args
- stop treating the global external network as the default CVE Bench path
- continue preserving host port exposure for manual debugging only

**Step 4: Run runtime and layout tests**

Run: `python -m unittest tests.test_launch_runtime tests.test_cvebench_layout -v`
Expected: PASS with CVE Bench-specific network facts preserved

**Step 5: Commit**

```bash
git add bench_hub/server/launch_runtime.py benchmark_adapters/challenge_json.py benchmark_adapters/cvebench_layout.py tests/test_launch_runtime.py tests/test_cvebench_layout.py
git commit -m "feat(cvebench): preserve compose-local network semantics"
```

### Task 4: Move sandbox policy into `GlobalDockerManager`

**Files:**
- Modify: `common/agent_runtime/docker_manager.py`
- Modify: `run_evolve_batch_skill.py`
- Modify: `run_evolve_batch.py`
- Modify: `run_sequential_evolve.py`
- Modify: `run_evolve.py`
- Test: `tests/test_agent_sandbox.py`
- Create: `tests/test_docker_manager.py`

**Step 1: Write the failing test for exclusive sandbox policy**

```python
def test_cvebench_requests_exclusive_sandbox_allocation():
    manager = GlobalDockerManager(config, chal_id="cvb-CVE-2024-4323")
    first = manager.allocate_sandbox(runtime_args={"sandbox_policy": "exclusive"})
    second = manager.allocate_sandbox(runtime_args={"sandbox_policy": "exclusive"})
    assert first.container_name != second.container_name
```

**Step 2: Run the focused test**

Run: `python -m unittest tests.test_docker_manager -v`
Expected: FAIL because sandbox allocation is not yet centralized

**Step 3: Implement minimal orchestration changes**

Implementation targets:
- centralize allocation/reuse policy in `common/agent_runtime/docker_manager.py`
- allow runner/config args to choose benchmark-bound strategy
- connect allocated sandboxes to the run-specific `agent_network_name`
- keep `DockerEnvironment` unchanged as the low-level execution primitive

**Step 4: Run sandbox tests**

Run: `python -m unittest tests.test_docker_manager tests.test_agent_sandbox -v`
Expected: PASS for exclusive allocation and network attachment

**Step 5: Commit**

```bash
git add common/agent_runtime/docker_manager.py run_evolve_batch_skill.py run_evolve_batch.py run_sequential_evolve.py run_evolve.py tests/test_docker_manager.py tests/test_agent_sandbox.py
git commit -m "feat(runtime): centralize sandbox allocation policy"
```

### Task 5: Bind benchmark runtime strategy in runner/config instead of challenge metadata

**Files:**
- Modify: `run_evolve_batch_skill.py`
- Modify: `run_evolve_batch.py`
- Modify: `run_sequential_evolve.py`
- Modify: `run_evolve.py`
- Modify: `common/agent_runtime/challenge_client.py`
- Test: `tests/test_challenge_client_registry.py`
- Test: `tests/test_run_evolve_batch_skill_scheduler.py`

**Step 1: Write the failing test for benchmark-bound runtime args**

```python
def test_cvebench_launch_uses_runner_runtime_args():
    chal_data = manager.get_challenge("cvb-CVE-2024-4323")
    assert chal_data["runtime"]["agent_network_name"] is not None
```

**Step 2: Run the focused test**

Run: `python -m unittest tests.test_challenge_client_registry tests.test_run_evolve_batch_skill_scheduler -v`
Expected: FAIL because benchmark orchestration still leaks through ad hoc runtime assumptions

**Step 3: Implement minimal config plumbing**

Implementation targets:
- define benchmark-bound runtime args in runner/config
- thread them through `ChallengeClient` launch calls
- keep `challenge.json` free of runtime policy fields
- keep `chal_data.runtime` limited to per-run outputs

**Step 4: Run focused manager/scheduler tests**

Run: `python -m unittest tests.test_challenge_client_registry tests.test_run_evolve_batch_skill_scheduler -v`
Expected: PASS

**Step 5: Commit**

```bash
git add run_evolve_batch_skill.py run_evolve_batch.py run_sequential_evolve.py run_evolve.py common/agent_runtime/challenge_client.py tests/test_challenge_client_registry.py tests/test_run_evolve_batch_skill_scheduler.py
git commit -m "feat(runtime): bind benchmark policy in runner args"
```

### Task 6: Verify `/done` behavior still works for multi-service CVE Bench runs

**Files:**
- Modify: `tests/test_benchmark_scorers.py`
- Modify: `tests/test_target_runtime_recovery.py`
- Extend: `tests/test_cvebench_parallel_runtime.py`

**Step 1: Write the failing test for a multi-service grader path**

```python
def test_done_poll_survives_multi_service_cvebench_runtime():
    payload = launch("cvb-CVE-2024-4323")
    result = poll_done(payload["run_id"])
    assert "status" in result
```

**Step 2: Run the focused scorer tests**

Run: `python -m unittest tests.test_benchmark_scorers tests.test_cvebench_parallel_runtime -v`
Expected: FAIL until scorer/runtime wiring uses the new run-specific runtime facts

**Step 3: Implement minimal scorer/runtime adjustments**

Implementation targets:
- make scorer lookup consume run-specific runtime outputs
- ensure recovery/cleanup logic does not collapse multiple runs of the same challenge
- keep `/done` polling attached to the run's isolated target service

**Step 4: Run focused verification**

Run: `python -m unittest tests.test_benchmark_scorers tests.test_target_runtime_recovery tests.test_cvebench_parallel_runtime -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_benchmark_scorers.py tests/test_target_runtime_recovery.py tests/test_cvebench_parallel_runtime.py
git commit -m "test(cvebench): verify isolated done polling"
```

### Task 7: Run the broader regression suite

**Files:**
- No code changes required

**Step 1: Run the regression suite**

Run:

```bash
python -m unittest \
  tests.test_launch_runtime \
  tests.test_challenge_server_registry \
  tests.test_challenge_server_runtime_locking \
  tests.test_cvebench_layout \
  tests.test_cvebench_parallel_runtime \
  tests.test_challenge_client_registry \
  tests.test_benchmark_scorers \
  tests.test_agent_sandbox \
  tests.test_run_evolve_batch_skill_scheduler \
  tests.test_target_runtime_recovery \
  -v
```

Expected: PASS

**Step 2: Run a manual smoke test for two concurrent launches**

Run:

```bash
python bench_hub/server/challenge_server.py
```

In a second shell:

```bash
curl -s http://127.0.0.1:7900/launch/cvb-CVE-2024-4323 | python -m json.tool
curl -s http://127.0.0.1:7900/launch/cvb-CVE-2024-4323 | python -m json.tool
```

Expected:

- distinct `run_id`
- distinct `project_name`
- distinct `agent_network_name`
- no challenge-name collision in service routing

**Step 3: Commit final implementation**

```bash
git add -A
git commit -m "feat(cvebench): isolate parallel runtime per run"
```
