# Run Batch Skill Runtime Args Design

## Goal

Make `run_evolve_batch_skill.py` pass explicit benchmark runtime arguments through both runtime layers:

- `GlobalDockerManager` keeps using the full `benchmark_runtime_args`
- `ChallengeClient` receives only the subset it understands, starting with `parallel_mode`

This should be enough to make CVE Bench launches use the configured target parallel strategy without adding new benchmark-specific branching in the runner.

## Decisions

### 1. Reuse `benchmark_runtime_args` as the single source of truth

The runner already resolves benchmark-scoped runtime configuration through `resolve_benchmark_runtime_args(...)`. We keep that as the only config entry point.

- `sandbox_policy` remains owned by `GlobalDockerManager`
- `parallel_mode` is filtered out and passed to `ChallengeClient`
- nothing is inferred implicitly from `benchmark_family`

### 2. Keep `ChallengeClient` filtering small

`ChallengeClient` should not consume the whole runtime-args map. The runner will pass a small filtered dict containing only the keys that affect target launch behavior.

Initial supported key:

- `parallel_mode`

### 3. Preserve runtime args for worker-side recovery

`evolve_single_challenge(...)` creates a fresh `ChallengeClient` for recovery through `ChallengeRuntimeCoordinator`. That manager must remember the same explicit runtime args used for the original launch, otherwise force-recreate may lose the configured parallel mode.

The cleanest approach is:

- runner resolves `ctf_runtime_args`
- runner seeds them into the worker-local `ChallengeClient`
- recovery refresh reuses the cached args through the existing `ChallengeClient` cache path

## Scope

### In scope

- `run_evolve_batch_skill.py`
- scheduler submit path
- worker-local runtime manager setup
- focused scheduler tests

### Out of scope

- changing benchmark metadata schema
- changing DockerManager sandbox reuse logic
- changing `challenge_server` defaults

## Verification

Focused verification is sufficient:

- scheduler lazy init passes explicit `parallel_mode` to `ChallengeClient.get_challenge_data(...)`
- worker runtime manager preserves the same args for recovery paths
- existing runtime-args tests still pass
