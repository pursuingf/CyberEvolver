# Benchmark Health Check Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a standalone script that checks benchmark targets through the running CTF manager and reports which challenges fail to launch or stay reachable.

**Architecture:** Keep `bench_hub/server/test_challenge_server.py` focused on regression tests and add a separate CLI script, `bench_hub/server/check_benchmark_health.py`, for operational health sweeps. The new script will reuse the same launch/stop/API concepts, classify failures into stable categories, and optionally write a JSON report for later analysis.

**Tech Stack:** Python, `requests`, `argparse`, existing CTF manager HTTP API

---

### Task 1: Add failing tests for classification helpers

**Files:**
- Create: `bench_hub/server/test_check_benchmark_health.py`
- Test: `bench_hub/server/test_check_benchmark_health.py`

**Step 1: Write the failing test**

Add tests that expect:
- static challenges to be classified as `static`
- web challenges with open TCP port but failed HTTP probe to be classified as `http_unreachable`
- launch API failures to be classified as `launch_failed`

**Step 2: Run test to verify it fails**

Run: `python -m unittest /data/pxd-team/workspace/fyh/evolve_ctf_agent/bench_hub/server/test_check_benchmark_health.py -q`
Expected: FAIL because `check_benchmark_health.py` does not exist yet.

**Step 3: Write minimal implementation**

Create `bench_hub/server/check_benchmark_health.py` with the smallest helper functions needed by the tests.

**Step 4: Run test to verify it passes**

Run: `python -m unittest /data/pxd-team/workspace/fyh/evolve_ctf_agent/bench_hub/server/test_check_benchmark_health.py -q`
Expected: PASS

### Task 2: Implement CLI sweep flow

**Files:**
- Modify: `bench_hub/server/check_benchmark_health.py`
- Test: `bench_hub/server/test_check_benchmark_health.py`

**Step 1: Write the failing test**

Add tests for:
- challenge filtering by `--index` and `--challenge`
- JSON report serialization shape
- cleanup call behavior when `stop_after_test=True`

**Step 2: Run test to verify it fails**

Run: `python -m unittest /data/pxd-team/workspace/fyh/evolve_ctf_agent/bench_hub/server/test_check_benchmark_health.py -q`
Expected: FAIL on missing CLI/report behaviors.

**Step 3: Write minimal implementation**

Add argument parsing, challenge loading, launch probe, stop handling, and report writing.

**Step 4: Run test to verify it passes**

Run: `python -m unittest /data/pxd-team/workspace/fyh/evolve_ctf_agent/bench_hub/server/test_check_benchmark_health.py -q`
Expected: PASS

### Task 3: Verify end-to-end script behavior

**Files:**
- Modify: `bench_hub/server/check_benchmark_health.py`

**Step 1: Run focused verification**

Run:
- `python -m unittest /data/pxd-team/workspace/fyh/evolve_ctf_agent/bench_hub/server/test_check_benchmark_health.py -q`
- `python -m unittest /data/pxd-team/workspace/fyh/evolve_ctf_agent/bench_hub/server/test_challenge_server.py -q`
- `python /data/pxd-team/workspace/fyh/evolve_ctf_agent/bench_hub/server/test_launch_runtime_regression.py`

Expected: all pass

**Step 2: Smoke-test the CLI help**

Run: `python /data/pxd-team/workspace/fyh/evolve_ctf_agent/bench_hub/server/check_benchmark_health.py --help`
Expected: usage output with index/challenge/report options
