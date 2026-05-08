# LLM Dispatcher Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local cross-process LLM dispatcher for `run_evolve_batch_skill.py` that centrally schedules, retries, and meters all model traffic.

**Architecture:** A main-process dispatcher accepts requests from worker stubs over shared multiprocessing primitives, applies round-robin lane scheduling by `chal_id`, and executes OpenAI-compatible HTTP requests through a bounded transport pool. Existing instrumentation stays in place by wrapping the dispatcher stub with `InstrumentedLLM`.

**Tech Stack:** Python multiprocessing, threading, `httpx`, existing `InstrumentedLLM` and `FileTokenBudget`

---

### Task 1: Add dispatcher tests

**Files:**
- Create: `tests/test_llm_dispatcher.py`

**Step 1: Write the failing tests**

Cover:
- message serialization from dict and LangChain-style messages
- round-robin lane scheduling fairness
- dispatcher stub returning a compatible response object
- transport retry classification behavior through a fake transport

**Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_llm_dispatcher.py -q`
Expected: FAIL because `utils.llm_dispatcher` does not exist yet

### Task 2: Implement dispatcher core

**Files:**
- Create: `utils/llm_dispatcher.py`

**Step 1: Add request and response models**

Implement picklable dataclasses for:
- request payload
- serialized response payload
- compatibility response object

**Step 2: Add message serialization**

Support:
- `[{role, content}]`
- LangChain `SystemMessage`, `HumanMessage`, `AIMessage`

**Step 3: Add scheduler state**

Implement:
- lane queues
- round-robin lane order
- global and per-lane in-flight counters

**Step 4: Add HTTP transport**

Implement an OpenAI-compatible `/chat/completions` client with:
- timeout
- retryable error classification
- exponential backoff with jitter
- usage extraction

**Step 5: Add dispatcher process loop and client stub**

Implement:
- request intake
- response publishing
- stop signal handling
- blocking stub `invoke`

### Task 3: Wire metadata through instrumentation

**Files:**
- Modify: `utils/llm_usage.py`

**Step 1: Write a failing test or extend the dispatcher tests**

Assert that dispatch metadata such as `chal_id` can be forwarded from `InstrumentedLLM` into the underlying stub.

**Step 2: Update `InstrumentedLLM.invoke`**

If the wrapped client advertises support for dispatch metadata, pass the merged scoped metadata into the client call.

### Task 4: Integrate dispatcher into `run_evolve_batch_skill.py`

**Files:**
- Modify: `run_evolve_batch_skill.py`

**Step 1: Start dispatcher in main process**

Create dispatcher runtime before the challenge process pool starts.

**Step 2: Replace direct `ChatOpenAI` construction**

Build managed base and mutation LLM stubs from the dispatcher runtime instead.

**Step 3: Ensure cleanup**

Stop the dispatcher process during normal shutdown and on interrupts.

### Task 5: Remove util-layer retry

**Files:**
- Modify: `utils/util.py`

**Step 1: Remove tenacity from `llm_invoke`**

Make it a thin wrapper over `llm.invoke(...)`.

**Step 2: Verify evolve callers still work**

No call-site behavior change should be required in:
- `evolve/refiner_agent.py`
- `evolve/orchestrator.py`
- `evolve/loganalyzer.py`

### Task 6: Verify

**Files:**
- Test: `tests/test_llm_dispatcher.py`

**Step 1: Run targeted tests**

Run: `pytest tests/test_llm_dispatcher.py -q`
Expected: PASS

**Step 2: Run import-level smoke check**

Run: `python -m py_compile run_evolve_batch_skill.py utils/llm_dispatcher.py utils/llm_usage.py utils/util.py`
Expected: no output

**Step 3: Optional runtime smoke**

Run: `python run_evolve_batch_skill.py --help`
Expected: CLI help output without import errors
