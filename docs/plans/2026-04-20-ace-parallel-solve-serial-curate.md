# ACE Parallel Solve Serial Curate Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert `ace_agent` from per-item online playbook mutation into a `parallel solve + serial curate` pipeline with configurable playbook sharing scope.

**Architecture:** Keep the existing batch executor and per-challenge worker lifecycle intact. Workers should read a fixed playbook snapshot, solve a challenge, and emit ACE artifacts only. The main batch process should group work items by playbook scope, execute each scope as an independent lane, run one parallel batch per lane, and then run one serial batch curator pass that updates the scope-local playbook for the next window.

**Tech Stack:** Python 3, `ThreadPoolExecutor`, existing batch runner in `baseline/batch`, existing ACE-inspired CTF agent in `baseline/agents/ace_agent.py`, JSON log artifacts, YAML agent config.

---

### Task 1: Add ACE Batch CLI Surface

**Files:**
- Modify: `baseline/batch/run_batch_baseline.py`
- Test: manual CLI smoke test via `python baseline/batch/run_batch_baseline.py --help`

**Step 1: Add ACE CLI arguments**

Add these arguments to `parse_args()`:

```python
parser.add_argument(
    "--ace-playbook-scope",
    choices=["global", "benchmark", "category", "challenge"],
    default="benchmark",
    help="Scope for sharing ACE playbooks across challenges.",
)
parser.add_argument(
    "--ace-batch-size",
    type=int,
    default=None,
    help="Per-scope lane width: number of challenges to evaluate in parallel inside one ACE scope before one serial curate step.",
)
parser.add_argument(
    "--ace-batch-order",
    choices=["sorted", "random"],
    default="sorted",
    help="Ordering strategy for ACE batches within each playbook scope.",
)
parser.add_argument(
    "--ace-curate-mode",
    choices=["per-item", "batch"],
    default="batch",
    help="ACE update mode. Use batch for parallel solve + serial curate.",
)
parser.add_argument(
    "--ace-worker-allocation",
    choices=["lane-balanced", "global"],
    default="lane-balanced",
    help="How global workers are distributed across ACE scopes. lane-balanced is the recommended setting.",
)
```

**Step 2: Run help output**

Run:

```bash
python baseline/batch/run_batch_baseline.py --help
```

Expected: the new ACE options appear in the CLI help text.

**Step 3: Commit**

```bash
git add baseline/batch/run_batch_baseline.py
git commit -m "feat(batch): add ace batch orchestration flags"
```

### Task 1A: Define Scheduling Semantics

**Files:**
- Modify: `docs/plans/2026-04-20-ace-parallel-solve-serial-curate.md`
- Modify: `baseline/batch/run_batch_baseline.py`

**Step 1: Treat each scope as an independent lane**

Definitions:
- `max_workers`: global hard cap on simultaneously running workers
- `ace_batch_size`: per-scope lane width
- `scope`: grouping key derived from `global`, `benchmark`, `category`, or `challenge`
- `active_scopes`: number of scope keys that still have pending work

Required semantics:
- each scope key gets its own lane
- each lane may submit at most `ace_batch_size` items at once
- a lane only runs its curator step after **all items in that lane's current batch** finish
- global concurrency must never exceed `max_workers`

**Step 2: Preserve lane-balanced behavior**

For `ace_worker_allocation=lane-balanced`:
- each active lane gets up to `ace_batch_size` inflight items for its current batch
- no lane may submit a second batch before curating the first
- one hot scope must not consume the entire global worker pool

Example target behavior:
- `ace_playbook_scope=category`
- active categories = 4
- `ace_batch_size=6`
- `max_workers=24`

Desired result:
- `web` lane runs up to 6 items
- `crypto` lane runs up to 6 items
- `pwn` lane runs up to 6 items
- `rev` lane runs up to 6 items
- each lane curates independently after its own local batch completes

**Step 3: Keep `global` allocation as a fallback**

For `ace_worker_allocation=global`:
- fill the global pool in lane order
- still keep curation scope-local
- still forbid overlapping batches for the same lane

### Task 2: Add Scope and Batch Helpers

**Files:**
- Modify: `baseline/batch/run_batch_baseline.py`
- Test: ad hoc Python REPL or tiny temporary script invoking helper functions

**Step 1: Add a scope key helper**

Create a helper that derives one scope key from challenge metadata:

