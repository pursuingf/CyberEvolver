# LLM Dispatcher Fatal Outage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a dispatcher-level outage detector and circuit breaker so `run_evolve_batch_skill.py` stops quickly when the upstream LLM service enters a systemic failure state.

**Architecture:** The implementation extends `utils/llm_dispatcher.py` with a rolling outage detector, a shared fatal state, and a dedicated fatal exception type. `run_evolve_batch_skill.py` then treats that fatal exception as a run-level stop signal so already completed challenges remain clearly separated from outage-affected work.

**Tech Stack:** Python 3.11, `multiprocessing.Manager`, `ProcessPoolExecutor`, `ThreadPoolExecutor`, `httpx`, existing dispatcher metrics/logging, `unittest`

---

### Task 1: Add failing tests for dispatcher breaker thresholds

**Files:**
- Modify: `tests/test_llm_dispatcher.py`
- Reference: `utils/llm_dispatcher.py`

**Step 1: Write the failing test**

Add tests that simulate recent dispatcher outcomes and assert the breaker trips when:
- non-`200` failures exceed the short-window threshold
- malformed `200` results exceed the total-failure threshold

```python
def test_breaker_trips_on_non200_storm(self):
    detector = DispatcherOutageDetector(...)
    for _ in range(20):
        detector.record_failure(status_code=503, error_type="HTTPStatusError")
    assert detector.should_trip()
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_llm_dispatcher -v`
Expected: FAIL because the outage detector does not exist yet.

**Step 3: Write minimal implementation**

Add the smallest outage-detector data structure and rule evaluation needed for the new tests.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_llm_dispatcher -v`
Expected: PASS for the new breaker-threshold tests.

**Step 5: Commit**

```bash
git add tests/test_llm_dispatcher.py utils/llm_dispatcher.py
git commit -m "test(dispatcher): add outage breaker thresholds"
```

### Task 2: Add shared fatal state and fatal exception propagation

**Files:**
- Modify: `utils/llm_dispatcher.py`
- Modify: `tests/test_llm_dispatcher.py`

**Step 1: Write the failing test**

Add tests that verify:
- a fatal outage record can be written into shared state
- `LLMClientStub.invoke()` raises `LLMDispatcherFatalError` before enqueue when fatal is already active
- `LLMClientStub.invoke()` raises `LLMDispatcherFatalError` while polling if fatal becomes active mid-wait

```python
def test_client_stub_fails_fast_when_dispatcher_fatal_state_is_active(self):
    stub = LLMClientStub(..., fatal_state={"active": True, "reason": "503 storm"})
    with self.assertRaises(LLMDispatcherFatalError):
        stub.invoke([...])
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_llm_dispatcher -v`
Expected: FAIL because no fatal shared state or fatal exception exists yet.

**Step 3: Write minimal implementation**

Add:
- `LLMDispatcherFatalError`
- fatal shared state wiring in runtime/handle/stub
- fatal checks in `LLMClientStub.invoke()`

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_llm_dispatcher -v`
Expected: PASS for fatal propagation tests.

**Step 5: Commit**

```bash
git add utils/llm_dispatcher.py tests/test_llm_dispatcher.py
git commit -m "feat(dispatcher): propagate fatal outage state"
```

### Task 3: Trip the breaker inside dispatcher main loop and emit fatal metrics

**Files:**
- Modify: `utils/llm_dispatcher.py`
- Modify: `tests/test_llm_dispatcher.py`

**Step 1: Write the failing test**

Add a test that simulates dispatcher request completions entering an outage pattern and asserts:
- fatal state becomes active
- `fatal_outage` metric event is emitted
- queued requests stop being dispatched normally

```python
def test_dispatcher_emits_fatal_outage_event_when_breaker_trips(self):
    ...
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_llm_dispatcher -v`
Expected: FAIL because the dispatcher loop still only counts failures.

**Step 3: Write minimal implementation**

