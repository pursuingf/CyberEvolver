# Pass@3 Campaign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a reproducible pass@3 campaign runner for baseline agents and ACE online, with strict no-think model routing and a correct pass@3 summarizer.

**Architecture:** Add one shared model-kwargs normalization path that disables thinking for in-process agents and propagates the same intent to subprocess-based upstream baselines. Build one campaign shell entrypoint that runs stages in the requested priority order, and one Python summarizer that computes pass@3 from `batch_results.json` for both `samples=3` runs and three independent ACE online runs.

**Tech Stack:** Bash, Python, pytest, existing batch runner and watcher scripts

---

### Task 1: Write the failing tests for model no-think normalization

**Files:**
- Modify: `tests/test_ace_batch_runner_helpers.py`
- Modify: `baseline/batch/run_batch_baseline.py`

**Step 1: Write the failing test**

Add tests that expect a shared helper to:
- force `thinking=False`
- force `chat_template_kwargs.enable_thinking=False`
- preserve existing model fields such as `model`, `openai_api_base`, `temperature`, and `max_tokens`

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_ace_batch_runner_helpers.py -k no_think`

Expected: FAIL because the helper does not yet exist or is not wired in the batch runner.

**Step 3: Write minimal implementation**

Add a normalization helper in `baseline/batch/run_batch_baseline.py` and call it after loading and merging `model_kwargs`.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_ace_batch_runner_helpers.py -k no_think`

Expected: PASS

### Task 2: Write the failing tests for subprocess no-think propagation

**Files:**
- Modify: `tests/test_run_script_helpers.py`
- Modify: `baseline/agents/upstream_runner.py`

**Step 1: Write the failing test**

Add tests that expect `build_llm_env()` to export no-think hints for subprocess baselines, including:
- `OPENAI_API_BASE`
- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- one explicit no-think env contract shared by wrappers, such as `OPENAI_ENABLE_THINKING=false`

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_run_script_helpers.py -k think`

Expected: FAIL because the env contract is not emitted yet.

**Step 3: Write minimal implementation**

Update `baseline/agents/upstream_runner.py` so subprocess wrappers receive a stable no-think env contract in addition to the existing base URL and API key.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_run_script_helpers.py -k think`

Expected: PASS

### Task 3: Write the failing tests for pass@3 summarization

**Files:**
- Create: `tests/test_pass3_summary.py`
- Create: `scripts/summarize_pass3.py`

**Step 1: Write the failing test**

Add tests that cover:
- one `batch_results.json` with three samples per challenge
- three independent run directories for ACE online
- correct challenge-level pass@3 calculation
- manifest-style output paths and stable markdown/JSON summaries

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_pass3_summary.py`

Expected: FAIL because the summarizer does not yet exist.

**Step 3: Write minimal implementation**

Create `scripts/summarize_pass3.py` with:
- `single-run` mode for `samples=3`
- `multi-run` mode for ACE online three-run aggregation
- challenge-level `pass@3` plus solved counts

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_pass3_summary.py`

Expected: PASS

### Task 4: Write the failing tests for campaign command generation

**Files:**
- Modify: `tests/test_run_script_helpers.py`
- Create: `scripts/run_pass3_campaign.bash`

**Step 1: Write the failing test**

Add assertions for the generated campaign layout:
- stage priority is `autopenbench -> nyuctfbench -> ace_online -> cvebench`
- every model uses worker count `24`
- ACE online runs three independent runs instead of `ACE_SAMPLES=3`
- baseline runs use `--samples 3`

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_run_script_helpers.py -k pass3_campaign`

Expected: FAIL because the script does not yet exist.

**Step 3: Write minimal implementation**

Create `scripts/run_pass3_campaign.bash` that:
- accepts `MODELS`, `RUN_STAGES`, and `DRY_RUN`
- runs baseline agents directly via `baseline/batch/run_batch_baseline.py`
- runs ACE online through `scripts/watch_model_and_run_ace_benchmarks.bash`
- stores a campaign manifest under `baseline/logs/batch/pass3_campaign/<campaign_id>/`

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_run_script_helpers.py -k pass3_campaign`

Expected: PASS

### Task 5: Implement runtime no-think propagation in baseline wrappers

**Files:**
- Modify: `baseline/agents/nyuctf_single.py`
- Modify: `baseline/agents/dcipher.py`
- Modify: `baseline/agents/cy_agent.py`

**Step 1: Write the failing test**

Extend tests to expect wrapper-generated config or launch payloads to carry no-think information when possible.

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_run_script_helpers.py -k no_think_wrapper`

Expected: FAIL because the wrappers do not yet translate the env contract into request parameters.

**Step 3: Write minimal implementation**

Update wrappers so:
- `nyuctf_single` patches the upstream OpenAI client with no-think defaults when supported
- `dcipher` writes no-think settings into generated config or CLI flags
- `cy_agent` injects no-think fields into its rendered launch payload or patched OpenAI client

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_run_script_helpers.py -k no_think_wrapper`

Expected: PASS

### Task 6: Verify end-to-end targeted checks

**Files:**
- Modify: `scripts/run_pass3_campaign.bash`
- Modify: `scripts/summarize_pass3.py`

**Step 1: Run focused test suites**

Run:
- `pytest -q tests/test_ace_batch_runner_helpers.py tests/test_run_script_helpers.py tests/test_pass3_summary.py`

Expected: PASS

**Step 2: Smoke-check campaign script**

Run:
- `DRY_RUN=1 MODELS='Kimi-K2.5-sii' bash scripts/run_pass3_campaign.bash`

Expected:
- prints stage-ordered commands
- shows baseline `samples=3`
- shows ACE online as three independent runs

**Step 3: Smoke-check summarizer**

Run:
- `python scripts/summarize_pass3.py --help`

Expected: usage text with single-run and multi-run style arguments

**Step 4: Commit**

```bash
git add docs/plans/2026-05-01-pass3-campaign.md \
  baseline/batch/run_batch_baseline.py \
  baseline/agents/upstream_runner.py \
  baseline/agents/nyuctf_single.py \
  baseline/agents/dcipher.py \
  baseline/agents/cy_agent.py \
  scripts/run_pass3_campaign.bash \
  scripts/summarize_pass3.py \
  tests/test_ace_batch_runner_helpers.py \
  tests/test_run_script_helpers.py \
  tests/test_pass3_summary.py
git commit -m "feat(baseline): add pass@3 campaign runner"
```
