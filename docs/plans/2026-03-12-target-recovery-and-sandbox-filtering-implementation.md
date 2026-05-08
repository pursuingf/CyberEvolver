# Target Recovery and Sandbox Filtering Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Filter local shell-wrapper false positives out of agent observations while adding single-flight foreground recovery for truly unreachable targets.

**Architecture:** Keep `docker_env.py` narrowly focused on command execution, false-positive classification, and one-time retry orchestration. Put challenge lifecycle recovery behind a shared per-challenge runtime coordinator in the challenge worker process, and serialize challenge restarts again inside `challenge_server` so foreground recovery and the background monitor cannot race each other.

**Tech Stack:** Python 3.11, `unittest`, `threading`, `requests`, FastAPI, Docker SDK

---

### Task 1: Make the Sandbox Reproducer Import-safe and Testable

**Files:**
- Modify: `tests/test_agent_sandbox.py`
- Test: `tests/test_agent_sandbox.py`

**Step 1: Write the failing test**

Refactor the current top-level script into `unittest.TestCase` coverage so the file can be imported safely. Add tests for a helper API in `common/agent_runtime/docker_env.py` such as:

```python
def test_nc_head_wrapper_failure_is_detected(self):
    detected = is_shell_wrapper_false_positive(
        command="nc 127.0.0.1 1 -w 1 | head -c 10",
        output="exec /usr/bin/bash: operation not permitted\n",
        returncode=255,
    )
    self.assertTrue(detected)
```

Add negative controls for:

```python
def test_plain_shell_error_is_not_detected(self):
    detected = is_shell_wrapper_false_positive(
        command="cat /definitely/not/exist | head -c 10",
        output="cat: /definitely/not/exist: No such file or directory\n",
        returncode=1,
    )
    self.assertFalse(detected)
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_agent_sandbox -v`

Expected: FAIL because the helper does not exist and the file is not yet structured as an import-safe test module.

**Step 3: Write minimal implementation**

Move the manual reproduction code under:

```python
if __name__ == "__main__":
    ...
```

Then add import-safe unit tests that target the new classifier helper.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_agent_sandbox -v`

Expected: PASS

### Task 2: Add a Narrow False-positive Classifier to `docker_env.py`

**Files:**
- Modify: `common/agent_runtime/docker_env.py`
- Test: `tests/test_agent_sandbox.py`

**Step 1: Write the failing test**

Extend `tests/test_agent_sandbox.py` with one more assertion around a sanitizer helper:

```python
def test_sanitized_agent_output_is_short(self):
    result = sanitize_agent_output_for_false_positive(
        command="echo hi | nc 127.0.0.1 1 -w 1 | head -c 10",
        output="exec /usr/bin/bash: operation not permitted\n",
    )
    self.assertIn("[SYSTEM]", result)
    self.assertNotIn("base", result.lower())
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_agent_sandbox.SandboxClassifierTests -v`

Expected: FAIL because the sanitizer helper does not exist yet.

**Step 3: Write minimal implementation**

In `common/agent_runtime/docker_env.py`:

- extract the duplicated subprocess logic into a small private runner helper
- add `is_shell_wrapper_false_positive(...)`
- add `sanitize_agent_false_positive_output(...)`
- update `agent_execute()` so it:
  - logs raw output unchanged
  - returns the sanitized output to the agent only when the classifier matches
  - leaves `execute()` behavior unchanged

Use a short system line like:

```python
"[SYSTEM] command hit a local shell-wrapper false positive; try another read method"
```

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_agent_sandbox.SandboxClassifierTests -v`

Expected: PASS

### Task 3: Route Agent Steps Through `agent_execute()`

**Files:**
- Modify: `agent/agent.py`
- Test: `tests/test_agent_sandbox.py`

**Step 1: Write the failing test**

Add a focused unit test that stubs an env object with both `execute()` and `agent_execute()` and asserts `Agent.step()` uses the agent-specific path:

```python
def test_agent_step_uses_agent_execute(self):
    env = FakeEnv()
    agent = build_agent(env=env)
    agent.step("echo hi")
    self.assertEqual(env.agent_calls, 1)
    self.assertEqual(env.execute_calls, 0)
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_agent_sandbox.AgentStepRoutingTests -v`

Expected: FAIL because `Agent.step()` still calls `execute()`.

**Step 3: Write minimal implementation**

