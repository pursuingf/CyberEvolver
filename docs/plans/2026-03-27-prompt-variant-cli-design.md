# Prompt Variant CLI Override Design

## Goal

Add a runner-level `--prompt-variant` switch so a single evolution run can temporarily choose `zero_day` or `one_day` prompt materialization without modifying benchmark metadata on disk.

## Scope

- Add a CLI argument in `run_evolve_batch_skill.py`
- Apply the override only to the in-memory `chal_data` sent to workers
- Ignore the override for challenges that do not declare the requested variant
- Leave `challenge.json`, `ChallengeClient`, and benchmark files unchanged

## Recommended Approach

Use a runner-local override in `run_evolve_batch_skill.py` after `challenge_client.get_challenge_data(...)` returns and before the challenge is submitted to the worker pool.

This keeps the feature:

- easy to use for experiments
- scoped to a single run
- isolated from benchmark storage and runtime management

## Data Flow

1. Parse `--prompt-variant zero_day|one_day`
2. Load challenge data from `ChallengeClient`
3. If `chal_data["variant_names"]` contains the requested variant:
   - copy the challenge data
   - replace `chal_data["default_variant"]`
   - update `chal_data["source_fields"]["default_variant"]` when present
4. Pass the overridden `chal_data` into the worker
5. `EvolutionOrchestrator.init_generation_zero(...)` keeps using `chal_data["default_variant"]`, so prompt materialization automatically selects the requested profile

## Edge Cases

- No `--prompt-variant`: keep current behavior
- Challenge has no `variant_names`: ignore override
- Requested variant is not in `variant_names`: ignore override
- Non-CVE benchmarks: unaffected unless they later expose compatible `variant_names`

## Testing

- Unit test that `one_day` overrides a challenge with `variant_names=["zero_day", "one_day"]`
- Unit test that the original `chal_data` object is not mutated
- Unit test that unsupported or missing variants are ignored
