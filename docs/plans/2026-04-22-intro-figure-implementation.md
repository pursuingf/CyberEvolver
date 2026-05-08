# Intro Figure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a new intro figure script that renders a two-panel paper figure with model-wise averaged grouped bars on the left and the aggregated evo-vs-raw trajectory on the right.

**Architecture:** Read the existing curated data from `logs/final_data/experiment_data_full.json`, compute one left-panel summary by averaging each method across the four benchmark families for each model, and reuse the existing aggregated evo/raw data for the right panel. Keep the new logic isolated in a standalone plotting script so the current figure scripts remain unchanged.

**Tech Stack:** Python 3, `matplotlib`, `numpy`, `json`, `unittest`

---

### Task 1: Add a failing test for intro figure aggregation

**Files:**
- Create: `tests/test_plot_intro_figure.py`
- Test: `python -m unittest tests.test_plot_intro_figure -v`

**Step 1: Write a test that imports the new plotting module and checks per-model averaged bars**

Expected checks:
- grouped bars are averaged equally across the four benchmark groups
- the method order matches the intended left-panel legend
- the output payload includes all four models

**Step 2: Run the test to verify it fails**

Run:

```bash
python -m unittest tests.test_plot_intro_figure -v
```

Expected: failure because `scripts/plot_intro_figure.py` does not exist yet.

### Task 2: Implement the new intro figure script

**Files:**
- Create: `scripts/plot_intro_figure.py`
- Test: `python -m unittest tests.test_plot_intro_figure -v`

**Step 1: Add data loading and aggregation helpers**

Implementation requirements:
- read `main_results`, `raw_pass_at_k`, and `evo_node_escalation`
- compute left-panel model-wise averages for `Baseline-Single`, `Baseline-Multi`, `Raw pass@1`, `Raw pass@16`, and `Ours (Evo)`
- compute right-panel aggregated mean curves aligned on indices `1..16`

**Step 2: Render and save the two-panel figure**

Output targets:
- `logs/final_data/fig_intro_main.pdf`
- `logs/final_data/fig_intro_main.png`

### Task 3: Verify rendering end-to-end

**Files:**
- Modify: `scripts/plot_intro_figure.py`

**Step 1: Run the focused unit test**

```bash
python -m unittest tests.test_plot_intro_figure -v
```

Expected: pass.

**Step 2: Run the rendering command**

```bash
python scripts/plot_intro_figure.py --output logs/final_data/fig_intro_main.pdf
```

Expected: both PDF and PNG are written successfully.

**Step 3: Inspect the PNG visually and adjust styling if needed**

Focus:
- evo bar emphasis
- readable labels in the left panel
- clean spacing between the two panels
