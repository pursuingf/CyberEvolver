# LLM Dispatcher Fatal Outage Design

**Goal:** Detect system-wide upstream LLM server outages inside the centralized dispatcher, fail fast across all waiting callers, and stop the run early so already-completed challenges are easy to distinguish from outage-affected work.

**Scope:** This design covers the dispatcher path rooted at `utils/llm_dispatcher.py` and the top-level run orchestration in `run_evolve_batch_skill.py`.

**Non-goals:**
- changing model prompt construction or challenge logic
- redesigning normal per-request retry behavior for healthy servers
- changing other entrypoints beyond `run_evolve_batch_skill.py`
- building a general external health-check service

## Problem

The centralized dispatcher currently treats each failed LLM request as an isolated request-level problem. That works for transient errors, but it behaves poorly when the upstream model service enters a system-wide bad state.

In the observed `GLM-5-sii` run, the dispatcher stayed alive and continued scheduling requests even after the upstream service had clearly degraded. The result was long periods where requests kept failing or retrying, while the run kept consuming time instead of stopping early.

This creates two operational problems:
- it delays discovery of server-side outages
- it blurs the boundary between challenges that finished normally and challenges that were only still running because the dispatcher kept pushing requests into a broken upstream service

## Evidence From The Observed Run

In `logs/evolution_data/GLM-5-sii/20260311_234008_llm_dispatcher_smoke/dispatcher_metrics.jsonl`, the dispatcher recorded a large volume of non-`200` responses and malformed `200` responses:
- non-`200` event count: `6373`
- `503`: `6351`
- `504`: `22`
- `fail` events with `status_code=200`: `294`
- common malformed `200` error: `ValueError: LLM response missing choices`

This shows that dispatcher process liveness is not enough. The real missing feature is a dispatcher-level outage detector and circuit breaker.

## Desired Behavior

When the upstream LLM service enters a clearly bad state, the dispatcher should:
1. detect that the failures are systemic rather than isolated
2. enter a fatal outage state
3. stop admitting new useful work
4. notify waiting callers quickly instead of letting them time out slowly
5. allow `main()` to stop the run early
6. make it obvious in logs and results which challenges completed before the outage and which were affected by it

## Proposed Architecture

### 1. Dispatcher fatal state

Add a shared fatal state to the dispatcher runtime alongside the existing shared request queue and response store.

Suggested fatal state fields:
- `active: bool`
- `detected_at: str`
- `reason: str`
- `window_summary: dict`
- `last_error_type: str | None`
- `last_status_code: int | None`

Once set, this state means the dispatcher has concluded that the upstream LLM service is in a fatal outage condition.

### 2. Outage detector inside dispatcher main loop

The dispatcher process should maintain a short rolling history of recent request outcomes and evaluate breaker rules after each completed request.

The detector should treat these as breaker-relevant failures:
- HTTP `5xx`
- transport/connect/timeout failures
- malformed successful responses such as `status_code=200` but missing required fields like `choices`

The detector should not treat request-content problems as fatal-outage signals, for example:
- `400`
- request schema errors caused by bad prompts or payloads

### 3. Circuit breaker behavior

Once the breaker trips:
- mark fatal state as active
- append a `fatal_outage` event to dispatcher metrics
- write a high-priority summary line to `run.log`
- stop dispatching new upstream HTTP requests
- fail queued-but-not-yet-dispatched requests with a unified fatal error result
- allow already-running in-flight HTTP calls to finish naturally

This keeps the implementation simple and safe while still stopping the scheduler quickly.

### 4. Fatal error propagation to callers

Introduce a dedicated exception type such as `LLMDispatcherFatalError`.

`LLMClientStub.invoke()` should check fatal state:
- before enqueueing a new request
- during polling while waiting for a response

If fatal state is active, the stub should raise `LLMDispatcherFatalError` immediately instead of continuing to wait for `response_timeout_s`.

This is what turns a dispatcher-side outage decision into fast failure inside challenge workers.

### 5. Run-level fail-fast in `main()`

`run_evolve_batch_skill.py::main()` should treat dispatcher fatal errors as a run-level stop signal.

When encountered:
- preserve already completed challenge results
- stop submitting any remaining pending challenges
- mark unresolved inflight challenge results as failed due to dispatcher fatal outage
- mark never-submitted pending challenges as failed before submission due to dispatcher fatal outage
- log a clear global stop message

