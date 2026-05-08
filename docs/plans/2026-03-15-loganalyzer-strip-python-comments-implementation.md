# LogAnalyzer Python Comment Stripping Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Strip full-line Python comments from action code blocks in summary markdown.

**Architecture:** Add a small rendering-time sanitizer inside `LogAnalyzer`. Keep detection conservative so only Python-looking actions are cleaned, while shell commands remain untouched.

**Tech Stack:** Python, unittest

---

### Task 1: Add tests for summary action sanitization

**Files:**
- Create: `tests/test_loganalyzer_summary_rendering.py`
- Modify: `evolve/loganalyzer.py`

**Step 1: Write the failing tests**
- Test a Python action with leading comment lines: comment lines disappear in rendered markdown.
- Test inline Python comments: preserved.
- Test a shell action with `#`: unchanged.

**Step 2: Run test to verify it fails**
Run: `/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_loganalyzer_summary_rendering -v`
Expected: FAIL because sanitizer does not exist yet.

**Step 3: Write minimal implementation**
- Add helper(s) in `LogAnalyzer`.
- Apply helper in `_format_markdown_report` for both raw and summarized action blocks.

**Step 4: Run test to verify it passes**
Run: `/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m unittest tests.test_loganalyzer_summary_rendering -v`
Expected: PASS

**Step 5: Verify syntax**
Run: `/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python -m py_compile evolve/loganalyzer.py tests/test_loganalyzer_summary_rendering.py`
Expected: PASS
