#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from math import comb
from pathlib import Path
from typing import Any, Dict, Iterable, List


def pass_at_k(n: int, c: int, k: int) -> float:
    if n <= 0:
        return 0.0
    if n < k:
        k = n
    if k <= 0:
        return 0.0
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def _load_records(path: Path) -> List[Dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_single_run(results_path: str | Path, pass_k: int = 3) -> Dict[str, Any]:
    path = Path(results_path)
    rows = _load_records(path)
    by_challenge: Dict[str, List[bool]] = defaultdict(list)
    for row in rows:
        by_challenge[str(row["chal_id"])].append(bool(row.get("solved")))

    per_challenge = []
    for chal_id in sorted(by_challenge):
        solved_runs = sum(1 for solved in by_challenge[chal_id] if solved)
        total_runs = len(by_challenge[chal_id])
        per_challenge.append(
            {
                "chal_id": chal_id,
                "runs": total_runs,
                "solved_runs": solved_runs,
                "pass_value": pass_at_k(total_runs, solved_runs, pass_k),
                "solved_any": solved_runs > 0,
            }
        )

    challenge_count = len(per_challenge)
    solved_challenges = sum(1 for row in per_challenge if row["solved_any"])
    pass_rate = (
        sum(float(row["pass_value"]) for row in per_challenge) / challenge_count
        if challenge_count
        else 0.0
    )

    return {
        "mode": "single_run",
        "pass_k": pass_k,
        "challenge_count": challenge_count,
        "solved_challenges": solved_challenges,
        "pass_rate": pass_rate,
        "input_paths": [str(path)],
        "per_challenge": per_challenge,
    }


def summarize_multi_run(result_paths: Iterable[str | Path], pass_k: int = 3) -> Dict[str, Any]:
    paths = [Path(path) for path in result_paths]
    union: Dict[str, List[bool]] = defaultdict(list)
    all_challenges: set[str] = set()

    loaded_rows = []
    for path in paths:
        rows = _load_records(path)
        loaded_rows.append(rows)
        all_challenges.update(str(row["chal_id"]) for row in rows)

    for rows in loaded_rows:
        solved_map = {str(row["chal_id"]): bool(row.get("solved")) for row in rows}
        for chal_id in sorted(all_challenges):
            union[chal_id].append(bool(solved_map.get(chal_id, False)))

    per_challenge = []
    for chal_id in sorted(union):
        solved_runs = sum(1 for solved in union[chal_id] if solved)
        total_runs = len(union[chal_id])
        per_challenge.append(
            {
                "chal_id": chal_id,
                "runs": total_runs,
                "solved_runs": solved_runs,
                "pass_value": pass_at_k(total_runs, solved_runs, pass_k),
                "solved_any": solved_runs > 0,
            }
        )

    challenge_count = len(per_challenge)
    solved_challenges = sum(1 for row in per_challenge if row["solved_any"])
    pass_rate = (
        sum(float(row["pass_value"]) for row in per_challenge) / challenge_count
        if challenge_count
        else 0.0
    )

    return {
        "mode": "multi_run",
        "pass_k": pass_k,
        "challenge_count": challenge_count,
        "solved_challenges": solved_challenges,
        "pass_rate": pass_rate,
        "input_paths": [str(path) for path in paths],
        "per_challenge": per_challenge,
    }


def render_markdown_summary(summary: Dict[str, Any]) -> str:
    pass_k = int(summary["pass_k"])
    solved = int(summary["solved_challenges"])
    total = int(summary["challenge_count"])
    pass_rate = float(summary["pass_rate"])
    inputs = summary.get("input_paths") or []

    lines = [
        f"# pass@{pass_k} Summary",
        "",
        f"- Mode: `{summary['mode']}`",
        f"- Solved: `{solved}/{total}`",
        f"- pass@{pass_k}: `{pass_rate:.2%}`",
        "- Inputs:",
    ]
    for path in inputs:
        lines.append(f"  - `{path}`")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize pass@k from batch_results.json files.")
    parser.add_argument(
        "--mode",
        choices=["single-run", "multi-run"],
        required=True,
        help="single-run expects one batch_results.json with repeated samples; multi-run expects repeated independent run outputs.",
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="Path to batch_results.json. Repeat this flag for multi-run aggregation.",
    )
    parser.add_argument("--pass-k", type=int, default=3, help="pass@k to summarize (default: 3)")
    parser.add_argument("--output-json", default=None, help="Optional path to write JSON summary")
    parser.add_argument("--output-md", default=None, help="Optional path to write Markdown summary")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "single-run":
        if len(args.input) != 1:
            raise SystemExit("--mode single-run expects exactly one --input")
        summary = summarize_single_run(args.input[0], pass_k=args.pass_k)
    else:
        summary = summarize_multi_run(args.input, pass_k=args.pass_k)

    markdown = render_markdown_summary(summary)
    print(markdown, end="")

    if args.output_json:
        output_json = Path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.output_md:
        output_md = Path(args.output_md)
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
