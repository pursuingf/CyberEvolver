# Run CVEBench Script Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expand `scripts/run_cvebench.bash` into a full CVEBench experiment driver that runs both baseline and evolve jobs across both prompt variants, with challenge_server auto-start and per-run model-scoped namespace isolation.

**Architecture:** Rework `scripts/run_cvebench.bash` to follow the same staged orchestration pattern as `scripts/run_nyuctfbench.bash`: normalize config from environment, optionally start or reuse a challenge_server instance, then run experiment stages sequentially. Keep the implementation shell-only and use helper functions for URL parsing, challenge_server readiness checks, port conflict handling, and namespace normalization.

**Tech Stack:** Bash, Python helpers embedded in the script, `baseline/batch/run_batch_baseline.py`, `run_evolve_batch_skill.py`, `bench_hub/server/challenge_server.py`, `unittest`

---

### Task 1: Add a failing dry-run regression test for the CVEBench script

**Files:**
- Create: `tests/test_run_cvebench_script.py`
- Test: `tests/test_run_cvebench_script.py`

**Step 1: Write the failing test**

```python
def test_dry_run_orders_baselines_first_and_uses_model_scoped_namespace(self):
    result = subprocess.run([...], env={...}, capture_output=True, text=True)
    self.assertEqual(result.returncode, 0)
    self.assertIn("Stage 1/6: cy_agent baseline zero_day", result.stdout)
    self.assertIn("Stage 6/6: evo one_day", result.stdout)
    self.assertIn("max-workers '24'", result.stdout)
    self.assertIn("step-limit '30'", result.stdout)
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_cvebench_script -v`
Expected: FAIL because the current script has only four stages and no dry-run orchestration.

**Step 3: Write minimal implementation**

No implementation in this task.

**Step 4: Run test to verify it still fails for the intended reason**

Run: `python -m unittest tests.test_run_cvebench_script -v`
Expected: FAIL with missing stage or namespace assertions.

### Task 2: Rebuild `run_cvebench.bash` around staged orchestration

**Files:**
- Modify: `scripts/run_cvebench.bash`
- Reference: `scripts/run_nyuctfbench.bash`

**Step 1: Preserve the current benchmark defaults but move them into env-overridable variables**

Use variables for:
- Python entrypoints
- benchmark/model/run-id prefix
- challenge_server settings
- evolve concurrency knobs
- baseline concurrency knobs
- prompt variant and dry-run toggles

**Step 2: Add shared shell helpers**

Implement:
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

These helpers should let the script:
- reuse an already-running matching challenge_server
- auto-pick a new port when the requested port is occupied by something else
- derive `CTF_NAMESPACE` from `RUN_ID_PREFIX` and `MODEL`

**Step 3: Add six sequential stages with baseline first**

Stages:
1. `cy_agent baseline zero_day`
2. `cy_agent baseline one_day`
3. `raw zero_day`
4. `evo zero_day`
5. `raw one_day`
6. `evo one_day`

Use:
- baseline `--max-workers 24`
- baseline `--step-limit 30`
- evolve `--llm-max-inflight 24`
- full benchmark runs with no allowlist filtering

**Step 4: Keep output compact but explicit**

Print:
- chosen namespace
- resolved challenge_server URL
- final completion summary

**Step 5: Run shell syntax validation**

Run: `bash -n scripts/run_cvebench.bash`
Expected: PASS

### Task 3: Verify the new script end-to-end in dry-run mode

**Files:**
- Test: `tests/test_run_cvebench_script.py`

**Step 1: Run the new test**

Run: `python -m unittest tests.test_run_cvebench_script -v`
Expected: PASS

**Step 2: Run related script regressions**

Run: `python -m unittest tests.test_wait_pids_then_start_script tests.test_challenge_server_script -v`
Expected: PASS

**Step 3: Run the combined validation set**

Run: `python -m unittest tests.test_run_cvebench_script tests.test_wait_pids_then_start_script tests.test_challenge_server_script -v`
Expected: PASS

**Step 4: Commit**

```bash
git add scripts/run_cvebench.bash tests/test_run_cvebench_script.py docs/plans/2026-04-14-run-cvebench-script-implementation.md
git commit -m "feat(scripts): expand cvebench runner"
```
