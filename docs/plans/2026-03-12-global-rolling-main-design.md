# Global Rolling Main Scheduler Design

**Goal:** Redesign `run_evolve_batch_skill.py::main()` so challenge execution is throughput-first across the whole run, no longer blocked by category boundaries, while challenge resources are initialized lazily only when a worker slot is actually available.

**Scope:** This design only covers the top-level scheduling path rooted at `run_evolve_batch_skill.py::main()`. It preserves the existing challenge worker implementation, summary schema, and LLM dispatcher behavior.

**Non-goals:**
- changing `evolve_single_challenge()` semantics
- changing `EvolutionLoop` or mutation/evaluation behavior inside a challenge
- redesigning the final `evolution_summary.json` format
- introducing category fairness or priority scheduling

## Problem

The current `main()` groups all selected challenges by category, submits a full category to the process pool, waits for that entire category to finish collecting results, and only then starts the next category.

That structure creates two problems:
- category boundaries act as hard execution barriers, which reduces end-to-end throughput whenever one category has slow challenges
- `challenge_client.get_challenge_data(chal_id)` is called eagerly for every challenge in the category burst instead of only when that challenge is about to start running

The desired behavior is simpler and more throughput-oriented:
- keep category metadata for logging and final reporting
- remove category as an execution barrier
- lazily initialize challenge data only at submit time

## Current Behavior

Today `main()` does the following:
1. select challenge metadata via `get_target_challenges()`
2. group challenges by category
3. loop over categories in sorted order
4. for each category, call `get_challenge_data(chal_id)` for every challenge in that category and submit them all
5. block until the full category result set is collected
6. move to the next category

The final summary is already challenge-oriented and then aggregated by category, which means the execution model can change without requiring a summary redesign.

## Proposed Architecture

### 1. Global pending queue

Replace the sequential category loop with a single global pending queue derived from the selected challenge metadata.

Each pending item contains only lightweight information:
- `chal_id`
- `category`
- raw challenge metadata from `get_target_challenges()`

The queue does not contain fully initialized `challenge_data`.

### 2. Global inflight future map

Maintain one global `future -> challenge context` mapping for all submitted challenges.

This replaces the current per-category `futures` dictionary and allows the main loop to refill the process pool immediately after any challenge completes, regardless of category.

### 3. Lazy challenge initialization

Move `challenge_client.get_challenge_data(chal_id)` into the submit path so it only runs when:
- the challenge reaches the front of the pending queue
- a worker slot is available
- the challenge is about to be submitted to the executor

This ensures we do not initialize challenge resources for work that is still merely pending.

### 4. Rolling refill loop

The new main scheduling loop is:
1. prefill inflight work up to `max_workers`
2. wait for any single challenge future to complete
3. collect and finalize that result
4. immediately submit the next pending challenge if one exists
5. repeat until both pending and inflight are empty

This keeps the pool busy without being blocked by category boundaries.

## Data Flow

### Submission flow

1. `main()` selects metadata through `get_target_challenges()`.
2. The selected challenges are normalized into `pending_items`.
3. `submit_next_pending_challenge(...)` pops one pending item.
4. The submit helper calls `challenge_client.get_challenge_data(chal_id)` just-in-time.
5. The helper submits `evolve_single_challenge(...)` to the shared `ProcessPoolExecutor`.
6. The returned future is recorded in the global inflight map.

### Completion flow

1. `collect_one_challenge_result(...)` waits for one completed future.
2. It calls `future.result()`.
3. The returned challenge result is appended to the global `results` list.
4. Budget logging and teardown happen for that challenge.
5. Main immediately tries to refill another worker slot from `pending_items`.

## Error Handling

### `get_challenge_data()` failure

If challenge initialization fails before submission:
- record a failed challenge result with a clear `error` message such as `challenge data init failed: ...`
- attempt best-effort `challenge_client.finish_challenge(chal_id)` cleanup
- continue scheduling the next pending challenge

### Worker failure with ordinary result

If the worker returns a structured failed result such as `failed`, `interrupted`, or `aborted_budget`:
- append it to `results`
- log budget snapshot if available
- run `finish_challenge(chal_id)`
- continue scheduling normally

### `BrokenProcessPool`

If any `future.result()` raises `BrokenProcessPool`:
- stop all new submissions immediately
- mark unresolved inflight challenges as failed with a message indicating the process pool broke during result collection
- mark all remaining pending challenges as failed with a message indicating the process pool broke before submission
- preserve final summary compatibility by keeping `status="failed"` and placing detail into `error`

This is a global failure mode now, not a category-scoped one.

### Keyboard interrupt

Keyboard interrupt behavior stays the same:
- stop new work
- shut down the executor with cancellation enabled
- kill descendant processes
- exit with code `130`

## Logging and Observability

Because category is no longer an execution barrier, logs should stop pretending that work happens in category batches.

### Startup logs

Keep:
- run directory
- token budget settings
- dispatcher startup
- selected challenge list

Replace category execution-order logs with:
- `Execution mix by category: crypto=50, pwn=12, web=8`
- `Scheduling mode: global rolling queue (throughput-first, lazy challenge init)`

### Submit logs

Each submit should log a compact line like:
- `submit chal=<id> category=<category> inflight=<n> pending=<m>`

This makes it visible when challenge resources are actually initialized and when a worker slot is consumed.

### Completion logs

Each completed challenge should continue to log:
- status
- best success rate
- per-challenge budget snapshot
- teardown result

Add progress context such as:
- completed count
- inflight count
- pending count

### Category progress summary

To preserve category visibility, add low-frequency category progress summaries that report, per category:
- total
- completed
- inflight
- pending
- solved

This keeps category observability without restoring category execution barriers.

### Broken pool logs

Broken pool logs should now explicitly describe global scheduling state, for example:
- which challenge was being collected when the pool broke
- how many inflight challenges were marked failed
- how many pending challenges were never submitted

## Files to Change

### Primary files
- `run_evolve_batch_skill.py`
- `utils/process_pool_guards.py`

### Potential new helper file
- optional small scheduler helper module if `main()` becomes too large, though the preferred first implementation is to keep the logic local and avoid over-factoring

### Tests
- `tests/test_run_evolve_batch_skill_guards.py`
- new scheduler-focused tests, likely `tests/test_run_evolve_batch_skill_scheduler.py`

## Testing Strategy

### Unit tests

Add tests for:
- rolling global submission and refill behavior
- lazy `get_challenge_data()` timing
- `BrokenProcessPool` global failover behavior
- summary compatibility after the scheduling rewrite

### Verification

Run:
- unit tests covering new main scheduling helpers
- existing process-pool guard tests
- `python run_evolve_batch_skill.py --help`
- a small end-to-end smoke run with challenges from at least two categories and `--max-workers 2`

## Design Decisions

### Why not preserve category-level round robin

The stated goal is throughput first. Category fairness would reintroduce intentional pacing across categories and would solve a different problem than the one requested.

### Why not create a background feeder thread

A feeder thread is possible, but it adds synchronization complexity without being necessary for the first version. A synchronous rolling refill loop in `main()` is easier to reason about and easier to debug.

### Why keep category in the final summary

Category is still useful for analysis and reporting, even if it stops being a scheduling boundary. The current summary format already supports that split cleanly.

## Success Criteria

The redesign is successful when:
- challenge execution no longer waits for one category to fully finish before another category can start
- `get_challenge_data(chal_id)` is called only when a challenge is about to be submitted
- the process pool stays filled up to `max_workers` whenever pending work exists
- final summaries still aggregate by category without format changes
- broken-pool behavior remains understandable and logs reflect global scheduling state accurately
