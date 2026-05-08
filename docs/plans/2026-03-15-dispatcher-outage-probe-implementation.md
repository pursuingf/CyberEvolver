# Dispatcher Outage Probe Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Confirm upstream outage with a tiny probe before triggering global fatal outage.

**Architecture:** Keep the existing outage detector thresholds, but interpose a tiny health probe before fatal activation. A successful probe proves the server is still responsive and resets detector state; a failed probe confirms outage and preserves current fatal behavior.

**Tech Stack:** Python, unittest, dispatcher transport helpers in `utils/llm_dispatcher.py`

---

### Task 1: Add failing tests for probe-confirmed fatal logic

**Files:**
- Modify: `tests/test_llm_dispatcher.py`
- Modify: `utils/llm_dispatcher.py`

**Step 1: Write the failing test**
- Add a test that simulates a detector trip candidate and a successful probe.
- Assert fatal is not activated and detector state resets.
- Add a second test where the probe fails and fatal activation proceeds.

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_llm_dispatcher -v`
Expected: FAIL because probe helpers/reset behavior do not exist yet.

**Step 3: Write minimal implementation**
- Add a detector reset method.
- Add helper to build and run tiny probe requests.
- Route fatal activation through probe confirmation.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_llm_dispatcher -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_llm_dispatcher.py utils/llm_dispatcher.py docs/plans/2026-03-15-dispatcher-outage-probe-design.md docs/plans/2026-03-15-dispatcher-outage-probe-implementation.md
git commit -m "fix(dispatcher): confirm outage with probe"
```
