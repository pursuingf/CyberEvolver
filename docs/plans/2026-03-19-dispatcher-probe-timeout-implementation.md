# Dispatcher Probe Timeout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Increase the outage probe timeout ceiling to 120 seconds without changing normal request timeout behavior.

**Architecture:** Adjust the timeout clamp inside `build_outage_probe_request()` and keep the rest of the outage-confirmation flow unchanged. Use the existing dispatcher unit test to drive the change.

**Tech Stack:** Python, unittest

---

### Task 1: Update the failing test

**Files:**
- Modify: `tests/test_llm_dispatcher.py`
- Modify: `utils/llm_dispatcher.py`

**Step 1: Write the failing test**
- Extend `test_build_outage_probe_request_uses_small_payload` to assert `probe.timeout_s == 120.0` when the triggering request timeout is 600 seconds.

**Step 2: Run test to verify it fails**
Run: `/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_llm_dispatcher.LLMDispatcherTests.test_build_outage_probe_request_uses_small_payload -v`
Expected: FAIL because the probe timeout is still clamped at 30 seconds.

**Step 3: Write minimal implementation**
- Update `build_outage_probe_request()` to clamp the timeout at 120 seconds instead of 30 seconds.

**Step 4: Run test to verify it passes**
Run: same unittest command
Expected: PASS

**Step 5: Verify related behavior**
Run:
- `/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_llm_dispatcher -v`
- `/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m py_compile utils/llm_dispatcher.py tests/test_llm_dispatcher.py`
Expected: all pass
