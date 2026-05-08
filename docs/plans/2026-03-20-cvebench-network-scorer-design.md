# CVE Bench Network and Scorer Design

## Goal

Extend the current benchmark runtime so CVE Bench can run in parallel without host-port collisions, while also supporting benchmark-specific success evaluation through a shared post-step scoring interface.

## Approved Constraints

- Keep the current benchmark discovery flow based on repo-local index files and `challenge.json`.
- Do not add benchmark-specific prompt routing in Python; pass full `chal_data` into the Jinja instance template instead.
- Do not add a `preferred_access` field or other prompt-side policy switch in runtime metadata.
- Keep both internal and external reachability data in runtime metadata so the framework can support Docker-network access and manual debugging at the same time.
- Avoid scattering benchmark-specific branches across `ChallengeClient`, agent prompt assembly, and agent execution.
- Use one scorer call site after each agent step instead of maintaining separate tool-result and polling hook types.

## Problem Statement

The current CVE Bench integration is still shaped like a host-port-exposed CTF runtime:

- launch metadata is flattened into a single `host` / `port` pair
- `ChallengeClient` and prompt rendering assume the agent should attack the target through that single pair
- CVE Bench targets keep their original fixed ports such as `9090` and `9091`
- the framework currently relies on `submit`-driven success checks instead of benchmark-managed evaluation

That creates two blocking issues:

1. Parallel runtime contention. If multiple CVE Bench challenges expose fixed application and evaluator ports through the host, concurrent runs become fragile or impossible.
2. Incorrect evaluation semantics. CVE Bench determines success through its evaluator service, not only through a user-triggered `submit` action.

## Alternatives Considered

### 1. Minimal CVE-Bench-only patches

Patch CVE Bench launch to use internal networking and hard-code `/done` polling in the agent loop.

This is the fastest route to a local fix, but it would keep benchmark-specific branches in multiple layers and make the next benchmark integration harder.

### 2. Shared runtime metadata plus benchmark scorer registry

Teach launch/runtime layers to emit richer service metadata, pass that metadata through `chal_data`, and let a benchmark scorer registry decide how success is checked after each agent step.

This keeps benchmark differences localized and is the approved approach.

### 3. Per-benchmark runtime driver stacks

Give each benchmark family its own launch, access, prompt, and evaluation driver.

This is flexible but too expensive for the current framework and would duplicate the launch-spec-based runtime path already in place.

## Runtime Endpoint Model

The runtime layer should stop collapsing service access into one address pair. Instead, every launched service should expose both internal and external access information.

Each service record should carry:

- `inner_host`
- `inner_port`
- `external_host`
- `external_port`

Compatibility fields can remain for existing consumers:

- `host = external_host`
- `port = external_port`

Those compatibility fields are transitional only. Prompt rendering and future runtime consumers should use the richer fields directly.

This keeps one canonical service record shape for every benchmark. No benchmark-family-specific access-policy field is needed.

## Network Model

Each launched challenge already has its own compose project and Docker network namespace. The new requirement is to make the agent sandbox participate in that challenge-local network when internal service coordinates are available.

The launch path should therefore:

- keep creating a dedicated runtime network per challenge instance
- ensure target services are reachable within that network by stable service alias
- expose external host ports only for compatibility, smoke tests, SSH forwarding, and manual debugging

The agent sandbox setup should then:

- join the challenge-local Docker network
- use the `inner_host` / `inner_port` path when available
- fall back to `external_host` / `external_port` only when no internal path exists

This removes the need for the agent to attack `localhost:<random-port>` for CVE Bench style targets, which is what blocks safe parallelization today.

## `chal_data` Propagation Model

`ChallengeClient` should not decide which address the prompt or scorer should use. Its job is only to return complete runtime metadata in `chal_data`.

That means:

- `challenge_server` returns full service metadata in `/launch`
- `ChallengeClient` preserves that metadata in `target_info`
- `chal_data` passed into the agent contains the full service records without benchmark-specific filtering
- additional runtime metadata is attached under a dedicated runtime section

The runtime section should include at least:

- `runtime.project_name`
- `runtime.network_name`
- `runtime.scoring`

This keeps Python-side logic simple and moves benchmark-specific presentation decisions into templates or scorer implementations.

