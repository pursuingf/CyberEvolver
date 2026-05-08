# NYUCTFBench Run Script Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn `scripts/run_nyuctfbench.bash` into a usable entrypoint that runs NYU fixed-ID evolve jobs for both `evo` and `raw`, then the two NYU baselines sequentially.

**Architecture:** Keep the script thin and shell-native. Reuse existing Python entrypoints, share one fixed challenge list, expose the main concurrency/model knobs as environment overrides, and stop immediately if any stage fails.

**Tech Stack:** Bash, `run_evolve_batch_skill.py`, `baseline/batch/run_batch_baseline.py`

---

### Task 1: Replace the placeholder script with a serial three-stage runner

**Files:**
- Modify: `scripts/run_nyuctfbench.bash`

**Step 1: Define shared inputs**

- Add the fixed NYU challenge ID list.
- Add defaults for `MODEL`, `RUN_ID_PREFIX`, `CHALLENGE_SERVER_URL`, evolve concurrency, and baseline concurrency.

**Step 2: Build evolve command arguments**

- Reuse `run_evolve_batch_skill.py`.
- Pass the fixed IDs as `--ids` in comma-separated form.
- Keep default LLM dispatcher concurrency around 24.

**Step 3: Build baseline command arguments**

- Reuse `baseline/batch/run_batch_baseline.py`.
- Run `nyuctf_single` first, then `dcipher`.
- Pass the same challenge list and model to both.

**Step 4: Enforce serial stage order**

- Use `set -euo pipefail`.
- Run the four stages in order: `evo`, `raw`, `nyuctf_single`, `dcipher`.
- Let the script stop on the first failing stage.

### Task 2: Add lightweight operator ergonomics

**Files:**
- Modify: `scripts/run_nyuctfbench.bash`

**Step 1: Add banners and command echoing**

- Print a clear stage banner before each command.
- Echo the final command line for easier copy/paste and debugging.

**Step 2: Add a dry-run mode**

- Support `DRY_RUN=1` to print commands without executing them.

### Task 3: Validate the script shape

**Files:**
- Test: `scripts/run_nyuctfbench.bash`

**Step 1: Run shell syntax validation**

Run: `bash -n scripts/run_nyuctfbench.bash`

Expected: no output, exit code 0

**Step 2: Run a dry-run smoke check**

Run: `DRY_RUN=1 bash scripts/run_nyuctfbench.bash`

Expected: four stage banners and four printed Python commands in the correct order
