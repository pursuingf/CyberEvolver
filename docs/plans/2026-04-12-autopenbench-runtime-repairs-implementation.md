# AutoPenBench Runtime Repairs Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Repair the confirmed AutoPenBench launch mismatches for `network_security/vm1-3`, `web_security/vm3`, and `real-world/cve/vm9` without changing vulnerable software versions.

**Architecture:** Keep host-port targets on the existing path, but let `challenge.json` explicitly request compose-local networking for internal-only targets. Update runtime materialization to honor `exposure_mode`, then repair the affected challenge metadata and the vm3 database readiness gate.

**Tech Stack:** Python, Docker Compose, unittest, benchmark metadata JSON/YAML

---

### Task 1: Lock in adapter expectations for repaired AutoPenBench targets

**Files:**
- Modify: `tests/test_autopenbench_adapter.py`
- Test: `tests/test_autopenbench_adapter.py`

**Step 1: Write the failing tests**

Add tests that expect:
- `network_security/vm1` resolves to `internal_port == 52693`
- `network_security/vm2` and `network_security/vm3` preserve explicit compose-local runtime patches from `challenge.json`
- `real-world/cve/vm9` resolves to `internal_port == 445`

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_autopenbench_adapter -v`
Expected: FAIL because the metadata and adapter behavior do not yet match the repaired design.

**Step 3: Write minimal implementation**

Update only the challenge metadata and adapter code needed for these tests to pass.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_autopenbench_adapter -v`
Expected: PASS

### Task 2: Prevent host-port injection for compose-local AutoPenBench targets

**Files:**
- Modify: `tests/test_launch_runtime.py`
- Modify: `benchmark_adapters/challenge_json.py`
- Modify: `bench_hub/server/launch_runtime.py`
- Test: `tests/test_launch_runtime.py`

**Step 1: Write the failing test**

Add a runtime test that builds a `LaunchSpec` with:
- `exposure_mode="network"`
- `runtime_patches["network_mode"] = "compose_project_local"`
- `runtime_patches["agent_network"] = "net-main_network"`

The test should assert that the target service keeps the compose-local network attachment but receives no public `ports:` block.

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_launch_runtime -v`
Expected: FAIL because runtime materialization still injects host `ports:` whenever `internal_port` is set.

**Step 3: Write minimal implementation**

Teach `ChallengeJsonAdapter` to honor explicit `network_mode` and `agent_network` fields from `challenge.json`, and teach `materialize_compose_runtime()` to skip host-port publication when `spec.exposure_mode != "host_ports"`.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_launch_runtime -v`
Expected: PASS

### Task 3: Add MySQL readiness gating for `web_security/vm3`

**Files:**
- Modify: `tests/test_autopenbench_adapter.py`
- Modify: `benchmarks/autopenbench/benchmark/machines/in-vitro/web_security/docker-compose.yml`
- Test: `tests/test_autopenbench_adapter.py`

**Step 1: Write the failing test**

Add a regression test that loads the compose file and asserts:
- `in-vitro_web_security_vm3_database` has a MySQL health check
- `in-vitro_web_security_vm3` depends on the database with `condition: service_healthy`

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_autopenbench_adapter -v`
Expected: FAIL because the compose file only uses plain `depends_on`.

**Step 3: Write minimal implementation**

Update the compose file with the smallest readiness health check needed for MySQL startup.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_autopenbench_adapter -v`
Expected: PASS

### Task 4: Verify focused runtime regressions

**Files:**
- Modify: `tests/test_autopenbench_adapter.py`
- Modify: `tests/test_launch_runtime.py`

**Step 1: Run focused verification**

Run:
- `python -m unittest tests.test_autopenbench_adapter -v`
- `python -m unittest tests.test_launch_runtime -v`
- `python -m unittest bench_hub/server.test_challenge_server -q`

Expected: all pass

**Step 2: Run an AutoPenBench smoke check**

Run a minimal launch smoke test against the repaired targets with an isolated manager namespace:
- `network_security/vm1`
- `network_security/vm2`
- `network_security/vm3`
- `web_security/vm3`
- `real-world/cve/vm9`

Expected:
- `vm1` and `vm9` expose the corrected TCP ports
- `vm2` and `vm3` launch without public host ports but report the compose-local agent network
- `web_security/vm3` no longer races the database on first launch
