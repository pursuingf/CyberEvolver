# CVE Bench Parallel Runtime Design

**Date:** 2026-03-26

## Context

The current CVE Bench integration runs challenge services on a shared Docker network and exposes target services through host ports. That model is workable for single-run smoke tests, but it breaks down for concurrent evaluation:

- CVE Bench challenge services use hard-coded canonical names such as `target`, `server`, and `mailhog`.
- The `/done` endpoint is hosted inside the `target` container, but the grader behind it may depend on other challenge-local service names.
- Challenge instances are stateful and agent actions are destructive, so a single running instance must not be shared by multiple agents at the same time.
- Agent actions must execute inside sandbox containers, not on the host.

This means the parallelization problem is primarily about preserving challenge-local DNS semantics and isolating stateful runs, not just avoiding host port conflicts.

## Key Findings

### CVE Bench evaluator semantics

- The `/done` endpoint is served by the evaluator process started inside the `target` container.
- The evaluator is launched by the common target entrypoint and exposed on port `9091`.
- Although `/done` lives on `target`, grader logic may reach other services using fixed canonical names.
- At least one grader, `fluent_bit`, directly accesses `http://server:9090`.
- Other multi-service challenges include `server` and `mailhog` in challenge metadata and compose topology.

### Why the shared-network model is unsafe

- All challenge instances connected to a single `ctfnet_*` network share the same alias space.
- Canonical names such as `target` and `server` are not unique across runs.
- Rewriting benchmark metadata is risky because evaluator and challenge initialization consume the original metadata semantics.
- Agent-facing aliases alone do not solve the internal challenge routing problem, because target-side code and grader-side code still expect canonical names.

## Chosen Direction

Use **compose-project isolation** for CVE Bench runs.

Each launched run should own:

- its own Docker Compose project
- its own challenge-local Docker networks
- its own dedicated sandbox container

The agent sandbox should join the challenge's agent-reachable network, while target-side services continue using their original canonical names within the isolated Compose namespace.

This preserves upstream CVE Bench behavior and avoids the need to rewrite `/cve_metadata.yml`, patch grader logic, or emulate benchmark-local DNS on top of a shared global network.

## Alternatives Considered

### A. Shared network with alias/DNS virtualization

This would keep all challenge services on one global network and try to hide collisions through extra aliases or per-container DNS tricks.

Pros:

- fewer Docker networks
- more sandbox reuse opportunities

Cons:

- must virtualize canonical names like `target` and `server`
- target-side code, grader code, and prompt-facing names all become separate concerns
- fragile when `/done` or health checks reach auxiliary services
- significantly more benchmark-specific logic

This is not the recommended first implementation.

### B. Isolated networks per run

Each run gets a dedicated Compose project and its own sandbox.

Pros:

- preserves canonical service names exactly as upstream expects
- clean isolation for stateful, destructive workloads
- straightforward mental model for debugging and cleanup
- agent-visible addressing no longer depends on host port forwarding

Cons:

- sandboxes are not reused for CVE Bench runs
- Docker address-pool exhaustion becomes an operational concern

This is the recommended baseline model.

## Approved Design

### 1. Runtime isolation model

For CVE Bench, parallel execution means:

- one launch creates one isolated Compose project
- one launch creates one isolated sandbox
- the sandbox only joins that launch's challenge-local network

Host port mappings remain available for manual debugging, but they are no longer the primary connectivity path for the agent.

### 2. Sandbox orchestration lives in `GlobalDockerManager`

Sandbox lifecycle policy should not be embedded in `DockerEnvironment`.

Instead:

- `DockerEnvironment` remains a low-level single-container execution primitive
- `GlobalDockerManager` owns sandbox allocation and reuse strategy
- `GlobalDockerManager` decides whether a benchmark uses shared or exclusive sandboxes
- `GlobalDockerManager` connects sandbox containers to the run-specific network returned by the launch layer

For CVE Bench, the selected policy is exclusive sandbox allocation.

### 3. Benchmark runtime policy belongs to runner/config

Benchmark execution policy should not be stored in `challenge.json`.

The challenge metadata should stay focused on:

- compose inputs
- prompt inputs
- benchmark source references

Benchmark-specific orchestration policy should instead live in runner-level configuration or benchmark-bound args in `run_xx.py`.

That configuration should define the benchmark's runtime behavior, such as:

- isolated vs shared sandbox policy
- isolated Compose-network mode
- scorer mode such as `/done` polling

### 4. Dynamic launch results belong in `chal_data.runtime`

`chal_data.runtime` should contain only per-launch results, not static strategy knobs.

The minimal runtime result should include values such as:

- `run_id`
- `project_name`
- `agent_network_name`
- `network_names`
- `service_names`
- `external_services`

This keeps prompt rendering and runtime consumers focused on actual launch outputs.

## Interface Consequences

### `challenge_server`

- Launch state should move from `chal_id -> instance` semantics toward `run_id -> instance`.
- A launch response should expose the run-specific network information required for sandbox attachment.
- Cleanup should target the run, not assume one global active instance per challenge.

### `ChallengeClient`

- It should carry benchmark-level orchestration args from the runner into launch/runtime calls.
- It should expose per-launch runtime results through `chal_data.runtime`.
- It should stop inferring CVE Bench behavior from prompt metadata or host port structure.

### `GlobalDockerManager`

- It should become the single source of truth for sandbox allocation policy.
- It should support benchmark-bound strategies such as exclusive sandbox creation for CVE Bench.
- It should attach the chosen sandbox to the run-specific agent network and keep challenge cache preparation as part of sandbox provisioning.

## Risks

### Docker network exhaustion

The isolated-network model increases network churn. This should be handled operationally:

- explicit cleanup on stop/failure
- clear error reporting when network creation fails
- possible future tuning of Docker address pools

This is still preferable to weakening challenge isolation.

### Existing code keyed by `chal_id`

Several current code paths still assume one active runtime per challenge ID. That assumption must be removed carefully because it affects launch bookkeeping, stop behavior, logging, and recovery.

### Duplicated sandbox managers

There are multiple `GlobalDockerManager` copies in runner scripts today. The implementation should consolidate policy logic into `common/agent_runtime/docker_manager.py` to avoid divergence.

## Testing Implications

The implementation should verify:

- concurrent launches of the same CVE challenge produce distinct runs
- each run gets a distinct sandbox and a distinct challenge-local network
- agent sandboxes can reach canonical service names inside the isolated network
- `/done` still works for challenges whose graders access auxiliary services such as `server`
- cleanup removes both runtime resources and sandbox resources for the run

## Non-Goals

This design does not attempt to:

- rewrite upstream CVE Bench metadata files
- replace canonical names with globally unique aliases inside benchmark internals
- optimize for sandbox reuse in CVE Bench
- solve all Docker network capacity issues at the metadata layer
