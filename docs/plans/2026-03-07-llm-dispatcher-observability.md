# LLM Dispatcher Observability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add structured dispatcher process metrics and low-frequency scheduler summaries for the `run_evolve_batch_skill.py` production path.

**Architecture:** Extend the local dispatcher process so it emits JSONL metric events for request lifecycle stages and periodically appends compact summary lines to the run log. Keep existing `llm_usage.jsonl` unchanged and isolate scheduling observability in dispatcher-owned outputs.

**Tech Stack:** Python multiprocessing, threading, JSONL file output, existing dispatcher runtime

---

### Task 1: Add failing observability tests

**Files:**
- Modify: `tests/test_llm_dispatcher.py`

**Step 1: Write failing tests**
- Add a scheduler snapshot test covering pending and inflight counters by lane.
- Add a metric record formatting test covering request metadata, queue depth, inflight counts, and attempt info.
- Add a dispatcher summary formatting test covering totals and top-lane rendering.

**Step 2: Run test to verify it fails**
Run: `/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_llm_dispatcher -v`
Expected: FAIL because the new snapshot / metric / summary helpers do not exist yet.

### Task 2: Implement dispatcher observability helpers

**Files:**
- Modify: `utils/llm_dispatcher.py`

**Step 1: Add scheduler snapshot support**
- Expose a pure snapshot method with pending and inflight lane counters.

**Step 2: Add metric and summary helpers**
- Add helpers to build lifecycle metric records.
- Add helpers to format compact dispatcher summary log lines.

**Step 3: Extend dispatcher transport result**
- Record attempt count, latency, and final status data in `LLMDispatchResult`.

### Task 3: Emit metrics from dispatcher runtime

**Files:**
- Modify: `utils/llm_dispatcher.py`
- Modify: `run_evolve_batch_skill.py`

**Step 1: Add dispatcher-owned output paths**
- Create `dispatcher_metrics.jsonl` in the run root.
- Reuse the existing run log path for low-frequency dispatcher summary lines.

**Step 2: Emit lifecycle events**
- Emit `enqueue`, `dispatch`, `retry`, `complete`, and `fail` records.
- Include request metadata such as `chal_id`, `component`, `lane`, `model`, and `request_id`.

**Step 3: Emit low-frequency summaries**
- Every ~30 seconds append a compact scheduler summary to `run.log`.

### Task 4: Verify

**Files:**
- Test: `tests/test_llm_dispatcher.py`

**Step 1: Run targeted tests**
Run: `/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_llm_dispatcher -v`
Expected: PASS

**Step 2: Run compile smoke**
Run: `python -m py_compile run_evolve_batch_skill.py utils/llm_dispatcher.py tests/test_llm_dispatcher.py`
Expected: no output
