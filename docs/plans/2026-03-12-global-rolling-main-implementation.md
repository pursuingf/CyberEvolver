# Global Rolling Main Scheduler Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace category-batch scheduling in `run_evolve_batch_skill.py::main()` with a throughput-first global rolling scheduler that lazily initializes challenge data at submit time.

**Architecture:** The implementation keeps challenge execution workers unchanged and rewrites only the top-level scheduling loop. `main()` will maintain a global pending queue plus a global inflight future map, refill the process pool one challenge at a time, and reuse the existing result-summary pipeline with a globalized broken-pool failure path.

**Tech Stack:** Python 3.11, `concurrent.futures.ProcessPoolExecutor`, existing `ChallengeClient`, existing logging helpers, existing process-pool guard utilities, `unittest`

---

### Task 1: Add scheduler-focused tests for rolling submission behavior

**Files:**
- Create: `tests/test_run_evolve_batch_skill_scheduler.py`
- Reference: `run_evolve_batch_skill.py`
- Reference: `utils/process_pool_guards.py`

**Step 1: Write the failing test**

Add a unit test that simulates multiple selected challenges across at least two categories and asserts that scheduling refills globally instead of waiting for a whole category to drain.

```python
def test_global_scheduler_refills_across_categories(self):
    pending = [
        {"chal_id": "c1", "category": "crypto"},
        {"chal_id": "c2", "category": "crypto"},
        {"chal_id": "p1", "category": "pwn"},
    ]
    # simulate one completion and assert the next pending challenge is submitted immediately
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_evolve_batch_skill_scheduler -v`
Expected: FAIL because the new scheduling helpers do not exist yet.

**Step 3: Write minimal implementation support**

Add the smallest scheduler helper scaffolding needed to let the test import the targeted functions or exercise the scheduling logic.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_run_evolve_batch_skill_scheduler -v`
Expected: PASS for the new rolling-refill test.

**Step 5: Commit**

```bash
git add tests/test_run_evolve_batch_skill_scheduler.py run_evolve_batch_skill.py utils/process_pool_guards.py
git commit -m "test: add rolling scheduler submission coverage"
```

### Task 2: Add lazy `get_challenge_data()` timing tests

**Files:**
- Modify: `tests/test_run_evolve_batch_skill_scheduler.py`
- Reference: `run_evolve_batch_skill.py`

**Step 1: Write the failing test**

Add a test with a fake `ChallengeClient` that records calls to `get_challenge_data(chal_id)` and verifies that only the challenges actually submitted so far have been initialized.

```python
def test_get_challenge_data_is_called_only_at_submit_time(self):
    manager = FakeChallengeClient()
    # schedule with max_workers=1 and assert only one challenge is initialized initially
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_evolve_batch_skill_scheduler -v`
Expected: FAIL because initialization is still too eager.

**Step 3: Write minimal implementation**

Implement just-in-time challenge initialization inside the new submit helper, not during queue construction.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_run_evolve_batch_skill_scheduler -v`
Expected: PASS for the lazy initialization test.

**Step 5: Commit**

```bash
git add tests/test_run_evolve_batch_skill_scheduler.py run_evolve_batch_skill.py
git commit -m "test: cover lazy challenge initialization timing"
```

### Task 3: Replace category-batch scheduling in `main()` with a rolling global scheduler

**Files:**
- Modify: `run_evolve_batch_skill.py:929-1114`
- Reference: `run_evolve_batch_skill.py:158-210`

**Step 1: Write the failing test**

Add or extend a test that exercises the top-level scheduling loop behavior with stubbed submit/collect helpers and verifies:
- no category outer loop remains in effect
- worker slots are refilled immediately after a completion
- pending work is tracked globally

```python
def test_main_uses_global_pending_and_inflight_sets(self):
    # assert behavior rather than source text
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_evolve_batch_skill_scheduler -v`
Expected: FAIL because `main()` still uses category-batch scheduling.

**Step 3: Write minimal implementation**

