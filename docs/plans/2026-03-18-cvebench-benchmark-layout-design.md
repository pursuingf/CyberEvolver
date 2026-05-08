# CVE Bench Benchmark Layout Design

## Goal

Integrate CVE Bench into the existing framework using the same repo-local benchmark workflow as AutoPenBench, while preserving CVE Bench's native one-challenge-per-directory structure and prompt variant semantics.

## Approved Constraints

- Manage CVE Bench under the unified repo-local `benchmark/` root.
- Keep the current index-file workflow: if an index JSON is present under `benchmark/`, `test_challenge_server.py` should discover and test its challenges without an extra allowlist.
- Write runtime compose files inside each challenge directory, not in a separate runtime root.
- Preserve CVE Bench prompt variants by storing both `zero_day` and `one_day`, while defaulting normal runs to `zero_day`.
- Reuse the existing `pentest_remote` agent direction instead of introducing a new agent framework mode.

## Layout

CVE Bench will be materialized into a repo-local layout:

- `benchmarks/cvebench.json`
- `benchmarks/cvebench/<challenge_id>/challenge.json`
- `benchmarks/cvebench/<challenge_id>/docker-compose.runtime.<namespace>.yml`

Each generated challenge directory maps directly to a single upstream CVE Bench challenge directory under `src/critical/challenges/<CVE>`.

The generated `challenge.json` will keep CVE Bench-native source metadata while adding the minimum fields needed by the current server and benchmark adapters. The metadata includes:

- `benchmark_family: cvebench`
- `task_profile: pentest_remote`
- `compose_files`
- `metadata_path`
- `eval_path`
- `challenge_source_root`
- `default_variant: zero_day`
- `prompt_variants.zero_day`
- `prompt_variants.one_day`

This keeps discovery uniform without flattening away the CVE Bench source model.

## Discovery Model

`ChallengeJsonAdapter` remains the primary discovery path for repo-local benchmarks. CVE Bench is therefore integrated by generating challenge-json-compatible metadata under `benchmark/` rather than by teaching the server to read the external CVE Bench repository directly at runtime.

`benchmarks/cvebench.json` maps challenge IDs to their relative challenge directories. `ChallengeJsonAdapter` then loads `challenge.json` from each directory and exposes CVE Bench challenges through the same normalized interface already used by the rest of the framework.

## Runtime Model

`challenge_server`, `ChallengeClient`, and `test_challenge_server.py` keep their current operating model. The only benchmark-specific behavior is that challenges tagged with `benchmark_family == "cvebench"` carry compose and prompt metadata shaped from CVE Bench.

At launch time:

- the runtime layer reads `compose_files` from `challenge.json`
- resolves CVE Bench `include`, `extends`, and environment-driven compose paths into stable launchable paths
- materializes a challenge-local runtime compose file inside `benchmarks/cvebench/<challenge_id>/`
- exposes the target endpoint derived from CVE Bench's application metadata
- preserves evaluator-related exposure such as the upload service on port `9091` when required by the task

`test_challenge_server.py` should continue to enumerate whatever is present in repo-local index files. No per-benchmark allowlist or separate "selected tasks" control surface should be introduced.

## Prompt Model

The agent-facing behavior stays lightweight and aligned with the existing `pentest_remote` profile.

The system prompt remains generic and benchmark-agnostic. Benchmark-specific information is carried through the instance prompt by pulling from the generated `challenge.json`.

For CVE Bench, the instance prompt should provide:

- the selected variant prompt, defaulting to `zero_day`
- the reachable host and port for the target
- the attack scope and restrictions
- any necessary account or task-specific metadata already supplied by CVE Bench

The benchmark integration should preserve both `zero_day` and `one_day` prompt variants in metadata so future experiments can switch variants without regenerating the benchmark layout.

## Testing Strategy

Add regression coverage for:

- CVE Bench layout generation into repo-local `benchmark/`
- generated `cvebench.json` index contents and per-challenge `challenge.json`
- challenge discovery through `ChallengeJsonAdapter`
- runtime compose materialization inside the challenge directory
- CVE Bench endpoint exposure and prompt variant persistence
- `test_challenge_server.py` exercising discovered CVE Bench challenges through the existing index-file workflow
