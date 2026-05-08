# Dispatcher Outage Probe Design

**Goal:** Avoid treating recoverable per-request failures as full upstream outages by confirming server health with a tiny probe before activating fatal outage.

## Problem

In some runs, a single challenge can generate many `504 stream timeout` failures while the remote model server still answers small requests normally. The current outage detector treats repeated `service_unavailable` results as sufficient evidence to trip global fatal outage immediately.

## Desired Behavior

When breaker thresholds are met:
1. Do not activate fatal outage immediately.
2. Send a tiny health probe to the same endpoint/model with a tiny prompt like `who are u?` and a small `max_tokens` value.
3. If the probe succeeds with a structurally valid response, treat the server as healthy enough to continue:
   - cancel fatal activation
   - reset detector counters/history
4. If the probe fails, activate fatal outage as before.

## Scope

Modify only dispatcher behavior in `utils/llm_dispatcher.py` and add targeted tests in `tests/test_llm_dispatcher.py`.

## Design

### Probe request

Build a synthetic `LLMDispatchRequest` that reuses:
- `endpoint`
- `api_key`
- `model`

But overrides request content to a tiny payload:
- short system prompt
- short user prompt (`who are u?`)
- small `max_tokens`
- single attempt
- short timeout
- no large-request delay

### Fatal confirmation flow

Change fatal activation flow from:
- detector trips -> set fatal state immediately

to:
- detector trips -> run tiny probe
- probe success -> reset detector and continue
- probe failure -> set fatal state and drain pending requests

### Observability

Add lightweight dispatcher summary lines for:
- probe starting
- probe success (fatal cancelled, detector reset)
- probe failure (fatal confirmed)

Optionally persist rare probe events in metrics without reintroducing high-volume noise.

## Testing

Add tests for:
1. detector candidate + probe success -> no fatal activation, detector resets
2. detector candidate + probe failure -> fatal activation proceeds
3. reset semantics -> later failures start counting from zero again