Update `Agent.step()` in `agent/agent.py`:

```python
exec_result = self.env.agent_execute(
    f"export PATH=\"$PATH:{self.cwd}/commands\" && " + action_cmd,
    cwd=self.cwd,
    timeout=200,
)
```

Keep the rest of the method unchanged.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_agent_sandbox.AgentStepRoutingTests -v`

Expected: PASS

### Task 4: Add Challenge Runtime Refresh Support

**Files:**
- Modify: `common/agent_runtime/challenge_client.py`
- Test: `tests/test_target_runtime_recovery.py`

**Step 1: Write the failing test**

Create a new unit test module that verifies `ChallengeClient` can force-refresh a running challenge and update cached services:

```python
def test_refresh_challenge_data_updates_runtime_cache(self):
    backend = FakeBackend([
        {"services": {"svc": {"host": "10.0.0.1", "port": 1234}}},
        {"services": {"svc": {"host": "10.0.0.1", "port": 5678}}},
    ])
    manager = build_manager_with_backend(backend)
    manager.get_challenge_data("demo")
    refreshed = manager.refresh_challenge_data("demo", force_recreate=True)
    self.assertEqual(refreshed["target_info"]["svc"]["port"], 5678)
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_target_runtime_recovery.ChallengeClientRefreshTests -v`

Expected: FAIL because the refresh API does not exist yet.

**Step 3: Write minimal implementation**

In `common/agent_runtime/challenge_client.py`:

- change backend initialization signatures to accept `force_recreate: bool = False`
- update `RemoteBackend.initialize(...)` to pass `force_recreate=true` to `GET /launch/{chal_id}`
- add `ChallengeClient.refresh_challenge_data(challenge_id, force_recreate=False)`
- ensure runtime cache, `target_status`, and `target_info` all update from the refreshed record

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_target_runtime_recovery.ChallengeClientRefreshTests -v`

Expected: PASS

### Task 5: Add a Single-flight Runtime Coordinator in the Challenge Worker

**Files:**
- Modify: `run_evolve_batch_skill.py`
- Create: `tests/test_target_runtime_recovery.py`

**Step 1: Write the failing test**

In `tests/test_target_runtime_recovery.py`, add concurrency coverage for a coordinator helper that all task threads share:

```python
def test_concurrent_recovery_only_calls_force_recreate_once(self):
    coordinator = build_coordinator_with_failing_probe()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(coordinator.ensure_target_available, chal_data_copy()) for _ in range(4)]
        results = [f.result() for f in futures]

    self.assertEqual(coordinator.refresh_calls, 1)
    self.assertTrue(all(r.recovered for r in results))
```

Also add a test for task-local `chal_data` refresh:

```python
def test_recovery_refreshes_task_local_target_info(self):
    ...
    self.assertEqual(chal_data["target_info"]["svc"]["port"], 5678)
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_target_runtime_recovery.RuntimeCoordinatorTests -v`

Expected: FAIL because the coordinator does not exist yet.

**Step 3: Write minimal implementation**

In `run_evolve_batch_skill.py`:

- add a small `ChallengeRuntimeCoordinator`
- give it:
  - a per-challenge `threading.Lock`
  - a probe helper for current `target_info`
  - `ensure_target_available(chal_data)`
  - `recover_and_refresh(chal_data, reason)`
- create one coordinator per challenge worker in `evolve_single_challenge()`
- attach it to the docker environment so `agent_execute()` can call back into it
- call a preflight check before `agent.run(...)`

Return a small structured result from recovery, for example:

```python
{"recovered": True, "target_changed": True, "target_info": refreshed_info}
```

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_target_runtime_recovery.RuntimeCoordinatorTests -v`

Expected: PASS

### Task 6: Teach `agent_execute()` to Recover Once and Retry Once

**Files:**
- Modify: `common/agent_runtime/docker_env.py`
- Modify: `run_evolve_batch_skill.py`
- Test: `tests/test_target_runtime_recovery.py`

**Step 1: Write the failing test**

Add a unit test that stubs the command runner and coordinator to verify the one-retry contract:

```python
def test_agent_execute_recovers_and_retries_once(self):
    env = build_env_with_results([
        {"output": "nc: Connection refused\n", "returncode": 1},
        {"output": "welcome\n", "returncode": 0},
    ])
    env.runtime_coordinator = FakeCoordinator(recover_result={"recovered": True, "target_changed": False})

    result = env.agent_execute("nc 127.0.0.1 31337", timeout=1)

    self.assertEqual(result["output"], "[SYSTEM] target recovered; retried once\nwelcome\n")
    self.assertEqual(env.run_calls, 2)
