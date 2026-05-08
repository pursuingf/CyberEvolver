# Target Recovery and Sandbox Filtering Design

## Context

Two runtime issues are currently interacting in a confusing way:

1. `DockerEnvironment.agent_execute()` can surface local shell-wrapper errors such as `exec /usr/bin/bash: operation not permitted` for commands shaped like `nc ... | head -c ...`. These are local execution-path false positives, but the agent can misread them as "the base shell is broken" or "the target is dead".
2. Remote challenge targets sometimes become unreachable between the `challenge_server` background health scan intervals. The current one-minute monitor is a useful safety net, but it does not cover the exact moment when an agent step needs the target.

The validated direction is:

- Filter the known local false-positive outputs before they reach the agent.
- Keep raw command output in logs for later debugging.
- When the target is truly unreachable, recover synchronously in the foreground with one `force_recreate`, then retry the current action once.
- Make recovery safe under challenge-level thread concurrency and `challenge_server` background monitoring.

## Goals

- Prevent `exec /usr/bin/bash: operation not permitted` false positives from poisoning the agent's observation stream.
- Preserve raw output in logs so operators can still inspect the original failure.
- Add synchronous target availability checks near actual agent execution instead of relying only on the one-minute server monitor.
- Recover an unreachable target with at most one foreground `force_recreate` per incident.
- Ensure a single challenge is not restarted multiple times by concurrent task threads or by the monitor racing the foreground recovery.

## Non-Goals

- Do not change the underlying container sandbox behavior that produces the `bash` or `sh` wrapper errors.
- Do not globally rewrite or ban all `nc` usage.
- Do not turn `docker_env.py` into a challenge lifecycle manager.
- Do not add repeated or unbounded retries for agent commands.
- Do not make agent observations verbose; only short system notices are acceptable.

## Observed Behavior

The key observations established during debugging are:

- The `operation not permitted` message is not returned by the target service.
- The message appears before the wrapped shell begins interpreting the payload, which indicates a local exec-path failure.
- The behavior is payload-sensitive: `nc ... | cat` can succeed while `nc ... | head -c ...` can fail immediately.
- A live local service still reproduces the same wrapper error for `nc ... | head -c ...`, so target liveness is not the root cause of that specific message.
- Real target outages still happen independently, and the current health monitor can miss them because it polls only once per minute.

This means we need two different mechanisms:

- narrow false-positive filtering for local wrapper failures
- explicit target recovery for actual connectivity failures

## Proposed Architecture

### 1. Agent-only command filtering

`agent/agent.py` should call `env.agent_execute(...)` for agent actions, while internal environment setup continues to use `env.execute(...)`.

This keeps the filtering behavior narrow:

- agent action output is sanitized when it matches a known false-positive pattern
- internal environment maintenance commands still return raw output

### 2. Narrow false-positive classifier in `docker_env.py`

`common/agent_runtime/docker_env.py` should own a small classifier that inspects:

- the original command text
- the combined stdout/stderr output
- the return code

It should mark a result as a local shell-wrapper false positive only when all of the following are true:

- the command includes `nc`
- the command shape includes `| head` or `head -c`
- the output matches one of the known local wrapper failures, such as:
  - `exec /usr/bin/bash: operation not permitted`
  - `exec /usr/bin/sh: operation not permitted`
  - `timeout: failed to run command 'bash': Operation not permitted`
- the return code matches the known wrapper-failure forms, such as `255` or `126`

When matched:

- raw output is written to the logger unchanged
- the returned observation is replaced with a short system note
- no target restart is attempted from this branch

### 3. Challenge runtime coordinator

Each challenge worker process should create one shared runtime coordinator that all task threads use for target checks and recovery.

The coordinator should:

- hold the authoritative in-process copy of the current challenge runtime info
- serialize foreground recovery for a single `chal_id`
- refresh `chal_data["target_info"]` after recovery
- expose a light preflight check and a single-flight recovery API

The coordinator belongs on the challenge side, not inside `docker_env.py`, because it needs challenge metadata, backend access, and per-challenge locking.

### 4. Foreground recovery path

Foreground recovery should happen in two places:

- task preflight: before an agent starts stepping, probe the current `target_info`
- action-time recovery: after a command result suggests a real connectivity failure, probe again and recover if still unavailable

The recovery sequence should be:

1. acquire the per-challenge foreground recovery lock
2. re-probe the target to avoid unnecessary restarts
3. if still unavailable, call `force_recreate`
4. refresh runtime cache and `chal_data["target_info"]`
5. retry the current action once

If the retry still fails, return the real result and stop recovering.

### 5. Server-side per-challenge serialization

`bench_hub/server/challenge_server.py` should add per-`chal_id` locking around:

