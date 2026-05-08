# Dispatcher Fatal Propagation Design

## Goal
Ensure a dispatcher fatal outage stops the run promptly even when individual evolve components would otherwise swallow request-level errors.

## Problem
The dispatcher now detects fatal upstream outages, but challenge-local components such as the log analyzer, refiner, and orchestrator still catch `LLMDispatcherFatalError` as a generic `Exception`. That lets a challenge finish as `extinct` or `failed` without surfacing `fatal_outage=True` to `main()`. As a result, `main()` may continue submitting new challenges after the dispatcher has already declared a fatal outage.

## Design
1. Propagate fatal errors through evolve components.
   - In `evolve/loganalyzer.py`, `evolve/refiner_agent.py`, and `evolve/orchestrator.py`, catch `LLMDispatcherFatalError` separately and immediately re-raise it.
   - Keep existing generic exception handling for non-fatal local failures.

2. Add a run-level dispatcher fatal guard in `main()`.
   - After each completed future and before each refill submission, inspect dispatcher shared fatal state directly.
   - If fatal is active, stop further submissions and mark remaining inflight/pending challenges via the existing global dispatcher-fatal guard path.

## Error Semantics
- A challenge that directly surfaces `LLMDispatcherFatalError` still returns `fatal_outage=True`.
- If a challenge returns a non-fatal result after dispatcher fatal has already been declared, `main()` still stops the run and treats remaining inflight/pending challenges as outage-affected.
- Already completed challenges remain intact.

## Testing
- Add a failing regression test showing evolve components re-raise `LLMDispatcherFatalError` instead of swallowing it.
- Add a failing regression test for a new `main`-level fatal guard helper that stops scheduling when fatal state is active even if the current challenge result is not flagged `fatal_outage`.
