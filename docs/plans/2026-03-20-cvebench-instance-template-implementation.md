# CVE Bench Instance Template Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the CVE Bench `zero_day` and `one_day` instance prompts with Jinja templates that preserve upstream wording and support runtime alias rendering for single-service and multi-service challenges.

**Architecture:** Keep `skill_based` as the default prompt source and only rewrite the CVE Bench family templates. The templates consume shallow `instance_data` fields plus runtime `service_names`, then assemble endpoint strings inside Jinja instead of Python.

**Tech Stack:** Jinja templates, existing prompt profile loader, lightweight local render verification

---

### Task 1: Rewrite the `zero_day` template

**Files:**
- Modify: `.worktrees/cvebench-network-scorer/benchmarks/prompt_profiles/cvebench/zero_day/instance_template.txt`

**Step 1: Write the failing test**

Create a local render snippet for a multi-service sample and confirm the current template still contains hardcoded benchmark text instead of Jinja-driven endpoints.

**Step 2: Run test to verify it fails**

Run a small render command against the current template and confirm the output still shows hardcoded `target:9090`-style strings.

**Step 3: Write minimal implementation**

Replace the body with a Jinja template that:
- computes rendered application endpoints
- computes rendered allowed endpoints with fallback
- computes the proof-upload endpoint
- preserves the original zero-day prompt style

**Step 4: Run test to verify it passes**

Render the template with:
- a single-service sample
- a multi-service sample

Confirm the output uses runtime aliases and no longer depends on hardcoded hostnames.

**Step 5: Commit**

Commit the template rewrite after verification.

### Task 2: Rewrite the `one_day` template

**Files:**
- Modify: `.worktrees/cvebench-network-scorer/benchmarks/prompt_profiles/cvebench/one_day/instance_template.txt`

**Step 1: Write the failing test**

Reuse the local render snippet and confirm the current template still contains fixed vulnerability text and non-Jinja placeholders.

**Step 2: Run test to verify it fails**

Render the current template and confirm it does not consume `instance_data.cve_description` and runtime service aliases correctly.

**Step 3: Write minimal implementation**

Replace the body with a Jinja template that:
- shares the same endpoint assembly pattern as `zero_day`
- inserts `instance_data.cve_description`
- preserves the original one-day wording and attack list

**Step 4: Run test to verify it passes**

Render the template with representative challenge data and verify the vulnerability description and multi-service endpoint rendering are correct.

**Step 5: Commit**

Commit the template rewrite after verification.

### Task 3: Verify prompt rendering

**Files:**
- Modify: `.worktrees/cvebench-network-scorer/benchmarks/prompt_profiles/cvebench/zero_day/instance_template.txt`
- Modify: `.worktrees/cvebench-network-scorer/benchmarks/prompt_profiles/cvebench/one_day/instance_template.txt`

**Step 1: Write the failing test**

Prepare one render case matching `CVE-2024-30542` with `target` and `mailhog`, plus one simpler single-service case.

**Step 2: Run test to verify it fails**

Confirm the old templates do not render those cases into the expected endpoint strings.

**Step 3: Write minimal implementation**

Only adjust the templates if the verification reveals formatting problems or missing fallbacks.

**Step 4: Run test to verify it passes**

Run the local render verification and check for:
- comma-joined multi-service endpoints
- proof-upload endpoint rendering
- correct fallback to canonical service names when alias data is absent
- unchanged operational guidance block

**Step 5: Commit**

Commit any follow-up fixes needed for the verification.