```python
def _ace_scope_key(scope: str, chal_id: str, meta: Dict[str, Any]) -> str:
    if scope == "global":
        return "global"
    if scope == "benchmark":
        return str(meta.get("benchmark", "unknown"))
    if scope == "category":
        return str(meta.get("category", "unknown"))
    if scope == "challenge":
        return chal_id
    raise ValueError(f"Unsupported ACE scope: {scope}")
```

**Step 2: Add grouping and chunking helpers**

Add helpers that:
- group `WorkItem`s by scope key
- apply either `sorted` or `random` ordering within each group
- split each group into windows of `ace_batch_size`
- compute lane-local pending counts for `lane-balanced` scheduling

Use a deterministic seed when `random` is selected:

```python
rng = random.Random(0)
```

Also add lane helpers:

```python
def _build_ace_lanes(
    work_items: list[WorkItem],
    challenges: dict[str, dict[str, Any]],
    scope: str,
    batch_order: str,
) -> dict[str, list[WorkItem]]:
    ...

def _pop_next_lane_batch(
    lane_items: list[WorkItem],
    ace_batch_size: int,
) -> list[WorkItem]:
    ...
```

**Step 3: Verify helper behavior**

Run a short Python snippet from repo root:

```bash
python - <<'PY'
from baseline.batch.run_batch_baseline import WorkItem
PY
```

Expected: module imports cleanly after helper additions.

**Step 4: Commit**

```bash
git add baseline/batch/run_batch_baseline.py
git commit -m "feat(batch): add ace scope grouping helpers"
```

### Task 3: Add ACE Batch Curator Module Skeleton

**Files:**
- Create: `baseline/batch/ace_curator.py`
- Test: import smoke test

**Step 1: Create the curator module**

Add a new module with these public entry points:

```python
def load_playbook(playbook_path: Path, initial_playbook: str) -> str: ...
def save_playbook(playbook_path: Path, playbook: str) -> None: ...
def collect_batch_artifacts(result_dirs: list[Path]) -> list[dict]: ...
def curate_batch_playbook(
    *,
    playbook: str,
    batch_artifacts: list[dict],
    llm_stub: Any,
    logger: logging.Logger,
) -> tuple[str, dict]:
    ...
```

Also add one helper for scope state persistence:

```python
def write_scope_state(
    scope_dir: Path,
    *,
    version: int,
    batch_index: int,
    summary: dict,
) -> None:
    ...
```

For the first implementation pass, `curate_batch_playbook()` can:
- flatten each item's `new_bullets`
- de-duplicate identical `(section, content)` pairs
- apply additions using logic extracted from `ace_agent`
- return both updated playbook and a curator summary dict

This keeps the first version deterministic and minimal. A later pass can upgrade this to an LLM-driven batch curator.

**Step 2: Import smoke test**

Run:

```bash
python - <<'PY'
from baseline.batch.ace_curator import curate_batch_playbook
print(curate_batch_playbook.__name__)
PY
```

Expected: prints `curate_batch_playbook`.

**Step 3: Commit**

```bash
git add baseline/batch/ace_curator.py
git commit -m "feat(batch): add ace batch curator module"
```

### Task 4: Refactor ACE Agent Into Artifact-Only and Persist Modes

**Files:**
- Modify: `baseline/agents/ace_agent.py`
- Test: targeted single-challenge run or focused import smoke test

**Step 1: Add explicit runtime knobs to `run_challenge()`**

Read these kwargs inside `run_challenge()`:

```python
ace_disable_persist = bool(kwargs.get("ace_disable_persist", False))
ace_playbook_snapshot = kwargs.get("ace_playbook_snapshot")
ace_scope_key = kwargs.get("ace_scope_key")
ace_playbook_version = kwargs.get("ace_playbook_version")
```

**Step 2: Prefer snapshot input over on-disk playbook**

Change playbook initialization to:

```python
if isinstance(ace_playbook_snapshot, str):
    playbook = ace_playbook_snapshot
else:
    playbook = _read_playbook(playbook_path) if playbook_path else _INITIAL_PLAYBOOK
```

**Step 3: Stop writing shared playbook in artifact-only mode**

Keep the per-item reflector, but when `ace_disable_persist=True`:
- do not call `_write_playbook()`
- do not mutate shared state on disk
- write one artifact file such as `ace_item_artifact.json`

The artifact should include:

```python
{
    "scope_key": ace_scope_key,
    "playbook_version": ace_playbook_version,
    "challenge_id": chal_id,
    "category": category,
    "benchmark": chal_data.get("benchmark", ""),
    "solved": solved,
    "reflection": reflection,
    "new_bullets": reflection.get("new_bullets", []) if reflection else [],
    "bullet_tags": reflection.get("bullet_tags", []) if reflection else [],
    "tokens": tokens,
}
```

