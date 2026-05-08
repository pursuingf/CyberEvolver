# Dispatcher Fatal Propagation Implementation Plan

1. Add failing regression tests for fatal propagation through log analyzer and for main-level fatal stop handling.
2. Update evolve components to re-raise `LLMDispatcherFatalError` instead of swallowing it as a generic exception.
3. Add a shared helper for `main()` to stop scheduling when dispatcher fatal state is active, then wire it into the result-collection loop and refill boundary.
4. Run targeted tests, full regression tests, syntax checks, and CLI help verification.
