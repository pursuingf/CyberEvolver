# LLM Load Test Design

**Goal:** Add a single load-test script that can stress any model defined in `configs/model.yml` in either direct or dispatcher mode, using roughly 100k-character inputs at concurrency 24, and produce clear throughput and error-frequency reports.

## Context

The current repository has production-grade request dispatching in [utils/llm_dispatcher.py](/data/pxd-team/workspace/fyh/evolve_ctf_agent/utils/llm_dispatcher.py), but it does not have a lightweight tool for directly comparing raw upstream model behavior against dispatcher-mediated behavior. The user needs a focused stress tool that can answer operational questions such as:

- How much throughput does a given model endpoint sustain at concurrency 24?
- What failure modes appear under large-input load?
- How do those failure modes differ between direct requests and dispatcher-mediated requests?
- Are failures dominated by connection resets, 5xx, rate limits, or request-invalid errors like context length overflow?

The tool should stay independent from `run_evolve_batch_skill.py` so that challenge execution, target lifecycle, token budgets, and evolution logic do not contaminate measurements.

## Options Considered

### Option 1: One script with `--mode direct|dispatcher` (recommended)

Create a single script, `scripts/llm_load_test.py`, with one CLI surface and a mode switch.

Pros:
- Shared payload generation and shared reporting keep comparisons fair.
- One report format for both modes.
- Lowest long-term maintenance overhead.

Cons:
- The script contains a small amount of mode branching.

### Option 2: One script with subcommands

Use `scripts/llm_load_test.py direct ...` and `scripts/llm_load_test.py dispatcher ...`.

Pros:
- Slightly cleaner UX for mode-specific help.

Cons:
- More CLI plumbing without meaningful functional benefit.
- Same implementation complexity as `--mode`, but a heavier interface.

### Option 3: Two separate scripts

Create `scripts/llm_load_test_direct.py` and `scripts/llm_load_test_dispatcher.py`.

Pros:
- Very explicit separation.

Cons:
- Higher duplication risk.
- Drift between reporting and payload generation becomes likely.
- Worse for side-by-side operational comparisons.

**Recommendation:** Option 1. It keeps the comparison fair and the code footprint small.

## Architecture

The script will have five layers:

1. **Config loading**
   - Read `configs/model.yml`.
   - Resolve one model config by `--model-config-name`.
   - Preserve request parameters from the chosen model entry.

2. **Payload generation**
   - Build one deterministic large payload using a fixed seed and target size.
   - Default payload length: 100,000 characters.
   - Use mostly ASCII structured random text to avoid Unicode-related variability.
   - Reuse the same payload across all requests in a run to keep the test focused on service behavior.

3. **Transport layer**
   - `direct` mode: send OpenAI-compatible `/chat/completions` requests directly.
   - `dispatcher` mode: start a local `LLMDispatcherRuntime`, create a client via `handle.build_client(...)`, and send the same messages through dispatcher.

4. **Concurrent runner**
   - Use a fixed worker pool sized by `--concurrency`.
   - Default concurrency: 24.
   - Execute `--num-requests` logical requests.
   - Record start time, finish time, latency, status, error type, error kind, and a short error summary per request.

5. **Reporting**
   - Print a concise terminal summary.
   - Write a structured JSON report.
   - Write a JSONL per-request trace for deeper analysis.

## Data Flow

1. Parse CLI arguments.
2. Load one model config from `configs/model.yml`.
3. Generate a single large input payload from the configured seed.
4. Build the final message list:
   - fixed system prompt
   - user prompt prefix plus generated payload
5. Start either direct transport or dispatcher runtime.
6. Launch concurrent request workers.
7. Collect per-request results into an in-memory list.
8. Aggregate summary statistics.
9. Write terminal output, JSON summary, and JSONL detail.
10. If dispatcher mode was used, shut down dispatcher cleanly.

## Error Semantics

The script must preserve distinctions between the different failure classes rather than flattening them into a generic exception bucket.

The report should distinguish at least:
- `service_unavailable`
- `connection_error`
- `request_timeout`
- `request_context_limit`
- `request_parameter_invalid`
- `request_invalid`
- `rate_limited`
- `malformed_response`
- `unknown`

This is especially important because large-input tests will often surface request-invalid conditions that are operationally different from server outages.

## CLI Surface

Recommended arguments:

- `--mode {direct,dispatcher}`
- `--model-config-name <name>`
- `--concurrency 24`
- `--num-requests 48`
- `--input-chars 100000`
- `--max-tokens 32`
- `--request-timeout 300`
- `--seed 20260313`
- `--output-dir reports/llm_load_test`
- `--system-prompt ...`
- `--user-prefix ...`

Dispatcher-specific options:
- `--dispatcher-max-inflight`
- `--dispatcher-max-inflight-per-lane`
- `--dispatcher-max-attempts`
- `--dispatcher-response-timeout`

## Output Contract

Each run will emit:

1. **Terminal summary**
   - mode, model, concurrency, input size
   - success/failure counts
   - effective RPS, success RPS, failure RPS
   - latency percentiles
   - top error kinds, types, status codes

2. **JSON summary report**
   - run metadata
   - aggregate throughput
   - latency distribution
   - errors grouped by kind/type/status/message
   - per-second throughput buckets

3. **JSONL detail report**
   - one line per logical request
   - request index, timestamps, latency, outcome, status, error fields, response size

## Testing Strategy

Add lightweight automated tests for:

1. Model config loading and validation.
2. Result aggregation correctness.
3. Mode routing (`direct` vs `dispatcher`).
4. Stable error aggregation and normalization.

Do not attempt heavy integration tests against real remote endpoints in the unit suite.

## Risks

1. **Large payload generation dominates runtime**
   - Mitigation: generate once and reuse.

2. **Mode comparison is distorted by different retry behavior**
   - Mitigation: expose explicit retry and timeout parameters in the script and record them in metadata.

3. **Report files become too large**
   - Mitigation: keep JSONL per-request records concise and normalize repetitive error messages.

## Success Criteria

The script is successful if it can:
- run against any model entry from `configs/model.yml`
- produce comparable direct and dispatcher runs with the same payload
- show both throughput frequency and error frequency clearly
- make large-input request-invalid failures easy to distinguish from actual server outages