## Prompt Model

The instance prompt should be rendered from full `chal_data` rather than from a preformatted endpoint block assembled in Python.

The Jinja instance template should:

- inspect `instance_data.target_info`
- prefer `inner_host` / `inner_port` when present
- otherwise fall back to `external_host` / `external_port`
- render benchmark metadata directly from `instance_data.metadata` and `instance_data.prompt_variants`

This means prompt-side selection is data-driven rather than benchmark-driven. Adding a new benchmark family should not require another prompt branch in Python as long as launch/runtime populates the same field shape.

## Unified Step-End Scorer Model

Success evaluation should be unified behind a single scorer call that runs after every executed agent step.

The framework will call a benchmark scorer after each step with:

- the executed action
- the resulting observation
- the full `chal_data`
- any task-local runtime state needed by the scorer

The scorer should return a structured result containing at least:

- `done`
- `message`
- `metadata`

Different benchmark families then specialize the scorer implementation instead of modifying the main agent loop:

- classic CTF-style benchmarks inspect the post-step result for a `submit` outcome
- CVE Bench performs the evaluator status check, for example through `/done`

The main loop only needs one decision point: after each step, ask the scorer whether the run is complete.

## CVE Bench Scoring Model

CVE Bench already carries enough information to support evaluator-based success checks:

- the task metadata includes the target application URL and upload URL
- the target image contains the evaluator service and `/done` endpoint
- the original CVE Bench agent uses real-time success checks rather than a final explicit submission

Within this framework, CVE Bench runtime metadata should declare scoring instructions under `chal_data["runtime"]["scoring"]`.

That scoring payload should identify:

- scorer kind
- scorer service
- scorer path or endpoint components

The scorer implementation can then build and query the evaluator endpoint without needing to parse prompt text or benchmark-specific hard-coded logic in the agent.

## Component Responsibilities

### `bench_hub/server/launch_runtime.py`

- materialize service metadata with internal and external coordinates
- preserve stable aliases for network-local access
- emit challenge-local network identifiers needed by the sandbox

### `bench_hub/server/challenge_server.py`

- extend `ServiceInfo` and launch responses to carry the richer metadata
- persist runtime metadata needed for later health checks and cleanup

### `common/agent_runtime/challenge_client.py`

- preserve full service records in `target_info`
- attach runtime scoring metadata to `chal_data`
- stop flattening runtime connectivity into a single benchmark-specific access path

### `agent/agent.py` and `gen0_root/skill_based/agent.py`

- pass complete `chal_data` into prompt rendering
- run the registered benchmark scorer after each executed step
- stop treating success as a `submit`-only concept inside the agent core

### `gen0_root/skill_based/instance_template.txt`

- choose internal or external service coordinates based only on available fields
- render prompt variants and benchmark metadata from `instance_data`

### Benchmark scorer registry

- register scorer implementations by `benchmark_family`
- provide a shared no-op/default scorer for benchmarks that do not need specialized behavior

## Testing Strategy

Regression coverage should be added in four layers.

### 1. Runtime metadata tests

Verify that launch/runtime paths emit service records with:

- `inner_host`
- `inner_port`
- `external_host`
- `external_port`
- compatibility `host` / `port`

Also verify that `chal_data["runtime"]["scoring"]` is present for CVE Bench challenges.

### 2. Prompt rendering tests

Verify that instance prompt rendering:

- uses `inner_host` / `inner_port` when both exist
- falls back to `external_host` / `external_port` otherwise
- preserves CVE Bench prompt variant semantics
- does not require benchmark-family-specific formatting logic in Python

### 3. Scorer tests

Verify that:

- the default CTF scorer recognizes post-step `submit` success
- the CVE Bench scorer performs post-step evaluator polling and stops when the target reports success

### 4. Runtime smoke tests

Verify an end-to-end CVE Bench launch can:

- start the challenge runtime
- expose complete service metadata
- attach the agent sandbox to the challenge network
- allow internal-address access to the target service
- query the evaluator endpoint through the benchmark scorer path

## Non-Goals

- redesigning the benchmark discovery model
- changing challenge layout generation for CVE Bench or AutoPenBench
- removing external service exposure entirely
- introducing benchmark-family branches into prompt assembly just to choose which endpoint to display
