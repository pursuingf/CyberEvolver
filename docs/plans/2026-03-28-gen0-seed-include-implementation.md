# Gen0 Seed Include Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let `run_evolve_batch_skill.py` create `gen0_root` with only explicitly selected `commands/` and `skills/` entries from the seed template, while keeping base framework files intact.

**Architecture:** The runner will accept repeatable `--seed-include` arguments and pass them into `EvolutionOrchestrator`. The orchestrator will materialize the root node in two phases: copy the non-tool framework files from the seed, then copy only the requested `commands/` files and `skills/` directories. Because runtime command and skill loading already scans the node filesystem, no runtime-specific filtering is needed.

**Tech Stack:** Python 3, argparse, pathlib, shutil, unittest

---

### Task 1: Add failing tests for filtered gen0 materialization

**Files:**
- Modify: `/data/pxd-team/workspace/fyh/evolve_ctf_agent/.worktrees/cvebench-network-scorer/tests/test_prompt_profile_overlay.py`
- Test: `/data/pxd-team/workspace/fyh/evolve_ctf_agent/.worktrees/cvebench-network-scorer/tests/test_prompt_profile_overlay.py`

**Step 1: Write the failing test**

Add tests that create an orchestrator with `base_seed_path=gen0_root/skill_based` and a new `seed_includes` argument, then assert:

- `gen0_root/src/commands/load_skill.py` exists when included
- `gen0_root/src/commands/submit.py` exists when included
- unselected command files are absent
- `gen0_root/src/agent.py` still exists
- `gen0_root/src/system_template.txt` still exists

Add a second test that passes an invalid include path and asserts that `init_generation_zero()` raises `ValueError`.

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_prompt_profile_overlay.PromptProfileOverlayTests.test_init_generation_zero_copies_only_selected_seed_tools tests.test_prompt_profile_overlay.PromptProfileOverlayTests.test_init_generation_zero_rejects_invalid_seed_include -v
```

Expected: FAIL because `EvolutionOrchestrator` does not yet accept or enforce seed includes.

**Step 3: Write minimal implementation**

Do not implement yet. Move to Task 2 after observing the failure.

**Step 4: Run test to verify it still fails for the intended reason**

Run the same command and confirm the failure is due to missing seed include support, not a broken test fixture.

**Step 5: Commit**

Do not commit yet. Wait until the implementation passes.

### Task 2: Wire CLI and orchestrator support for seed includes

**Files:**
- Modify: `/data/pxd-team/workspace/fyh/evolve_ctf_agent/.worktrees/cvebench-network-scorer/run_evolve_batch_skill.py`
- Modify: `/data/pxd-team/workspace/fyh/evolve_ctf_agent/.worktrees/cvebench-network-scorer/evolve/orchestrator.py`

**Step 1: Write the failing test**

No new test. Use the failing tests from Task 1.

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_prompt_profile_overlay.PromptProfileOverlayTests.test_init_generation_zero_copies_only_selected_seed_tools tests.test_prompt_profile_overlay.PromptProfileOverlayTests.test_init_generation_zero_rejects_invalid_seed_include -v
```

Expected: FAIL.

**Step 3: Write minimal implementation**

Implement:

- a repeatable `--seed-include` CLI argument in `run_evolve_batch_skill.py`
- passing that list into `EvolutionOrchestrator(...)`
- storing the normalized include list on the orchestrator
- replacing blind root `copy_from()` behavior with filtered root materialization logic

Keep child-node copying unchanged.

**Step 4: Run test to verify it passes**

Run:

```bash
python -m unittest tests.test_prompt_profile_overlay.PromptProfileOverlayTests.test_init_generation_zero_copies_only_selected_seed_tools tests.test_prompt_profile_overlay.PromptProfileOverlayTests.test_init_generation_zero_rejects_invalid_seed_include -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add evolve/orchestrator.py run_evolve_batch_skill.py tests/test_prompt_profile_overlay.py
git commit -m "feat(evolve): support filtered gen0 seed tools"
```

### Task 3: Verify runtime prompt/tool visibility follows filtered root contents

**Files:**
- Modify: `/data/pxd-team/workspace/fyh/evolve_ctf_agent/.worktrees/cvebench-network-scorer/tests/test_run_evolve_batch_skill_scheduler.py`
- Test: `/data/pxd-team/workspace/fyh/evolve_ctf_agent/.worktrees/cvebench-network-scorer/tests/test_run_evolve_batch_skill_scheduler.py`

**Step 1: Write the failing test**

Add a focused test around `run_node_task()` setup that uses a fake node with a filtered `commands/` directory. Assert that only selected command docs are concatenated into `cmd_docs`.

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_run_evolve_batch_skill_scheduler.GlobalRollingSchedulerTests.test_run_node_task_only_includes_selected_command_docs -v
```

Expected: FAIL until the test fixture reflects the filtered root behavior correctly.

**Step 3: Write minimal implementation**

Only adjust fixtures or helper behavior if needed. Do not add runtime filtering logic unless the test proves a genuine gap remains after Task 2.

**Step 4: Run test to verify it passes**

Run:

```bash
python -m unittest tests.test_run_evolve_batch_skill_scheduler.GlobalRollingSchedulerTests.test_run_node_task_only_includes_selected_command_docs -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_run_evolve_batch_skill_scheduler.py
git commit -m "test(evolve): verify filtered seed tools shape prompt docs"
```

### Task 4: Run focused regression

**Files:**
- Test: `/data/pxd-team/workspace/fyh/evolve_ctf_agent/.worktrees/cvebench-network-scorer/tests/test_prompt_profile_overlay.py`
- Test: `/data/pxd-team/workspace/fyh/evolve_ctf_agent/.worktrees/cvebench-network-scorer/tests/test_run_evolve_batch_skill_scheduler.py`

**Step 1: Run the focused suite**

Run:

```bash
python -m unittest tests.test_prompt_profile_overlay tests.test_run_evolve_batch_skill_scheduler -v
```

Expected: PASS.

**Step 2: Spot-check CLI parsing**

Run:

```bash
python run_evolve_batch_skill.py --help
```

Expected: help output includes `--seed-include`.

**Step 3: Commit final implementation if needed**

```bash
git add docs/plans/2026-03-28-gen0-seed-include-design.md docs/plans/2026-03-28-gen0-seed-include-implementation.md evolve/orchestrator.py run_evolve_batch_skill.py tests/test_prompt_profile_overlay.py tests/test_run_evolve_batch_skill_scheduler.py
git commit -m "feat(evolve): support selective gen0 seed tools"
```
