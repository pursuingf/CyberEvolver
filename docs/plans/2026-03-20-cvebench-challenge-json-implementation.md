# CVE Bench Challenge JSON Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Generate all CVE Bench `challenge.json` files in the current benchmark layout using a flat prompt-facing schema without absolute paths.

**Architecture:** Update the existing `cvebench_layout.py` generator instead of creating a second path. Keep launch/runtime fields compatible with `ChallengeJsonAdapter`, but replace copied prompt blobs with flattened fields extracted from `eval.yml` and `metadata.yml`.

**Tech Stack:** Python, YAML parsing, JSON generation, unittest

---

### Task 1: Update tests for the new layout contract

**Files:**
- Modify: `.worktrees/cvebench-network-scorer/tests/test_cvebench_layout.py`
- Modify: `.worktrees/cvebench-network-scorer/tests/test_benchmark_adapters.py`

**Step 1: Write the failing test**

Update the layout tests to expect:
- nested challenge paths under `cvebench/critical/challenges/<CVE>`
- no copied `prompt_variants`
- flattened prompt fields such as `application_service_keys`
- relative paths only

Add a multi-service assertion for a challenge fixture that exercises split endpoint extraction.

**Step 2: Run test to verify it fails**

Run the focused CVE Bench layout tests and confirm they fail against the old generator behavior.

**Step 3: Write minimal implementation**

Do not touch production code yet.

**Step 4: Run test to verify it passes**

This step is expected to remain red until Task 2 is complete.

**Step 5: Commit**

Commit after the generator catches up and the updated tests pass.

### Task 2: Rewrite the CVE Bench generator for the new schema

**Files:**
- Modify: `.worktrees/cvebench-network-scorer/benchmark_adapters/cvebench_layout.py`

**Step 1: Write the failing test**

Use the updated tests from Task 1 as the red bar.

**Step 2: Run test to verify it fails**

Run the focused layout tests and confirm:
- old output paths are wrong
- old prompt fields are still present
- flattened fields are missing

**Step 3: Write minimal implementation**

Update the generator to:
- write `challenge.json` into `benchmarks/cvebench/critical/challenges/<CVE>`
- extract flat prompt-facing fields
- keep all stored paths relative
- remove copied `metadata` and `prompt_variants`

**Step 4: Run test to verify it passes**

Run the focused layout tests until they pass.

**Step 5: Commit**

Commit the generator rewrite and passing tests.

### Task 3: Keep adapter scoring compatible

**Files:**
- Modify: `.worktrees/cvebench-network-scorer/benchmark_adapters/challenge_json.py`
- Test: `.worktrees/cvebench-network-scorer/tests/test_benchmark_adapters.py`

**Step 1: Write the failing test**

Add an adapter test proving runtime scoring still resolves the CVE Bench scorer target and port from the new flattened fields.

**Step 2: Run test to verify it fails**

Run the focused adapter test and confirm the old `metadata`-based logic no longer works.

**Step 3: Write minimal implementation**

Update runtime scoring derivation to use:
- `proof_upload_service_key`
- `proof_upload_endpoint_suffix`
- fallback application fields

**Step 4: Run test to verify it passes**

Run the focused adapter tests until they pass.

**Step 5: Commit**

Commit the adapter compatibility fix.

### Task 4: Generate all challenge files and verify the batch output

**Files:**
- Modify: `.worktrees/cvebench-network-scorer/benchmarks/cvebench/critical/challenges/*/challenge.json`

**Step 1: Write the failing test**

Prepare a batch verification command that checks every index entry has a sibling `challenge.json` and that none of the generated JSON files contain absolute paths.

**Step 2: Run test to verify it fails**

Run the batch verification before regeneration and confirm missing or stale files are detected.

**Step 3: Write minimal implementation**

Run the updated generator to rewrite all CVE Bench `challenge.json` files.

**Step 4: Run test to verify it passes**

Run:
- focused layout tests
- focused adapter tests
- batch verification script

**Step 5: Commit**

Commit the generated challenge files and supporting code once verification is green.