This keeps the final challenge boundary readable.

## Breaker Rules

Use a short-window multi-condition breaker. Trigger fatal outage when any one of these conditions becomes true.

### Recommended default rules

1. Recent non-`200` threshold:
- window: `30s`
- trigger when non-`200` failures in the window are `>= 20`

2. Recent total failure threshold:
- window: `30s`
- trigger when total breaker-relevant failures are `>= 30`
- and successful completions in that same window are `<= 0`

3. Consecutive failures threshold:
- trigger when consecutive breaker-relevant completed requests reach `15`

4. Failure-rate threshold:
- window: `60s`
- trigger when failure rate is `>= 90%`
- and the window sample count is `>= 40`

### Why use multiple conditions

A single condition is too brittle:
- pure non-`200` counting misses malformed `200` responses
- pure malformed-response counting misses classic `503/504` storms
- pure fail-rate logic can be noisy at low volume

The combined rule set better matches real outage shapes while still avoiding false positives from one-off transient failures.

## Configuration

Add dispatcher breaker configuration to `run_evolve_batch_skill.py` CLI and pass it into `LLMDispatcherRuntime`.

Suggested parameters:
- `--llm-fatal-window-seconds` default `30`
- `--llm-fatal-non200-threshold` default `20`
- `--llm-fatal-total-fail-threshold` default `30`
- `--llm-fatal-min-success` default `0`
- `--llm-fatal-consecutive-fails` default `15`
- `--llm-fatal-fail-rate-threshold` default `0.9`
- `--llm-fatal-fail-rate-min-samples` default `40`
- `--llm-disable-fatal-breaker` flag

Default behavior should keep the breaker enabled.

## Logging and Observability

### Dispatcher metrics

Add a new dispatcher metric event:
- `event="fatal_outage"`

This event should include:
- breaker reason
- counts from the triggering window
- last error type/status code
- detection timestamp

### Global run log

When the breaker trips, `run.log` should contain an explicit message like:
- `LLM dispatcher fatal outage detected. Stopping the run early.`

Immediately after that, log a short boundary summary such as:
- `completed_before_outage=<n>`
- `inflight_failed_due_to_outage=<n>`
- `pending_failed_before_submission=<n>`

### Caller-visible errors

Fatal outage errors returned to workers should be distinguishable from ordinary request errors. The error message should make it clear that this is a dispatcher-level global stop, not a single-request failure.

## Error Semantics

### Ordinary request failures

If a single request fails but breaker conditions are not met:
- preserve current request-level behavior
- allow retries as today
- return per-request failure to the caller

### Fatal outage failures

If breaker conditions are met:
- return `LLMDispatcherFatalError` to waiting callers
- all subsequent new requests should fail fast with the same fatal error
- `main()` should stop the run rather than continuing to schedule work

## Files To Change

### Primary files
- `utils/llm_dispatcher.py`
- `run_evolve_batch_skill.py`

### Tests
- `tests/test_llm_dispatcher.py`
- `tests/test_run_evolve_batch_skill_guards.py`
- possibly extend scheduler-facing tests if main fail-fast handling needs dedicated coverage

## Testing Strategy

### Unit tests

Add coverage for:
- breaker tripping on a `503` storm
- breaker tripping on malformed `200` responses
- `LLMClientStub.invoke()` failing fast after fatal state becomes active
- `main()` stopping submission after dispatcher fatal error and preserving completed results

### Verification

Run:
- dispatcher unit tests
- main scheduling guard tests
- `python run_evolve_batch_skill.py --help`
- targeted smoke run against a controlled bad endpoint if available

## Risks

### False positives

If thresholds are too sensitive, brief upstream blips could stop the whole run unnecessarily. This is why the design uses windowed, multi-condition thresholds instead of a single-failure trigger.

### In-flight request cleanup

Already-running HTTP requests are not force-cancelled. This is acceptable for the first rollout because the important behavior is preventing new work from piling on.

### Different providers degrade differently

Some providers fail with clean `5xx`, while others return malformed `200` payloads. The detector must treat both as breaker-relevant.

## Success Criteria

This design is successful when:
- the dispatcher detects clear upstream outage patterns quickly
- waiting workers receive a global fatal signal without waiting for long per-request timeouts
- `main()` stops the run early instead of continuing to schedule work into a broken upstream service
- the final results make it easy to see which challenges completed before the outage and which did not