- foreground `launch_challenge(..., force_recreate=...)`
- `cleanup_instance(...)`
- monitor-triggered auto-restart

This ensures:

- only one recreate or cleanup runs for a challenge at a time
- background monitor recovery does not race a foreground recovery
- `running_instances` updates are internally consistent

## Data Flow

### Agent command flow

1. `Agent.step()` calls `env.agent_execute(...)`.
2. `agent_execute()` runs the actual `docker exec ... bash -lc ...`.
3. If the result matches the false-positive classifier:
   - log the raw output
   - return a short sanitized observation
4. Otherwise, if the result looks like a real connectivity failure:
   - ask the runtime coordinator whether the target is actually unreachable
   - if yes, trigger a synchronized `force_recreate`
   - retry the command once
5. Return the real command output, prefixed with a short system note only when recovery actually happened.

### Recovery metadata flow

1. Challenge worker builds a runtime coordinator from challenge metadata and CTF backend config.
2. Task threads share that coordinator.
3. When recovery succeeds, the coordinator updates:
   - its own runtime snapshot
   - the task-local `chal_data`
   - any refreshed service host/port values needed for future steps
4. If the service endpoint changed, the retried observation includes a short update line so the agent can adapt.

## Error Classification Rules

### False positive branch

This branch is only for known local wrapper failures. It never restarts the target.

Returned observation:

- short system note
- no long explanation
- no claim that the base environment is broken

### Real connectivity branch

This branch is for actual target availability problems, such as:

- `Connection refused`
- `Connection timed out`
- `No route to host`
- `Network is unreachable`
- active probe failure against current target host/port

Only this branch can trigger `force_recreate`.

### Unknown failures

Anything outside the two categories above is returned unchanged. This design deliberately avoids broad heuristics.

## Concurrency Model

There are two distinct contention domains.

### Challenge worker process

Within a single challenge worker process, `task_workers` run as threads. They can all notice the same outage at nearly the same time.

To avoid duplicate recreates:

- maintain a per-challenge lock in the runtime coordinator
- only one thread performs foreground recovery
- other threads wait, then consume the refreshed runtime state
- all waiting threads re-probe after wake-up before proceeding

### `challenge_server`

The server can have simultaneous foreground API requests and a background monitor. Both must respect the same per-challenge lock.

The server should also keep a small "recovery in progress" marker so the monitor can skip a challenge already being rebuilt by a foreground request.

## Logging and Agent Observations

Logging should stay operator-friendly while observations stay terse.

### Logs

- raw command output remains available in challenge logs
- foreground recovery events are logged with challenge id, reason, and whether a retry occurred
- server logs should distinguish:
  - monitor-triggered recovery
  - foreground recovery
  - lock-wait reuse of another thread's recovery result

### Agent observations

- false-positive filter: one short system line
- successful recovery + retry: return retried output, optionally prefixed by one short system line
- endpoint changed: include one short updated target line

No long diagnostic prose should be injected into the agent context.

## Testing Strategy

### 1. False-positive filtering tests

Add deterministic tests that verify:

- known `nc | head` wrapper failures are sanitized
- ordinary shell errors are not sanitized
- commands without the matching pattern remain unchanged

### 2. Foreground recovery tests

Add tests for the runtime coordinator that verify:

- preflight probe can trigger a single recovery
- concurrent threads only perform one `force_recreate`
- task-local `chal_data["target_info"]` is refreshed after recovery
- recovery retries at most once

### 3. Server locking tests

Add tests for `challenge_server` that verify:

- `launch_challenge` and monitor recovery serialize per challenge
- concurrent recreate requests for the same challenge do not perform duplicate cleanup/relaunch cycles
- `running_instances` remains coherent after recovery

### 4. Regression safety checks

Run focused Python tests plus lightweight syntax verification for all touched modules.

## Risks and Mitigations

### Risk: false-positive classifier becomes too broad

Mitigation:

- require command-shape match plus output match plus return-code match
- keep the rule list explicit and small

### Risk: recovery races still happen across threads

Mitigation:

- use per-challenge locking in both the worker process and the server
- re-probe after acquiring the lock before forcing recreate

### Risk: agent continues using stale endpoint info

Mitigation:

- refresh `chal_data["target_info"]` after recovery
- include a short endpoint update in the retried observation only if it actually changed

### Risk: recovery loop becomes noisy or expensive

Mitigation:

- one foreground retry only
- keep the background monitor as a fallback, not the primary path

## Rollout Plan

1. Add deterministic tests for the false-positive classifier and the runtime coordinator.
2. Refactor `agent` to use `agent_execute()` for action steps.
3. Add foreground recovery support in the challenge worker process.
4. Add per-challenge locking in `challenge_server`.
5. Run focused regression tests and a syntax pass.
