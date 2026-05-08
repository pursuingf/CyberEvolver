# AutoPenBench Benchmark Layout Design

## Goal

Integrate AutoPenBench into the existing framework without changing the current `challenge_server/test_challenge_server.py` operating model. AutoPenBench challenges should be managed through repo-local benchmark indexes and per-challenge metadata, and runtime compose files should be created inside each challenge directory.

## Approved Constraints

- Keep the current index-file workflow: if an index JSON is listed in `INDEX_FILES`, every challenge in that file should be tested automatically.
- Manage benchmark content under a unified repo-local benchmark root instead of relying on external source lists for normal operation.
- Preserve the existing single-challenge launch model for now. Ignore multi-VM scenario coordination and shared-subnet semantics until later.
- Generate runtime compose files inside the challenge directory, not in a separate `.runtime` directory.
- Keep the agent-facing AutoPenBench behavior lightweight: the prompt mainly exposes the task text plus reachable host/port information.

## Layout

The framework will use a repo-local benchmark root named `benchmark/`, with compatibility fallback to `benchmarks/` when needed by older configs or tests.

AutoPenBench will be organized as:

- `benchmark/autopenbench.json`
- `benchmark/autopenbench/benchmark/machines/<level>/<category>/<vm>/challenge.json`
- `benchmark/autopenbench/benchmark/machines/<level>/<category>/<vm>/docker-compose.runtime.<namespace>.yml`

The generated `challenge.json` keeps AutoPenBench-native fields such as `task`, `flag`, `target`, `vulnerability`, `level`, `category`, and `vm`, while also adding the minimal framework fields needed for discovery and runtime launching.

## Discovery Model

The existing `ChallengeJsonAdapter` remains the primary discovery path for repo-local benchmarks. AutoPenBench is therefore adapted into challenge-json-shaped metadata on disk rather than discovered directly from an external source at runtime.

`benchmark/autopenbench.json` maps challenge IDs like `apb-in-vitro-access_control-vm0` to their relative challenge directories. `ChallengeJsonAdapter` can then discover AutoPenBench in the same way it discovers NYU/Intercode-style benchmarks.

## Runtime Model

Each AutoPenBench challenge points to:

- the shared base compose file under `benchmark/machines/docker-compose.yml`
- the category compose file under `benchmark/machines/<level>/<category>/docker-compose.yml`
- the target service for the selected VM

At launch time, the runtime layer materializes a single challenge-local compose override file inside the selected `vmX` directory. `docker compose` is then invoked from that challenge directory while referencing the compose stack with paths relative to that directory.

For now, only the selected target service is exposed as a public service. Shared dependencies and multi-target topologies are deferred.

## Generation Flow

AutoPenBench metadata is generated from `data/games.json`.

For each game entry:

1. Derive the challenge ID from `level`, `category`, and `target`.
2. Resolve the corresponding `vmX` directory under `benchmark/machines/...`.
3. Create or refresh `challenge.json` in that VM directory.
4. Add the challenge to `benchmark/autopenbench.json`.

This keeps AutoPenBench’s native benchmark layout intact while making it consumable through the project’s existing benchmark index workflow.

## Testing Strategy

Add regression coverage for:

- benchmark root resolution with `benchmark/` as the primary root
- generated AutoPenBench index and per-challenge metadata
- `test_challenge_server.py` automatic challenge enumeration through index files
- runtime compose placement inside the challenge directory
- `challenge_server` launch behavior for a single AutoPenBench challenge
