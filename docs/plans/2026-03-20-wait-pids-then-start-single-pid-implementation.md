# Wait PIDs Then Start Single-PID Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the two-PID shell interface with a single-PID interface that forwards the remaining arguments as the command to run after the watched process exits.

**Architecture:** Keep the script's current polling loop and `kill -0` liveness check, but simplify argument parsing to one PID plus a passthrough command array. Drive the change with a subprocess-based regression test that verifies the command runs only after the watched PID exits and that long arguments survive intact.

**Tech Stack:** Bash, Python `unittest`, `subprocess`

---

### Task 1: Add the failing regression test

**Files:**
- Create: `tests/test_wait_pids_then_start_script.py`
- Modify: `scripts/wait_pids_then_start.sh`

**Step 1: Write the failing test**
- Add a unittest that launches `sleep 1`, captures its PID, then runs `scripts/wait_pids_then_start.sh <pid> bash -lc ...`.
- Make the follow-up command write a distinctive long string to a temp file so the test proves argument forwarding works.

**Step 2: Run test to verify it fails**
Run: `/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_wait_pids_then_start_script -v`
Expected: FAIL because the script still requires two PIDs and a `--` separator.

**Step 3: Write minimal implementation**
- Update the script to parse one PID and collect the remaining arguments into `CMD=( "$@" )`.
- Keep the existing wait loop and `exec` behavior.

**Step 4: Run test to verify it passes**
Run: same unittest command
Expected: PASS

**Step 5: Verify touched files are syntactically valid**
Run:
- `bash -n scripts/wait_pids_then_start.sh`
- `/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m py_compile tests/test_wait_pids_then_start_script.py`
Expected: both succeed
