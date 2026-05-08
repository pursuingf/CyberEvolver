# LLM Load Test Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a single load-test script with `direct` and `dispatcher` modes that stresses any configured model with large inputs and reports throughput plus error frequencies.

**Architecture:** The script will live under `scripts/` and use a shared payload generator, shared concurrency runner, and shared report pipeline. Only the request transport differs between the two modes.

**Tech Stack:** Python 3, `argparse`, `yaml`, `httpx`, local dispatcher runtime, JSON/JSONL reporting.

---

### Task 1: Create result and config helpers

**Files:**
- Create: `scripts/llm_load_test.py`
- Test: `tests/test_llm_load_test.py`

**Step 1: Write the failing tests**

Add tests for:
- loading one model config from `configs/model.yml`
- rejecting a missing model name
- normalizing a per-request result record into aggregate buckets

**Step 2: Run test to verify it fails**

Run:
`/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_llm_load_test -v`

Expected:
- import or missing-symbol failures for new helper functions

**Step 3: Write minimal implementation**

Implement in `scripts/llm_load_test.py`:
- config loader helper
- request result dataclass / dict shape
- aggregation helper skeleton

**Step 4: Run test to verify it passes**

Run the same unittest command and confirm the new helper tests pass.

**Step 5: Commit**

```bash
git add scripts/llm_load_test.py tests/test_llm_load_test.py
git commit -m "feat(load-test): add config helpers"
```

### Task 2: Add payload generation and summary aggregation

**Files:**
- Modify: `scripts/llm_load_test.py`
- Modify: `tests/test_llm_load_test.py`

**Step 1: Write the failing tests**

Add tests for:
- deterministic payload generation from a seed
- correct `errors_by_kind`, `errors_by_type`, `errors_by_status`
- correct per-second throughput buckets

**Step 2: Run test to verify it fails**

Run:
`/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_llm_load_test -v`

Expected:
- failures for missing payload builder and incomplete aggregation

**Step 3: Write minimal implementation**

Implement:
- deterministic payload generator
- error-message normalization helper
- summary aggregation with latency percentiles and per-second buckets

**Step 4: Run test to verify it passes**

Run the same unittest command and confirm the suite is green.

**Step 5: Commit**

```bash
git add scripts/llm_load_test.py tests/test_llm_load_test.py
git commit -m "feat(load-test): add payload aggregation"
```

### Task 3: Implement direct mode transport

**Files:**
- Modify: `scripts/llm_load_test.py`
- Modify: `tests/test_llm_load_test.py`

**Step 1: Write the failing tests**

Add tests for:
- direct mode selecting the direct transport path
- direct mode returning per-request result records with status and error fields

**Step 2: Run test to verify it fails**

Run:
`/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_llm_load_test -v`

Expected:
- failures for missing direct runner

**Step 3: Write minimal implementation**

Implement:
- direct request builder
- concurrent direct runner
- minimal remote error extraction for direct-mode reporting

**Step 4: Run test to verify it passes**

Run the same unittest command and confirm the new direct-mode tests pass.

**Step 5: Commit**

```bash
git add scripts/llm_load_test.py tests/test_llm_load_test.py
git commit -m "feat(load-test): add direct mode"
```

### Task 4: Implement dispatcher mode transport

**Files:**
- Modify: `scripts/llm_load_test.py`
- Modify: `tests/test_llm_load_test.py`

**Step 1: Write the failing tests**

Add tests for:
- dispatcher mode selecting dispatcher transport
- dispatcher mode creating and shutting down runtime
- dispatcher mode preserving result fields from dispatcher exceptions

**Step 2: Run test to verify it fails**

Run:
`/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_llm_load_test -v`

Expected:
- failures for missing dispatcher runner

**Step 3: Write minimal implementation**

Implement:
- dispatcher runtime startup/shutdown wrapper
- dispatcher-mode client creation
- concurrent dispatcher request runner

**Step 4: Run test to verify it passes**

Run the same unittest command and confirm dispatcher-mode tests pass.

**Step 5: Commit**

```bash
git add scripts/llm_load_test.py tests/test_llm_load_test.py
git commit -m "feat(load-test): add dispatcher mode"
```

### Task 5: Add CLI, report writing, and smoke verification

**Files:**
- Modify: `scripts/llm_load_test.py`
- Modify: `tests/test_llm_load_test.py`

**Step 1: Write the failing tests**

Add tests for:
- CLI argument parsing
- report file paths under `reports/llm_load_test`
- JSON and JSONL output structure

**Step 2: Run test to verify it fails**

Run:
`/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_llm_load_test -v`

Expected:
- failures for missing CLI and report writing

**Step 3: Write minimal implementation**

Implement:
- full `argparse` CLI
- terminal summary printer
- JSON summary writer
- JSONL detail writer
- `if __name__ == "__main__": main()` entrypoint

**Step 4: Run test to verify it passes**

Run:
`/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_llm_load_test -v`

Expected:
- full green suite

**Step 5: Run smoke checks**

Run:
```bash
/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m py_compile scripts/llm_load_test.py tests/test_llm_load_test.py
/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python scripts/llm_load_test.py --help
```

Expected:
- compile passes
- CLI help renders correctly

**Step 6: Commit**

```bash
git add scripts/llm_load_test.py tests/test_llm_load_test.py
git commit -m "feat(load-test): add reporting cli"
```