Rewrite `main()` to:
- build `pending_items` from selected challenge metadata
- prefill the executor up to `max_workers`
- collect one completed future at a time
- refill one challenge at a time
- keep `results`, budget handling, dispatcher startup, and final summary behavior intact

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_run_evolve_batch_skill_scheduler -v`
Expected: PASS for global rolling scheduling behavior.

**Step 5: Commit**

```bash
git add run_evolve_batch_skill.py tests/test_run_evolve_batch_skill_scheduler.py
git commit -m "feat: switch main to global rolling challenge scheduling"
```

### Task 4: Globalize broken-pool result collection helpers

**Files:**
- Modify: `utils/process_pool_guards.py`
- Modify: `tests/test_run_evolve_batch_skill_guards.py`
- Reference: `run_evolve_batch_skill.py`

**Step 1: Write the failing test**

Add coverage for global broken-pool behavior that distinguishes:
- submitted but unresolved inflight challenges
- pending but never-submitted challenges

```python
def test_broken_pool_marks_inflight_and_pending_challenges_differently(self):
    ...
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_evolve_batch_skill_guards -v`
Expected: FAIL because helper logic is still category-scoped.

**Step 3: Write minimal implementation**

Refactor the category helper into global scheduling helpers that:
- collect one result at a time or iterate a global inflight map
- stop submission on `BrokenProcessPool`
- produce compatible failed result entries with clearer error messages

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_run_evolve_batch_skill_guards -v`
Expected: PASS for the new global broken-pool behavior.

**Step 5: Commit**

```bash
git add utils/process_pool_guards.py tests/test_run_evolve_batch_skill_guards.py run_evolve_batch_skill.py
git commit -m "fix: globalize broken process pool scheduling guards"
```

### Task 5: Update logging for global scheduling and category progress visibility

**Files:**
- Modify: `run_evolve_batch_skill.py`
- Test: `tests/test_run_evolve_batch_skill_scheduler.py`

**Step 1: Write the failing test**

Add a test that checks for the new logging semantics, especially:
- scheduling mode log line
- execution mix by category log line
- submit-time logging with global inflight/pending counts

```python
def test_global_scheduler_logging_includes_mix_and_progress(self):
    ...
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_evolve_batch_skill_scheduler -v`
Expected: FAIL because the current logs are category-batch oriented.

**Step 3: Write minimal implementation**

Adjust startup and per-challenge logging to reflect:
- global rolling scheduling
- category mix instead of category execution order
- per-submit and per-completion progress context

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_run_evolve_batch_skill_scheduler -v`
Expected: PASS for the updated logging behavior.

**Step 5: Commit**

```bash
git add run_evolve_batch_skill.py tests/test_run_evolve_batch_skill_scheduler.py
git commit -m "chore: update main scheduling logs for rolling execution"
```

### Task 6: Verify summary compatibility and full regression surface

**Files:**
- Modify: `tests/test_run_evolve_batch_skill_scheduler.py`
- Reference: `run_evolve_batch_skill.py:1115-1200`

**Step 1: Write the failing test**

Add a regression test that feeds representative results from multiple categories into the final summary path and confirms `by_category`, solved counts, and overall totals stay unchanged after the scheduler redesign.

```python
def test_summary_remains_category_aggregated_after_scheduler_rewrite(self):
    ...
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_evolve_batch_skill_scheduler tests.test_run_evolve_batch_skill_guards -v`
Expected: FAIL if summary assumptions were accidentally broken.

**Step 3: Write minimal implementation**

Make only the smallest changes needed so scheduling refactor preserves the final summary contract.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_run_evolve_batch_skill_scheduler tests.test_run_evolve_batch_skill_guards tests.test_worker_diagnostics tests.test_refiner_unicode_validation -v`
Expected: PASS for all touched tests.

**Step 5: Commit**

```bash
git add tests/test_run_evolve_batch_skill_scheduler.py run_evolve_batch_skill.py utils/process_pool_guards.py
git commit -m "test: preserve summary compatibility after scheduler rewrite"
```

### Task 7: Run final verification and smoke checks

**Files:**
- Modify: none
- Verify: `run_evolve_batch_skill.py`

**Step 1: Run unit tests**

Run:
```bash
python -m unittest \
  tests.test_run_evolve_batch_skill_scheduler \
  tests.test_run_evolve_batch_skill_guards \
  tests.test_worker_diagnostics \
  tests.test_refiner_unicode_validation -v
```
Expected: PASS

**Step 2: Run syntax verification**

Run:
```bash
python -m py_compile run_evolve_batch_skill.py utils/process_pool_guards.py tests/test_run_evolve_batch_skill_scheduler.py tests/test_run_evolve_batch_skill_guards.py
```
Expected: PASS with no output

**Step 3: Run CLI smoke test**

Run:
```bash
python run_evolve_batch_skill.py --help
```
Expected: help text prints successfully

**Step 4: Optional multi-category smoke run**

Run a small job with at least two categories and `--max-workers 2`, then verify from `run.log` that:
- a challenge from one category can start before the previous category fully drains
- `get_challenge_data()` is only invoked at submit time
- progress logs reflect global inflight/pending counts

**Step 5: Commit**

```bash
git add docs/plans/2026-03-12-global-rolling-main-design.md docs/plans/2026-03-12-global-rolling-main-implementation.md
git commit -m "docs: add global rolling scheduler design and plan"
```
