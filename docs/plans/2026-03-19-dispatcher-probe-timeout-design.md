# Dispatcher Probe Timeout Design

## Goal
Raise the dispatcher outage probe timeout ceiling from 30 seconds to 120 seconds so that slow-but-alive servers are less likely to be misclassified as fatal outages.

## Scope
This change only affects the small health probe sent after the outage detector trips. It does not change normal request timeout semantics, retry behavior, or CLI flags.

## Design
- Keep the existing probe payload, max attempts, and metadata unchanged.
- Change the probe timeout calculation in `utils/llm_dispatcher.py` from `min(30.0, max(5.0, trigger_request.timeout_s))` to `min(120.0, max(5.0, trigger_request.timeout_s))`.
- Update the existing unit test to assert the new timeout ceiling.

## Verification
- Run the focused dispatcher unit test for `build_outage_probe_request`.
- Run the full `tests.test_llm_dispatcher` suite.
- Run `py_compile` on the touched files.
