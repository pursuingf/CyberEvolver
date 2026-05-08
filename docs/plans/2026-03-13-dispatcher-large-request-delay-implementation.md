# Dispatcher Large Request Delay Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add configurable large-request classification plus a fixed pre-send delay for those requests, with lightweight monitoring in dispatcher summaries.

**Architecture:** Extend `LLMDispatchRequest` with estimated token metadata, calculate it in the dispatcher client stub, apply delay in `_perform_http_request`, and surface large-request counters in metrics and summary logs.

**Tech Stack:** Python, `utils/llm_dispatcher.py`, `run_evolve_batch_skill.py`, `unittest`

---

### Task 1: Add failing dispatcher tests
- Modify: `tests/test_llm_dispatcher.py`
- Cover: large-request estimation metadata, large-request delay before HTTP post, summary rendering of large-request counters.

### Task 2: Implement dispatcher large-request delay
- Modify: `utils/llm_dispatcher.py`
- Add estimated token helpers, request metadata fields, conditional `sleep`, and summary/metric enrichment.

### Task 3: Wire CLI parameters to runtime
- Modify: `run_evolve_batch_skill.py`
- Add threshold/delay args and pass them into `LLMDispatcherRuntime`.

### Task 4: Verify
- Run: `python -m unittest tests.test_llm_dispatcher -v`
- Run: `python -m py_compile utils/llm_dispatcher.py run_evolve_batch_skill.py tests/test_llm_dispatcher.py`
