# Process Pool Guardrails Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve diagnosis and containment when a challenge worker fails during evaluation, without changing the overall evolution architecture.

**Architecture:** Add narrow guardrails at the three failure boundaries already exposed by logs: scheduler result collection, per-task file logging, and main-process handling after `BrokenProcessPool`. Keep behavior stable for healthy runs while making failed runs easier to diagnose and less likely to interfere with other workers.

**Tech Stack:** Python 3.11, `unittest`, `concurrent.futures`, standard `logging`

---

### Task 1: Scheduler Result-Collection Diagnostics

**Files:**
- Modify: `evolve/scheduler.py`
- Modify: `utils/worker_diagnostics.py`
- Test: `tests/test_worker_diagnostics.py`

**Step 1: Write the failing test**

Add tests for a new diagnostic formatter that records:
- node id
- sample id
- failure stage (`future_result` or `result_processing`)
- exception type and message

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_worker_diagnostics.WorkerDiagnosticsTests -v`

Expected: FAIL because the formatter does not exist yet.

**Step 3: Write minimal implementation**

Add a formatter in `utils/worker_diagnostics.py`, then use it inside `TaskScheduler.submit_tasks()` around:
- `future.result()`
- result append / progress log / node stat update

Re-raise after logging so current behavior stays visible to the caller.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_worker_diagnostics.WorkerDiagnosticsTests -v`

Expected: PASS

### Task 2: Task Logger Handler Cleanup

**Files:**
- Modify: `run_evolve_batch_skill.py`
- Test: `tests/test_run_evolve_batch_skill_guards.py`

**Step 1: Write the failing test**

Add a focused unit test around a small helper that closes and detaches a `logging.FileHandler`.

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_evolve_batch_skill_guards.LoggerCleanupTests -v`

Expected: FAIL because the helper does not exist yet.

**Step 3: Write minimal implementation**

Add a helper in `run_evolve_batch_skill.py` that:
- removes the handler from the logger if attached
- flushes it best-effort
- closes it best-effort

Call it from `run_node_task()` in `finally`.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_run_evolve_batch_skill_guards.LoggerCleanupTests -v`

Expected: PASS

### Task 3: Broken Process Pool Containment

**Files:**
- Modify: `run_evolve_batch_skill.py`
- Test: `tests/test_run_evolve_batch_skill_guards.py`

**Step 1: Write the failing test**

Add a unit test around a new helper that processes category futures and confirms:
- on first `BrokenProcessPool`, it records the current challenge as failed
- it records unresolved challenges in the same category as failed without calling `future.result()` again
- it skips immediate `finish_challenge()` for unresolved broken-pool challenges

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_evolve_batch_skill_guards.BrokenPoolHandlingTests -v`

Expected: FAIL because the helper does not exist yet.

**Step 3: Write minimal implementation**

Extract the category future-collection loop into a helper that:
- handles success and ordinary exceptions as before
- handles `BrokenProcessPool` separately
- marks remaining same-category futures as failed due to pool break
- avoids immediate teardown for those unresolved futures
- returns a flag so `main()` stops submitting or collecting more categories with the broken executor

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_run_evolve_batch_skill_guards.BrokenPoolHandlingTests -v`

Expected: PASS

### Task 4: Verification

**Files:**
- Verify: `run_evolve_batch_skill.py`
- Verify: `evolve/scheduler.py`
- Verify: `utils/worker_diagnostics.py`
- Verify: `tests/test_worker_diagnostics.py`
- Verify: `tests/test_run_evolve_batch_skill_guards.py`

**Step 1: Run focused tests**

Run: `python -m unittest tests.test_worker_diagnostics tests.test_run_evolve_batch_skill_guards -v`

Expected: All pass

**Step 2: Run syntax verification**

Run: `python -m py_compile run_evolve_batch_skill.py evolve/scheduler.py utils/worker_diagnostics.py tests/test_worker_diagnostics.py tests/test_run_evolve_batch_skill_guards.py`

Expected: exit 0

**Step 3: Run entrypoint smoke check**

Run: `python run_evolve_batch_skill.py --help`

Expected: exit 0
