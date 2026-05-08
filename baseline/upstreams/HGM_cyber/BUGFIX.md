# HGM Cyber Bugfix Notes

## Bug 1: List Aliasing in `tree.py::get_pseudo_decendant_evals` (Upstream)

**Source**: upstream HGM repo (`tree.py`), present in both SWE-bench and cyber versions.

**Severity**: High — corrupts Thompson Sampling data, inflates `num_evals`, distorts `mean_utility`.

### Root Cause

`get_pseudo_decendant_evals()` returns `self.utility_measures` by reference (not a copy) when `num_evals < num_pseudo`. The caller `get_decendant_evals()` then does `decendant_evals += descendant.utility_measures`, which is an in-place `list.extend()` that permanently appends descendant data into the parent's `utility_measures`.

```python
# BEFORE (buggy)
def get_pseudo_decendant_evals(self, num_pseudo):
    return self.utility_measures if self.num_evals < num_pseudo \
           else [self.mean_utility] * num_pseudo
    #      ↑ returns same reference    ↑ returns new list (safe)
```

### Impact

Each call to `expand()` triggers `get_decendant_evals()` for every node. For a parent node with one child:

```
Initial:      utility_measures = [1,1,0,0,0,0]  (6 items, 2/6 solved)
After 1 call: [1,1,0,0,0,0] + child_evals(17)  → 23 items
After 2 calls: 23 + 17 → 40 items
...
After 588 calls: 10002 items                    → reaches threshold, stops
```

Final state: `num_evals = 10002` (real: 6), `mean_utility ≈ child's mean` (real: 0.333).

The growth stops at `n_pseudo_descendant_evals` (default 10000) because the `else` branch returns a new list.

**Formula**: `final_num_evals = own_evals + ⌈(threshold - own_evals) / child_evals⌉ × child_evals`

This also corrupts the `sample()` function which uses per-node `utility_measures` for Thompson Sampling — inflated `num_evals` makes the Beta distribution extremely narrow, suppressing exploration.

### Evidence This Is a Bug, Not Design

1. **Paper (arXiv:2510.21614)** describes CMP using scalar counters (`n_success_C`, `n_failure_C`), not list mutation. `n_pseudo_descendant_evals` does not appear in the paper.
2. **The `else` branch** returns a new list `[mean] * num_pseudo` — if mutation were intentional, both branches would mutate.
3. **`sample()` function** reads `node.utility_measures` directly for per-node TS. After corruption, this becomes clade-level data, defeating the purpose of having separate expand/evaluate sampling.
4. **Cumulative effect is absurd**: the same child data is appended hundreds of times.

### Fix

One-line change — return a copy:

```python
# AFTER (fixed)
def get_pseudo_decendant_evals(self, num_pseudo):
    return list(self.utility_measures) if self.num_evals < num_pseudo \
           else [self.mean_utility] * num_pseudo
```

---

## Bug 2: Diagnose Prompt Exceeds Model Context (Migration)

**Source**: our migration — upstream targets 200K-context models; we use Kimi-K2.5 (131K context).

**Severity**: Critical — caused 98.8% expand failure rate (1570/1590) in production run.

### Root Cause

The diagnose prompt's system message embeds `seed_code + all ancestor model_patch.diff` files via `get_current_code()`. Each `model_patch.diff` was 150-400KB because the container's `.gitignore` only excluded `__pycache__/`, so `self_evo.md` (the coding agent's full conversation log, ~150KB) was included in every diff.

With even one ancestor, the system message exceeded 131K tokens. All 1570 failures reported exactly `126977 input tokens`.

### Upstream comparison

Upstream HGM has the same issue: no `.gitignore` in the container, `self_evo.md` (186KB in their `best_agent/`) also leaks into `model_patch.diff`. Verified in `HGM/hgm_utils.py` — no `.gitignore` is created, and `diff_versus_commit()` includes all untracked files via `git ls-files --others`. It works for them because they use 200K-context models (Claude/GPT-4).

### Fix

Exclude non-code artifacts from git diff in `hgm_cyber_utils.py::sample_child()`:

```python
# BEFORE: only __pycache__ excluded → self_evo.md (150KB) leaks into every patch
container.exec_run("echo __pycache__/ > /hgm/.gitignore")

# AFTER: exclude chat logs and artifacts → patches shrink from 150-400KB to 5-30KB
container.exec_run("printf '__pycache__/\nself_evo.md\nchat_history.md\n*.log\n' > /hgm/.gitignore")
```

Result: patches shrink by ~92% (182KB → 25KB per patch). A 3-level ancestor chain goes from ~750KB to ~75KB (~19K tokens), well within 131K context.

The upstream `seed + patches` approach is preserved — the diagnose LLM still sees the original code plus what changed at each evolution step.

---

## Bug 3: `find_best_nodes()` Uses Corrupted Metadata (Our Code)

**Source**: `eval_best_agent.py` (our new code, not upstream).

### Root Cause

`find_best_nodes()` reads `mean_utility` and `num_evals` from `hgm_metadata.jsonl`, which is corrupted by Bug 1. For the initial node it reads from `metadata.json` (correct), creating an inconsistent comparison.

Example from production run:
- Initial: `mean_utility = 0.250` (from metadata.json, real 4/16)
- Node 1: `mean_utility = 0.134` (from hgm_metadata.jsonl, corrupted; real 2/6 = 0.333)
- Result: selected initial (0.25) instead of Node 1 (real 0.333)

### Fix

Read actual `total_resolved_instances / total_submitted_instances` from each node's own `metadata.json` instead of trusting `hgm_metadata.jsonl`.

---

## Bug 4: No Harness-Level Scoring (Seed Agent Design)

**Source**: seed agent design gap.

### Root Cause

The seed agent relied on the LLM voluntarily calling the `check_done` tool to detect success. In practice, Kimi-K2.5 never called it (0/40 challenges). The agent would do a few reconnaissance steps, get `finish_reason=stop`, and exit — even when it had accidentally triggered a successful exploit.

Additionally, `solved` was detected via string matching (`"Challenge SOLVED" in str(chat_history)`), which is fragile and prone to false positives.

### Fix

Added harness-level auto-check in `llm_withtools.py`: after every tool call, poll the `SCORING_URL` endpoint. If the scoring service reports success, the agent loop exits with `solved=True`.

- `_auto_check_scoring()` returns a boolean, propagated through `chat_with_agent()` → `cyber_agent.py`
- No more string matching — `solved` is a deterministic flag from the scoring endpoint
- `check_done` tool retained for explicit agent use (provides feedback to LLM)
