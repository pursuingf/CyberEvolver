#!/usr/bin/env python3
"""Analyze dcipher batch run logs — count rounds per agent role.

Usage:
    python baseline/scripts/analyze_dcipher_rounds.py <batch_log_dir>

Outputs:
    <batch_log_dir>/step_details.json   — per-challenge breakdown
    <batch_log_dir>/step_summary.md     — human-readable summary
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


_SECTION_RE = re.compile(r"^=+ (PLANNER|EXECUTOR|EXECUTOR DONE) =+$")


def parse_upstream_log(log_path: Path) -> dict[str, Any]:
    """Parse an upstream.log and return per-role action counts."""
    try:
        lines = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return {"error": "cannot read log"}

    # Sections: autoprompt (before first marker), PLANNER, EXECUTOR, EXECUTOR DONE
    autoprompt_actions = 0
    planner_actions = 0
    executor_actions = 0
    executor_instances = 0

    current_section = "autoprompt"  # before first marker
    first_marker_seen = False

    for line in lines:
        stripped = line.strip()

        # Check for section markers
        m = _SECTION_RE.match(stripped)
        if m:
            first_marker_seen = True
            marker = m.group(1)
            if marker == "PLANNER":
                current_section = "planner"
            elif marker == "EXECUTOR DONE":
                # Control returns to planner
                current_section = "planner"
            elif marker == "EXECUTOR":
                current_section = "executor"
                executor_instances += 1
            continue

        # Count [Assistant Action] lines
        if stripped == "[Assistant Action]":
            if current_section == "autoprompt":
                autoprompt_actions += 1
            elif current_section == "planner":
                planner_actions += 1
            elif current_section == "executor":
                executor_actions += 1

    total = autoprompt_actions + planner_actions + executor_actions

    # Check for timed_out / return code from header
    timed_out = False
    for line in lines[:5]:
        if "TIMED OUT: True" in line:
            timed_out = True
            break

    return {
        "autoprompt_actions": autoprompt_actions,
        "planner_actions": planner_actions,
        "executor_actions": executor_actions,
        "executor_instances": executor_instances,
        "total_actions": total,
        "timed_out": timed_out,
    }


def analyze_batch(batch_dir: Path) -> tuple[list[dict], dict]:
    """Analyze all challenges in a batch run."""
    challenges_dir = batch_dir / "challenges"
    if not challenges_dir.is_dir():
        print(f"Error: {challenges_dir} not found", file=sys.stderr)
        sys.exit(1)

    results = []
    for result_json in sorted(challenges_dir.rglob("result.json")):
        chal_dir = result_json.parent
        category = chal_dir.parent.name
        chal_id = chal_dir.name

        # Read result.json
        try:
            result_data = json.loads(result_json.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        solved = result_data.get("solved", False)
        elapsed = result_data.get("elapsed_seconds", 0)
        error = result_data.get("error")

        # Parse upstream.log
        upstream_log = chal_dir / "upstream.log"
        if upstream_log.exists():
            log_stats = parse_upstream_log(upstream_log)
        else:
            log_stats = {
                "autoprompt_actions": 0,
                "planner_actions": 0,
                "executor_actions": 0,
                "executor_instances": 0,
                "total_actions": 0,
                "timed_out": False,
            }

        # Determine if it's a launch error (no real execution)
        is_error = bool(error and log_stats["total_actions"] == 0)

        entry = {
            "challenge": f"{category}/{chal_id}",
            "category": category,
            "solved": solved,
            "elapsed_seconds": elapsed,
            "timed_out": log_stats["timed_out"],
            "is_error": is_error,
            "autoprompt_actions": log_stats["autoprompt_actions"],
            "planner_actions": log_stats["planner_actions"],
            "executor_actions": log_stats["executor_actions"],
            "executor_instances": log_stats["executor_instances"],
            "total_actions": log_stats["total_actions"],
        }
        results.append(entry)

    # Compute summary stats
    summary = _compute_summary(results)
    return results, summary


def _avg(values: list[float | int]) -> float:
    return round(sum(values) / len(values), 1) if values else 0.0


def _median(values: list[float | int]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return float(s[n // 2])
    return round((s[n // 2 - 1] + s[n // 2]) / 2, 1)


def _compute_summary(results: list[dict]) -> dict:
    """Compute aggregate statistics."""
    # Filter out error launches (no execution at all)
    executed = [r for r in results if not r["is_error"]]
    solved = [r for r in executed if r["solved"]]
    failed = [r for r in executed if not r["solved"]]

    def _group_stats(items: list[dict]) -> dict:
        if not items:
            return {
                "count": 0,
                "avg_autoprompt": 0, "avg_planner": 0, "avg_executor": 0,
                "avg_executor_instances": 0, "avg_total": 0,
                "median_total": 0,
                "avg_elapsed": 0,
                "timed_out_count": 0,
            }
        return {
            "count": len(items),
            "avg_autoprompt": _avg([r["autoprompt_actions"] for r in items]),
            "avg_planner": _avg([r["planner_actions"] for r in items]),
            "avg_executor": _avg([r["executor_actions"] for r in items]),
            "avg_executor_instances": _avg([r["executor_instances"] for r in items]),
            "avg_total": _avg([r["total_actions"] for r in items]),
            "median_total": _median([r["total_actions"] for r in items]),
            "avg_elapsed": _avg([r["elapsed_seconds"] for r in items]),
            "timed_out_count": sum(1 for r in items if r["timed_out"]),
        }

    # Per-category breakdown
    by_category = defaultdict(list)
    for r in executed:
        by_category[r["category"]].append(r)

    category_stats = {}
    for cat in sorted(by_category):
        items = by_category[cat]
        cat_solved = [r for r in items if r["solved"]]
        cat_failed = [r for r in items if not r["solved"]]
        category_stats[cat] = {
            "all": _group_stats(items),
            "solved": _group_stats(cat_solved),
            "failed": _group_stats(cat_failed),
        }

    return {
        "total_challenges": len(results),
        "executed": len(executed),
        "error_launches": len(results) - len(executed),
        "all": _group_stats(executed),
        "solved": _group_stats(solved),
        "failed": _group_stats(failed),
        "by_category": category_stats,
    }


def render_markdown(summary: dict, results: list[dict], batch_name: str) -> str:
    """Render summary as markdown."""
    lines = [
        f"# dcipher Step Analysis: {batch_name}",
        "",
    ]

    # Overall stats
    s = summary
    lines.extend([
        "## Overall",
        "",
        f"- Total challenges: {s['total_challenges']}",
        f"- Executed (non-error): {s['executed']}",
        f"- Error launches: {s['error_launches']}",
        "",
    ])

    # Summary table
    lines.extend([
        "## Average Rounds by Outcome",
        "",
        "| Group | Count | AutoPrompt | Planner | Executor | Exec Instances | Total (avg) | Total (median) | Elapsed (avg) | Timed Out |",
        "|-------|------:|-----------:|--------:|---------:|---------------:|------------:|---------------:|--------------:|----------:|",
    ])

    for label, key in [("All", "all"), ("Solved", "solved"), ("Failed", "failed")]:
        g = s[key]
        lines.append(
            f"| {label} | {g['count']} | {g['avg_autoprompt']} | {g['avg_planner']} "
            f"| {g['avg_executor']} | {g['avg_executor_instances']} "
            f"| {g['avg_total']} | {g['median_total']} "
            f"| {g['avg_elapsed']}s | {g['timed_out_count']} |"
        )

    lines.extend(["", ""])

    # Per-category table
    lines.extend([
        "## Per-Category Breakdown",
        "",
        "| Category | Solved | Failed | Avg Total (Solved) | Avg Total (Failed) | Median Total (Solved) | Avg Elapsed (Solved) |",
        "|----------|-------:|-------:|-------------------:|-------------------:|----------------------:|---------------------:|",
    ])

    for cat in sorted(s["by_category"]):
        cs = s["by_category"][cat]
        lines.append(
            f"| {cat} | {cs['solved']['count']} | {cs['failed']['count']} "
            f"| {cs['solved']['avg_total']} | {cs['failed']['avg_total']} "
            f"| {cs['solved']['median_total']} | {cs['solved']['avg_elapsed']}s |"
        )

    lines.extend(["", ""])

    # Per-challenge detail table (sorted by total_actions desc)
    sorted_results = sorted(
        [r for r in results if not r["is_error"]],
        key=lambda r: r["total_actions"],
        reverse=True,
    )

    lines.extend([
        "## Per-Challenge Details (sorted by total actions)",
        "",
        "| Challenge | Solved | AutoP | Planner | Executor | ExecN | Total | Elapsed | Timeout |",
        "|-----------|:------:|------:|--------:|---------:|------:|------:|--------:|:-------:|",
    ])

    for r in sorted_results:
        solved_mark = "Y" if r["solved"] else ""
        timeout_mark = "T" if r["timed_out"] else ""
        lines.append(
            f"| {r['challenge']} | {solved_mark} | {r['autoprompt_actions']} "
            f"| {r['planner_actions']} | {r['executor_actions']} "
            f"| {r['executor_instances']} | {r['total_actions']} "
            f"| {r['elapsed_seconds']}s | {timeout_mark} |"
        )

    # Error launches
    errors = [r for r in results if r["is_error"]]
    if errors:
        lines.extend(["", "", "## Error Launches (no execution)", ""])
        for r in errors:
            lines.append(f"- {r['challenge']}")

    lines.append("")
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <batch_log_dir>", file=sys.stderr)
        sys.exit(1)

    batch_dir = Path(sys.argv[1]).resolve()
    batch_name = batch_dir.name

    results, summary = analyze_batch(batch_dir)

    # Write JSON
    json_path = batch_dir / "step_details.json"
    with json_path.open("w") as f:
        json.dump({"challenges": results, "summary": summary}, f, indent=2, ensure_ascii=False)
    print(f"Wrote {json_path}")

    # Write markdown
    md_path = batch_dir / "step_summary.md"
    md_content = render_markdown(summary, results, batch_name)
    with md_path.open("w") as f:
        f.write(md_content)
    print(f"Wrote {md_path}")

    # Print quick summary to stdout
    s = summary
    print(f"\n{'='*60}")
    print(f"  {batch_name}")
    print(f"{'='*60}")
    print(f"  Executed: {s['executed']}  Solved: {s['solved']['count']}  Failed: {s['failed']['count']}  Errors: {s['error_launches']}")
    print(f"  Avg rounds — Solved: {s['solved']['avg_total']}  Failed: {s['failed']['avg_total']}")
    print(f"  Median rounds — Solved: {s['solved']['median_total']}  Failed: {s['failed']['median_total']}")
    print(f"  Timed out: {s['all']['timed_out_count']}")


if __name__ == "__main__":
    main()
