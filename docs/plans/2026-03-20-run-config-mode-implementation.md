# Run Config Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a required `--config-mode {evo,raw}` argument to `run_evolve_batch_skill.py` and route runtime config selection through a copied profile.

**Architecture:** Keep the change local to `run_evolve_batch_skill.py`. Parse the required mode, resolve the selected config through a helper, and thread that selected config through the existing main flow instead of directly reading `EVO_CONFIG`.

**Tech Stack:** Python, argparse, unittest

---

### Task 1: Add parser and resolver tests

**Files:**
- Modify: `tests/test_run_evolve_batch_skill_scheduler.py`
- Test: `tests/test_run_evolve_batch_skill_scheduler.py`

**Step 1: Write failing tests**

Add tests covering:

- missing `--config-mode` raises `SystemExit`
- `parse_args(["--config-mode", "evo"])` succeeds
- `parse_args(["--config-mode", "raw"])` succeeds
- the selected config resolver returns a copy and applies `--model` override without mutating module globals

**Step 2: Run test to verify it fails**

Run:

```bash
/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_run_evolve_batch_skill_scheduler -v
```

Expected: failure because the parser or resolver behavior does not yet exist.

### Task 2: Implement required config mode parsing

**Files:**
- Modify: `run_evolve_batch_skill.py`

**Step 1: Add required parser argument**

Add:

```python
parser.add_argument(
    "--config-mode",
    required=True,
    choices=["evo", "raw"],
    help="Select the run config profile.",
)
```

**Step 2: Add config resolver helper**

Implement a helper that:

- selects `EVO_CONFIG` or `RAW_CONFIG`
- returns a copied dict
- applies `--model` override to that copy

**Step 3: Replace direct `EVO_CONFIG` usage in main**

Use the resolved config for:

- run metadata
- `chal_llm_kwargs`
- `mut_llm_kwargs`
- `evo_config=...` calls in the main flow

### Task 3: Verify and clean up

**Files:**
- Modify: `run_evolve_batch_skill.py`
- Test: `tests/test_run_evolve_batch_skill_scheduler.py`

**Step 1: Run tests**

Run:

```bash
/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_run_evolve_batch_skill_scheduler -v
```

Expected: pass

**Step 2: Run syntax verification**

Run:

```bash
/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m py_compile run_evolve_batch_skill.py tests/test_run_evolve_batch_skill_scheduler.py
```

Expected: pass

**Step 3: Run CLI smoke**

Run:

```bash
/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python run_evolve_batch_skill.py --help
```

Expected: help output includes required `--config-mode`