**Step 4: Preserve legacy behavior**

When `ace_disable_persist` is false, keep current behavior so older workflows still run.

**Step 5: Verify module import**

Run:

```bash
python - <<'PY'
from baseline.agents.ace_agent import run_challenge
print(callable(run_challenge))
PY
```

Expected: prints `True`.

**Step 6: Commit**

```bash
git add baseline/agents/ace_agent.py
git commit -m "feat(ace): add artifact-only mode for batch curation"
```

### Task 5: Pass ACE Snapshot Context Through the Worker

**Files:**
- Modify: `baseline/batch/worker.py`
- Test: import smoke test

**Step 1: Extend `run_single_challenge()` signature**

Add optional parameters:

```python
ace_playbook_snapshot: Optional[str] = None,
ace_scope_key: Optional[str] = None,
ace_playbook_version: Optional[int] = None,
ace_disable_persist: bool = False,
```

**Step 2: Forward these values into `agent_module.run_challenge()`**

Example:

```python
agent_result = agent_module.run_challenge(
    ...,
    ace_playbook_snapshot=ace_playbook_snapshot,
    ace_scope_key=ace_scope_key,
    ace_playbook_version=ace_playbook_version,
    ace_disable_persist=ace_disable_persist,
    **extra_agent_kwargs,
)
```

**Step 3: Verify worker import**

Run:

```bash
python - <<'PY'
from baseline.batch.worker import run_single_challenge
print(run_single_challenge.__name__)
PY
```

Expected: prints `run_single_challenge`.

**Step 4: Commit**

```bash
git add baseline/batch/worker.py
git commit -m "feat(batch): pass ace snapshot context into workers"
```

### Task 6: Add ACE Batch Execution Path To Batch Runner

**Files:**
- Modify: `baseline/batch/run_batch_baseline.py`
- Test: focused dry run with one benchmark and a few challenges

**Step 1: Keep non-ACE behavior unchanged**

Wrap the existing worker dispatch path so it still handles every non-`ace_agent` run exactly as before.

**Step 2: Add a dedicated ACE branch**

When:

```python
args.agent == "ace_agent" and args.ace_curate_mode == "batch"
```

switch to this flow:

```python
lanes = _build_ace_lanes(...)
scope_state = {scope_key: {"version": 0, "batch_index": 0, "playbook": ...}}
while lanes still have pending items:
    submit at most one batch per active lane
    never exceed max_workers globally
    for each completed lane batch:
        collect ace_item_artifact.json files for that lane
        updated_playbook, curator_summary = curate_batch_playbook(...)
        persist updated scope-local playbook
        increment that lane's version counter
```

For `ace_worker_allocation=lane-balanced`, submission rules must be:
- each active lane gets up to `ace_batch_size` inflight items for its current batch
- no lane may submit a second batch before curating the first
- lane batches are independent; one lane does not wait for unrelated lanes before curating unless global worker starvation prevents submission

**Step 3: Ensure per-item checkpointing still happens**

After each future returns:
- append standard checkpoint records
- append normal `ChallengeResult`
- do not postpone result persistence until after curation
- record which lane the result belonged to so lane-complete detection is deterministic

**Step 4: Save group-level curator state**

For each batch write:
- `ace_state/<scope_key>/batch_0001_curator_input.json`
- `ace_state/<scope_key>/batch_0001_curator_output.json`
- `ace_state/<scope_key>/playbook_history/version_0001.txt`
- `ace_state/<scope_key>/state.json`

`state.json` should minimally include:

```json
{
  "scope_key": "crypto",
  "playbook_version": 3,
  "last_batch_index": 3,
  "total_items_curated": 18
}
```

**Step 5: Manual dry run**

Run:

```bash
python baseline/batch/run_batch_baseline.py \
  --agent ace_agent \
  --model <model-key> \
  --benchmark nyuctfbench \
  --samples 1 \
  --max-workers 2 \
  --ace-curate-mode batch \
  --ace-playbook-scope benchmark \
  --ace-batch-size 2
```

Expected:
- two challenge workers can run in parallel
- no worker writes shared `playbook.txt` directly
- one curator update occurs after the two-item batch completes

**Step 6: Commit**

