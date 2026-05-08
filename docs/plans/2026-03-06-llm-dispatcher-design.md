# LLM Dispatcher Design

**Goal:** Introduce a single cross-process, cross-thread LLM dispatcher for `run_evolve_batch_skill.py` so all model requests flow through one fairness-controlled scheduler instead of each worker calling the model endpoint directly.

**Scope:** This design only covers the production path rooted at `run_evolve_batch_skill.py`. The first rollout includes:
- the main agent loop through the managed `llm` object passed into dynamic agent code
- `evolve/refiner_agent.py`
- `evolve/orchestrator.py`
- `evolve/loganalyzer.py`

**Non-goals:**
- changing `run_evolve_batch.py` or `run_sequential_evolve.py`
- introducing an external service or remote broker
- rewriting prompt construction logic in `evolve/`

## Problem

Today the code creates LLM clients inside challenge workers and then fans out requests from multiple nested thread pools. Even when requests are instrumented, the concurrency shaping is still distributed. This leads to bursty traffic, layered retries, and poor visibility into which challenge or component is consuming model capacity.

The real requirement is a central "water filter": one process that sees every request, meters them fairly, retries consistently, and records usage in one place.

## Proposed Architecture

### 1. Main-process dispatcher

`run_evolve_batch_skill.py` starts one `LLMDispatcherProcess` before the `ProcessPoolExecutor` is created. This dispatcher owns:
- a shared request queue
- a shared response store
- a scheduler loop
- a small transport worker pool for actual network calls

Every challenge worker process receives a lightweight `LLMClientStub` that only knows how to submit requests to the dispatcher and wait for the response.

### 2. Request flow

1. Caller invokes `llm.invoke(messages)`.
2. `InstrumentedLLM` merges request metadata such as `chal_id`, `node_id`, `sample_id`, and `component`.
3. If the inner client supports dispatch metadata, `InstrumentedLLM` forwards that metadata to the dispatcher stub.
4. The stub serializes the message payload and enqueues an `LLMRequest`.
5. The dispatcher schedules the request using fairness rules.
6. A transport worker performs the OpenAI-compatible HTTP request via `httpx`.
7. The dispatcher writes a serialized `LLMResponse` into the shared response store.
8. The stub returns the response to the original caller.

### 3. Fairness model

The dispatcher groups requests by lane. The initial lane key is `chal_id`, with a fallback lane of `global` when challenge metadata is unavailable.

Scheduling policy:
- global in-flight limit
- per-lane in-flight limit
- round-robin selection across non-empty eligible lanes

This prevents one challenge or one internal fan-out stage from saturating the model endpoint.

### 4. Retry and error handling

Retry must move into the dispatcher transport layer. `utils/util.py::llm_invoke` should become a thin pass-through to avoid stacked retries.

Dispatcher retry policy:
- retry network errors
- retry HTTP `429`
- retry HTTP `5xx`
- do not retry malformed request errors such as `400`
- exponential backoff with jitter

Errors are returned to callers in a structured form and re-raised by the stub as ordinary Python exceptions.

### 5. Response compatibility

The dispatcher returns a small picklable response object with:
- `content`
- `usage_metadata`
- `response_metadata`

This preserves compatibility with the existing `InstrumentedLLM` wrapper and any code expecting `response.content`.

## Files to Add or Change

### New files

- `utils/llm_dispatcher.py`
- `tests/test_llm_dispatcher.py`

### Modified files

- `run_evolve_batch_skill.py`
- `utils/util.py`
- `utils/llm_usage.py`

## Design Decisions

### Why a local process instead of a remote broker

The user wants a central scheduler, not a bigger deployment surface. A local dispatcher process gives us the control-plane behavior we want without adding service management, network hops, or infrastructure dependencies.

### Why keep `InstrumentedLLM`

`InstrumentedLLM` already solves the bookkeeping layer well enough:
- JSONL usage logs
- cross-process token budget
- metadata scoping

Replacing the transport underneath it is lower risk than replacing the whole instrumentation stack.

### Why not keep LangChain in the transport path

The scheduler needs explicit control over serialization, retry boundaries, error classification, and wire behavior. A direct `httpx` OpenAI-compatible client keeps that logic inside our codebase where we can reason about it.

## Risks

### Manager proxy overhead

A shared queue and response store will add some overhead, but LLM round-trip latency dominates this cost. The trade-off is worthwhile for centralized control in the first rollout.

### Dead request cleanup

If a worker dies while waiting, the dispatcher may still finish the request. The dispatcher should timestamp completed responses and periodically reap stale entries.

### Layered retries still exist in agent code

Dynamic agent code still contains local retry wrappers. The first rollout will remove util-layer retries and centralize transport retries, but some higher-level retries may remain. That is acceptable for the first cut, and we can flatten them later if needed.

## Success Criteria

- All LLM calls in the `run_evolve_batch_skill.py` production path route through one dispatcher process.
- Concurrency at the model endpoint is bounded centrally.
- Requests from different challenges are interleaved fairly instead of bursting from a single challenge.
- Existing instrumentation and token budget reporting continue to work.
- Existing callers still use `llm.invoke(...)` and receive `response.content`.