Integrate outage detection into the dispatcher completion path so that breaker-relevant failures can trip a shared fatal state and emit a fatal metric/log event.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_llm_dispatcher -v`
Expected: PASS for fatal event emission and breaker trip behavior.

**Step 5: Commit**

```bash
git add utils/llm_dispatcher.py tests/test_llm_dispatcher.py
git commit -m "feat(dispatcher): trip breaker on outage patterns"
```

### Task 4: Stop `main()` early when dispatcher fatal errors reach challenge workers

**Files:**
- Modify: `run_evolve_batch_skill.py`
- Modify: `tests/test_run_evolve_batch_skill_guards.py`
- Possibly create: `tests/test_run_evolve_batch_skill_dispatcher_fatal.py`

**Step 1: Write the failing test**

Add coverage for run-level fail-fast behavior where:
- some challenge results already completed successfully
- one challenge result surfaces dispatcher fatal outage
- remaining inflight and pending challenges are marked as outage-affected
- no new submissions happen after fatal detection

```python
def test_main_stops_scheduling_after_dispatcher_fatal_error(self):
    ...
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_evolve_batch_skill_guards -v`
Expected: FAIL because `main()` does not yet distinguish dispatcher fatal outage from ordinary failures.

**Step 3: Write minimal implementation**

Teach `run_evolve_batch_skill.py` to detect dispatcher fatal errors in challenge results or exception text and convert them into a global stop path that preserves already completed results and marks remaining work clearly.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_run_evolve_batch_skill_guards -v`
Expected: PASS for dispatcher fatal run-level fail-fast behavior.

**Step 5: Commit**

```bash
git add run_evolve_batch_skill.py tests/test_run_evolve_batch_skill_guards.py tests/test_run_evolve_batch_skill_dispatcher_fatal.py
git commit -m "fix(scheduler): fail fast on dispatcher outage"
```

### Task 5: Add CLI configuration for breaker thresholds

**Files:**
- Modify: `run_evolve_batch_skill.py`
- Modify: `tests/test_run_evolve_batch_skill_guards.py` or dedicated CLI/config tests

**Step 1: Write the failing test**

Add a test that verifies the new CLI arguments are parsed and passed into `LLMDispatcherRuntime` correctly.

```python
def test_dispatcher_breaker_cli_args_are_wired_into_runtime(self):
    ...
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_run_evolve_batch_skill_guards -v`
Expected: FAIL because runtime configuration does not yet include fatal-breaker parameters.

**Step 3: Write minimal implementation**

Add the new CLI args and plumb them into `LLMDispatcherRuntime` and the dispatcher process configuration.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_run_evolve_batch_skill_guards -v`
Expected: PASS for CLI/runtime wiring.

**Step 5: Commit**

```bash
git add run_evolve_batch_skill.py tests/test_run_evolve_batch_skill_guards.py utils/llm_dispatcher.py
git commit -m "feat(dispatcher): add outage breaker config"
```

### Task 6: Add regression coverage for malformed `200` responses

**Files:**
- Modify: `tests/test_llm_dispatcher.py`
- Reference: `utils/llm_dispatcher.py`

**Step 1: Write the failing test**

Add a focused test asserting that repeated `status_code=200` responses missing `choices` count toward breaker-relevant failures and can trigger fatal outage.

```python
def test_malformed_200_responses_trip_breaker(self):
    ...
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_llm_dispatcher -v`
Expected: FAIL if malformed `200` responses are still treated as only isolated request failures.

**Step 3: Write minimal implementation**

Ensure malformed `200` results are classified as breaker-relevant failures inside dispatcher outage accounting.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_llm_dispatcher -v`
Expected: PASS for malformed `200` breaker coverage.

**Step 5: Commit**

```bash
git add utils/llm_dispatcher.py tests/test_llm_dispatcher.py
git commit -m "test(dispatcher): count malformed 200 outages"
```

### Task 7: Run full verification and smoke checks

**Files:**
- Modify: none
- Verify: `utils/llm_dispatcher.py`, `run_evolve_batch_skill.py`

**Step 1: Run unit tests**

Run:
```bash
python -m unittest \
  tests.test_llm_dispatcher \
  tests.test_run_evolve_batch_skill_guards \
  tests.test_run_evolve_batch_skill_scheduler \
  tests.test_worker_diagnostics \
  tests.test_refiner_unicode_validation -v
```
Expected: PASS

**Step 2: Run syntax verification**

Run:
```bash
python -m py_compile \
  utils/llm_dispatcher.py \
  run_evolve_batch_skill.py \
  tests/test_llm_dispatcher.py \
  tests/test_run_evolve_batch_skill_guards.py
```
Expected: PASS with no output

**Step 3: Run CLI smoke test**

Run:
```bash
python run_evolve_batch_skill.py --help
```
Expected: help text prints successfully and includes the new breaker arguments

**Step 4: Optional targeted outage smoke test**

If a controlled bad endpoint is available, run a short test that forces repeated `503` or malformed `200` responses and verify:
- dispatcher emits `fatal_outage`
- workers fail fast with dispatcher fatal error
- `main()` stops scheduling quickly

**Step 5: Commit**

```bash
git add docs/plans/2026-03-12-llm-dispatcher-fatal-outage-design.md docs/plans/2026-03-12-llm-dispatcher-fatal-outage-implementation.md
git commit -m "docs(dispatcher): add fatal outage plan"
```