```bash
git add baseline/batch/run_batch_baseline.py
git commit -m "feat(batch): orchestrate ace parallel solve and serial curate"
```

### Task 7: Add Minimal Regression Coverage

**Files:**
- Create: `tests/test_ace_batch_curator.py`
- Create or Modify: `tests/test_run_batch_baseline.py`

**Step 1: Add curator unit tests**

Cover:
- duplicate bullets across two artifacts are only added once
- missing artifact files do not crash curation
- playbook scope history files are written correctly
- lane-balanced scheduling never schedules more than one batch at a time per scope
- lane-balanced scheduling respects `max_workers`

Example:

```python
def test_curate_batch_playbook_deduplicates_identical_additions():
    playbook = "## STRATEGIES & INSIGHTS\n"
    artifacts = [
        {"new_bullets": [{"section": "strategies_and_insights", "content": "Use checksec first"}]},
        {"new_bullets": [{"section": "strategies_and_insights", "content": "Use checksec first"}]},
    ]
    updated, summary = curate_batch_playbook(
        playbook=playbook,
        batch_artifacts=artifacts,
        llm_stub=None,
        logger=logging.getLogger("test"),
    )
    assert updated.count("Use checksec first") == 1
```

**Step 2: Add grouping helper tests**

Cover:
- `global` scope groups everything together
- `benchmark` scope separates challenge groups
- chunking respects `ace_batch_size`
- category-scope lane extraction gives one independent lane per category
- with 4 active scopes and `ace_batch_size=6`, target concurrency is capped at 24 before applying the global `max_workers` cap

**Step 3: Run targeted tests**

Run:

```bash
pytest tests/test_ace_batch_curator.py tests/test_run_batch_baseline.py -v
```

Expected: PASS

**Step 4: Commit**

```bash
git add tests/test_ace_batch_curator.py tests/test_run_batch_baseline.py
git commit -m "test(batch): cover ace batch curation helpers"
```

### Task 8: Verify End-To-End Artifact Contract

**Files:**
- Modify as needed based on failures in earlier tasks
- Test using one tiny benchmark slice

**Step 1: Run one end-to-end batch**

Run a very small command against 2-4 challenges with `--max-workers 2`.

**Step 2: Inspect outputs**

Verify all of the following exist:
- per-challenge `result.json`
- per-challenge `trajectory.txt`
- per-challenge `reflection.json`
- per-challenge `ace_item_artifact.json`
- scope-level `playbook.txt`
- scope-level `playbook_history/version_*.txt`
- scope-level curator input/output logs

Also verify lane behavior:
- with `scope=category` and enough pending items, each category only curates after its own local batch completes
- no category starts batch 2 before curating batch 1

**Step 3: Confirm no concurrent playbook mutation**

Search for unexpected per-worker writes:

```bash
rg -n "_write_playbook|playbook.txt" baseline/agents/ace_agent.py baseline/batch -S
```

Expected: in batch mode, only the batch curator path writes shared playbooks.

**Step 4: Commit**

```bash
git add baseline/agents/ace_agent.py baseline/batch baseline/logs
git commit -m "feat(ace): finalize batch-scoped playbook evolution"
```

### Task 9: Document Operational Guidance

**Files:**
- Modify: `configs/baseline/ace_agent.yaml` if defaults should be documented there
- Modify: `README.md` or a relevant baseline doc if ACE usage is already documented

**Step 1: Add usage examples**

Document one recommended command:

```bash
python baseline/batch/run_batch_baseline.py \
  --agent ace_agent \
  --model Qwen3-235B-A22B-Instruct-2507-sii \
  --benchmark nyuctfbench \
  --max-workers 4 \
  --ace-curate-mode batch \
  --ace-playbook-scope benchmark \
  --ace-batch-size 4
```

**Step 2: Explain scope trade-offs briefly**

Include:
- `global` is highest sharing, highest contamination risk
- `benchmark` is recommended default
- `category` is good for cyber heterogeneity
- `challenge` is mainly for ablation or sanity checks
- `ace_batch_size` is per scope, not global
- recommended `max_workers ≈ active_scopes * ace_batch_size`
- `lane-balanced` is the recommended worker allocation mode

**Step 3: Run one final smoke check**

Run:

```bash
python baseline/batch/run_batch_baseline.py --help
```

Expected: docs and CLI stay aligned.

**Step 4: Commit**

```bash
git add configs/baseline/ace_agent.yaml README.md baseline/batch/run_batch_baseline.py
git commit -m "docs(ace): document batch curation workflow"
```
