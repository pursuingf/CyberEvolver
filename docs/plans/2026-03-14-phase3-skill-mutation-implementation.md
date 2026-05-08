# Phase 3 Skill Mutation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow refiner phase 3 to create, modify, and delete files under `skills/`, while blocking deletion of `skills/skill_template`.

**Architecture:** The change stays localized to phase-3 prompt generation and validator logic in `evolve/refiner_agent.py`. Tests exercise the phase-3 patch application path so the new behavior is verified through the same validation entrypoint used in production.

**Tech Stack:** Python, unittest, pytest

---

### Task 1: Add failing phase-3 validation tests

**Files:**
- Create: `tests/test_refiner_phase3_validation.py`
- Modify: none
- Test: `tests/test_refiner_phase3_validation.py`

**Step 1: Write the failing test**

Add tests that expect phase 3 to:

- accept `<replace_code>` on `skills/existing/SKILL.md`
- accept `<delete_file>` on `skills/existing/description.md`
- reject `<delete_file>` on `skills/skill_template/SKILL.md`
- reject a non-`skills/` path such as `agent.py`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_refiner_phase3_validation.py -q`
Expected: failures showing phase 3 still rejects `replace_code` and `delete_file`

**Step 3: Write minimal implementation**

Update `evolve/refiner_agent.py` phase-3 prompt policy and validator logic to match the desired behavior.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_refiner_phase3_validation.py -q`
Expected: all new tests pass

### Task 2: Run focused regression checks

**Files:**
- Modify: `evolve/refiner_agent.py`
- Test: `tests/test_refiner_phase3_validation.py`
- Test: `tests/test_refiner_unicode_validation.py`

**Step 1: Run targeted regression tests**

Run:

```bash
pytest tests/test_refiner_phase3_validation.py tests/test_refiner_unicode_validation.py -q
```

Expected: all targeted refiner validation tests pass

**Step 2: Review the diff**

Confirm the change is limited to phase 3 behavior and does not relax path safety outside `skills/`.