```

Add a companion test that recovery is not repeated after the retry:

```python
def test_agent_execute_does_not_loop_recovery(self):
    ...
    self.assertEqual(env.runtime_coordinator.refresh_calls, 1)
    self.assertEqual(env.run_calls, 2)
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_target_runtime_recovery.AgentExecuteRecoveryTests -v`

Expected: FAIL because `agent_execute()` has no recovery path yet.

**Step 3: Write minimal implementation**

In `common/agent_runtime/docker_env.py`:

- add a connectivity-error detector that is separate from the false-positive classifier
- allow `agent_execute()` to consult `self.runtime_coordinator` only on the real-connectivity branch
- if recovery succeeds, rerun the same command once
- prefix the retried output with one short system line only when recovery actually occurred
- if the endpoint changed, add one extra short line with the new host/port

Keep `execute()` unchanged.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_target_runtime_recovery.AgentExecuteRecoveryTests -v`

Expected: PASS

### Task 7: Serialize `challenge_server` Recovery Per Challenge

**Files:**
- Modify: `bench_hub/server/challenge_server.py`
- Create: `tests/test_challenge_server_runtime_locking.py`

**Step 1: Write the failing test**

Add a unit test that simulates two concurrent `force_recreate` calls for the same `chal_id` and asserts cleanup/relaunch happens only once:

```python
def test_same_challenge_recreate_is_single_flight(self):
    server = load_server_module_with_fakes()
    run_parallel_recreate_calls(server, chal_id="demo")
    self.assertEqual(server.cleanup_calls, 1)
    self.assertEqual(server.launch_calls, 1)
```

Add a second test that simulates monitor overlap:

```python
def test_monitor_skips_challenge_already_recovering(self):
    ...
    self.assertEqual(server.monitor_restart_calls, 0)
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_challenge_server_runtime_locking -v`

Expected: FAIL because there is no per-challenge recovery serialization yet.

**Step 3: Write minimal implementation**

In `bench_hub/server/challenge_server.py`:

- add a global lock registry keyed by `chal_id`
- add a small state guard for "foreground recovery in progress"
- wrap `cleanup_instance()`, `launch_challenge()`, and monitor-triggered recreate in the same per-challenge lock
- have the monitor skip a challenge already under foreground recovery

Do not change healthy-instance reuse behavior.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_challenge_server_runtime_locking -v`

Expected: PASS

### Task 8: Verification

**Files:**
- Verify: `agent/agent.py`
- Verify: `common/agent_runtime/docker_env.py`
- Verify: `common/agent_runtime/challenge_client.py`
- Verify: `run_evolve_batch_skill.py`
- Verify: `bench_hub/server/challenge_server.py`
- Verify: `tests/test_agent_sandbox.py`
- Verify: `tests/test_target_runtime_recovery.py`
- Verify: `tests/test_challenge_server_runtime_locking.py`

**Step 1: Run focused tests**

Run: `python -m unittest tests.test_agent_sandbox tests.test_target_runtime_recovery tests.test_challenge_server_runtime_locking -v`

Expected: All pass

**Step 2: Run syntax verification**

Run: `python -m py_compile agent/agent.py common/agent_runtime/docker_env.py common/agent_runtime/challenge_client.py run_evolve_batch_skill.py bench_hub/server/challenge_server.py tests/test_agent_sandbox.py tests/test_target_runtime_recovery.py tests/test_challenge_server_runtime_locking.py`

Expected: exit 0

**Step 3: Run entrypoint smoke checks**

Run: `python run_evolve_batch_skill.py --help`

Expected: exit 0

Run: `python bench_hub/server/challenge_server.py --help`

Expected: exit 0 or the existing server help behavior without a traceback

**Step 4: Commit**

```bash
git add agent/agent.py common/agent_runtime/docker_env.py common/agent_runtime/challenge_client.py run_evolve_batch_skill.py bench_hub/server/challenge_server.py tests/test_agent_sandbox.py tests/test_target_runtime_recovery.py tests/test_challenge_server_runtime_locking.py
git commit -m "fix: recover unreachable targets and filter sandbox false positives"
```
