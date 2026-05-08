# Intro Left Benchmark Figure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a standalone intro-left bar chart under `figure/` with four benchmark groups on the x-axis, five averaged methods per group, and a softer paper-facing color hierarchy.

**Architecture:** Keep the existing curated experiment JSON as the single source of truth. Add one new plotting module under `figure/` that computes benchmark-wise averages across the four models, renders a standalone left-panel figure with the requested palette, and saves both vector and preview outputs into `figure/`.

**Tech Stack:** Python 3, `json`, `numpy`, `matplotlib`, `unittest`

---

### Task 1: Add a failing benchmark-aggregation test

**Files:**
- Create: `tests/test_plot_intro_left_benchmarks.py`
- Test: `python -m unittest tests.test_plot_intro_left_benchmarks -v`

**Step 1: Write the failing test**

The test should import `figure/plot_intro_left_benchmarks.py` and verify:
- x-axis benchmark order is `NYU CTF`, `AutoPenBench`, `CVEBench Zero-Day`, `CVEBench One-Day`
- method order is the five requested bars
- each bar value is the equal-weight average across available models for that benchmark

**Step 2: Run the test to verify it fails**

Run:

```bash
python -m unittest tests.test_plot_intro_left_benchmarks -v
```

Expected: failure because the new plotting module does not exist yet.

### Task 2: Implement the standalone left benchmark chart

**Files:**
- Create: `figure/plot_intro_left_benchmarks.py`
- Test: `tests/test_plot_intro_left_benchmarks.py`

**Step 1: Add data loading and benchmark-aggregation helpers**

Requirements:
- read `logs/final_data/experiment_data_full.json`
- aggregate each benchmark across the four models
- produce five values per benchmark: `Baseline-Single`, `Baseline-Multi`, `Raw pass@1`, `Raw pass@16`, `Ours (Evo)`

**Step 2: Render a paper-facing grouped bar chart**

Requirements:
- output to `figure/fig_intro_left_benchmarks.pdf`
- also save `figure/fig_intro_left_benchmarks.png`
- highlight `Ours (Evo)` with `#a985ca`
- keep raw bars in a blue family
- keep baseline bars in a lighter, softer family with higher transparency

### Task 3: Verify and polish the exported figure

**Files:**
- Modify: `figure/plot_intro_left_benchmarks.py`

**Step 1: Run the focused unit test**

```bash
python -m unittest tests.test_plot_intro_left_benchmarks -v
```

Expected: pass.

**Step 2: Render the figure with the matplotlib-capable interpreter**

```bash
/usr/bin/python3.10 figure/plot_intro_left_benchmarks.py
```

Expected: both PDF and PNG appear under `figure/`.

**Step 3: Inspect the PNG and revise once if needed**

Focus:
- soft but readable baselines
- clear separation between raw and evo bars
- x tick labels and legend readability after down-scaling
