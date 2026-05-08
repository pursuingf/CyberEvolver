# Run Script Helper Extraction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract the shared ctf run-script helper logic from `scripts/run_cvebench.bash` and `scripts/run_nyuctfbench.bash` into one reusable shell helper file without changing validated behavior.

**Architecture:** Create `scripts/lib/challenge_run_helpers.sh` as a sourced shell library that contains only shared runtime helpers. Keep benchmark-specific defaults and stage orchestration inside each entry script, and preserve the existing environment-variable contract so current invocations continue to work.

**Tech Stack:** Bash, `unittest`, shell syntax checks

---

### Task 1: Add a failing helper-sourcing regression test

**Files:**
- Create: `tests/test_run_script_helpers.py`
- Test: `tests/test_run_script_helpers.py`

**Step 1: Write the failing test**

Add tests that:
- require `scripts/lib/challenge_run_helpers.sh` to exist
- require both run scripts to source it
- require the helper file to be sourceable and expose `run_cmd` and `start_challenge_server`

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_script_helpers -v`
Expected: FAIL because the helper file does not exist yet.

**Step 3: Write minimal implementation**

No implementation in this task.

**Step 4: Run test again to confirm the failure is for the intended reason**

Run: `python -m unittest tests.test_run_script_helpers -v`
Expected: FAIL on missing helper file or missing source statements.

### Task 2: Extract shared helper functions into one library

**Files:**
- Create: `scripts/lib/challenge_run_helpers.sh`
- Modify: `scripts/run_cvebench.bash`
- Modify: `scripts/run_nyuctfbench.bash`

**Step 1: Create the helper library**

Move the following shared functions into `scripts/lib/challenge_run_helpers.sh`:
- `normalize_namespace_part`
- `banner`
- `run_cmd`
- `parse_url_host_port`
- `challenge_server_ready`
- `port_is_in_use`
- `find_available_port`
- `wait_for_challenge_server`
- `cleanup_challenge_server`
- `start_challenge_server`

**Step 2: Source the helper file from each run script**

In both entry scripts:
- compute `SCRIPT_DIR`
- source `scripts/lib/challenge_run_helpers.sh`
- keep benchmark-specific variables and stage logic local

**Step 3: Preserve current behavior**

Do not change:
- port conflict behavior
- `Ctrl-C` handling behavior
- namespace semantics already validated in the current scripts
- baseline/evolve stage ordering

**Step 4: Run syntax checks**

Run: `bash -n scripts/lib/challenge_run_helpers.sh scripts/run_cvebench.bash scripts/run_nyuctfbench.bash`
Expected: PASS

### Task 3: Verify helper extraction without regressions

**Files:**
- Test: `tests/test_run_script_helpers.py`
- Test: `tests/test_run_cvebench_script.py`
- Test: `tests/test_run_nyuctfbench_script.py`
- Test: `tests/test_wait_pids_then_start_script.py`
- Test: `tests/test_challenge_server_script.py`

**Step 1: Run the new helper test**

Run: `python -m unittest tests.test_run_script_helpers -v`
Expected: PASS

**Step 2: Run the focused run-script regression suite**

Run: `python -m unittest tests.test_run_script_helpers tests.test_run_cvebench_script tests.test_run_nyuctfbench_script -v`
Expected: PASS

**Step 3: Run the broader script-related suite**

Run: `python -m unittest tests.test_run_script_helpers tests.test_run_cvebench_script tests.test_run_nyuctfbench_script tests.test_wait_pids_then_start_script tests.test_challenge_server_script -v`
Expected: PASS

**Step 4: Commit**

```bash
git add scripts/lib/challenge_run_helpers.sh scripts/run_cvebench.bash scripts/run_nyuctfbench.bash tests/test_run_script_helpers.py docs/plans/2026-04-14-run-script-helper-extraction-implementation.md
git commit -m "refactor(scripts): extract shared run helpers"
```
