# Prompt Variant CLI Override Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `--prompt-variant` CLI override that temporarily switches challenge prompt materialization between `zero_day` and `one_day` for a single run.

**Architecture:** Keep the override entirely inside `run_evolve_batch_skill.py`. Parse a run-level argument, apply it to a copied `chal_data` object after challenge loading, and let the existing prompt materialization pipeline consume the updated `default_variant`.

**Tech Stack:** Python, argparse, unittest

---

### Task 1: Add failing tests for runner-level prompt variant override

**Files:**
- Modify: `tests/test_run_evolve_batch_skill_scheduler.py`
- Test: `tests/test_run_evolve_batch_skill_scheduler.py`

**Step 1: Write the failing test**

Add tests covering:

- overriding `default_variant` when the requested variant exists
- ignoring the override when the variant is unsupported
- preserving the original `chal_data`

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_run_evolve_batch_skill_scheduler -v
```

Expected: new prompt-variant tests fail because the helper/CLI behavior does not exist yet.

**Step 3: Write minimal implementation**

Add a small helper in `run_evolve_batch_skill.py` that:

- accepts `chal_data` and `prompt_variant`
- returns a copied challenge dict when overriding
- leaves unsupported cases unchanged

**Step 4: Run test to verify it passes**

Run:

```bash
python -m unittest tests.test_run_evolve_batch_skill_scheduler -v
```

Expected: prompt-variant tests pass.

### Task 2: Wire the CLI argument into challenge submission

**Files:**
- Modify: `run_evolve_batch_skill.py`
- Test: `tests/test_run_evolve_batch_skill_scheduler.py`

**Step 1: Write the failing test**

Add or extend a scheduler test so `fill_available_challenge_slots(...)` applies the override before submission.

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_run_evolve_batch_skill_scheduler -v
```

Expected: the submitted challenge data still uses the original `default_variant`.

**Step 3: Write minimal implementation**

- Add `--prompt-variant` to `parse_args()`
- Thread the parsed value into challenge loading/submission
- Apply the override only in the main runner path

**Step 4: Run test to verify it passes**

Run:

```bash
python -m unittest tests.test_run_evolve_batch_skill_scheduler -v
```

Expected: submitted challenge data reflects the requested prompt variant.

### Task 3: Run focused regression

**Files:**
- Test: `tests/test_run_evolve_batch_skill_scheduler.py`
- Test: `tests/test_prompt_profile_overlay.py`
- Test: `tests/test_cvebench_instance_templates.py`

**Step 1: Run focused regression**

```bash
python -m unittest tests.test_run_evolve_batch_skill_scheduler tests.test_prompt_profile_overlay tests.test_cvebench_instance_templates -v
```

Expected: all tests pass.

**Step 2: Review working tree**

Run:

```bash
git status --short
```

Expected: only the intended runner/test/doc files are newly modified by this task.
