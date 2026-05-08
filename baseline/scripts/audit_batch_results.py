#!/usr/bin/env python3
"""Audit batch run results — detect false positives by cross-checking
result.json against upstream.log evidence.

Usage:
    python baseline/scripts/audit_batch_results.py <batch_log_dir>
    python baseline/scripts/audit_batch_results.py <batch_log_dir> --fix   # rewrite result.json files

For dcipher runs, checks for 'SOLVED!' (not preceded by 'Not ') in upstream.log.
For nyuctf_single runs, checks for 'Correct flag!' in upstream.log.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Evidence patterns per agent
# ---------------------------------------------------------------------------

# Positive evidence: signals that confirm a genuine solve.
_POSITIVE_PATTERNS: dict[str, list[re.Pattern]] = {
    "dcipher": [
        re.compile(r"submit_flag:\s*\{'success':\s*True\}", re.IGNORECASE),
        re.compile(r'submit_flag:\s*\{"success":\s*true\}', re.IGNORECASE),
        # SOLVED! that is NOT preceded by "Not "
        re.compile(r"(?<!Not )SOLVED!", re.IGNORECASE),
    ],
    "nyuctf_single": [
        re.compile(r"check_flag:\s*\{'correct':\s*True\}", re.IGNORECASE),
        re.compile(r'check_flag:\s*\{"correct":\s*true\}', re.IGNORECASE),
        re.compile(r"Correct flag!", re.IGNORECASE),
    ],
}

# Negative evidence: signals that confirm the challenge was NOT solved
# (if positive evidence is absent and these are present → definite false positive).
_NEGATIVE_PATTERNS: dict[str, list[re.Pattern]] = {
    "dcipher": [
        re.compile(r"Challenge Not Solved!", re.IGNORECASE),
        re.compile(r"AGENT GAVE UP!", re.IGNORECASE),
    ],
    "nyuctf_single": [
        re.compile(r"Challenge is unsolved", re.IGNORECASE),
        re.compile(r"Incorrect flag\.", re.IGNORECASE),
    ],
}


def _grep_file(filepath: Path, pattern: str, ignore_case: bool = True) -> bool:
    """Fast check if a file matches a grep pattern (uses subprocess)."""
    import subprocess
    cmd = ["grep", "-q"]
    if ignore_case:
        cmd.append("-i")
    cmd.extend(["-P", pattern, str(filepath)])
    return subprocess.run(cmd, capture_output=True).returncode == 0


def _has_positive_evidence_file(filepath: Path, agent: str) -> bool:
    """Check upstream.log for positive solve evidence using fast grep."""
    if agent == "dcipher":
        return (
            _grep_file(filepath, r"submit_flag:\s*\{'success':\s*True\}")
            or _grep_file(filepath, r'submit_flag:\s*\{"success":\s*true\}')
            or _grep_file(filepath, r"^SOLVED!")
        )
    elif agent == "nyuctf_single":
        return (
            _grep_file(filepath, r"check_flag:\s*\{'correct':\s*True\}")
            or _grep_file(filepath, r'check_flag:\s*\{"correct":\s*true\}')
            or _grep_file(filepath, r"Correct flag!")
        )
    return False


def _has_negative_evidence_file(filepath: Path, agent: str) -> bool:
    """Check upstream.log for negative (unsolved) evidence using fast grep."""
    if agent == "dcipher":
        return (
            _grep_file(filepath, r"Challenge Not Solved!")
            or _grep_file(filepath, r"AGENT GAVE UP!")
        )
    elif agent == "nyuctf_single":
        return (
            _grep_file(filepath, r"Challenge is unsolved")
            or _grep_file(filepath, r"Incorrect flag\.")
        )
    return False


def audit_challenge(
    result_json_path: Path,
    agent: str,
) -> dict[str, Any]:
    """Audit a single challenge result.

    Returns dict with keys: challenge, category, reported_solved,
    verified_solved, verdict, reason.
    """
    result = json.loads(result_json_path.read_text())
    chal_dir = result_json_path.parent
    chal_id = result.get("challenge_id", chal_dir.name)
    category = result.get("category", chal_dir.parent.name)
    reported_solved = result.get("solved", False)

    upstream_log = chal_dir / "upstream.log"
    if not upstream_log.exists():
        return {
            "challenge": f"{category}/{chal_id}",
            "category": category,
            "reported_solved": reported_solved,
            "verified_solved": False if reported_solved else None,
            "verdict": "NO_LOG" if reported_solved else "skip",
            "reason": "upstream.log missing",
            "steps": result.get("steps_completed", 0),
            "elapsed": result.get("elapsed_seconds", 0),
            "tokens_total": result.get("tokens_total", 0),
        }

    has_positive = _has_positive_evidence_file(upstream_log, agent)
    has_negative = _has_negative_evidence_file(upstream_log, agent)

    if reported_solved:
        if has_positive:
            verdict = "TRUE_POSITIVE"
            verified = True
            reason = "positive evidence in upstream.log"
        elif has_negative:
            verdict = "FALSE_POSITIVE"
            verified = False
            reason = "negative evidence in upstream.log (e.g. 'Not Solved')"
        else:
            verdict = "UNVERIFIED"
            verified = None
            reason = "no clear evidence in upstream.log"
    else:
        if has_positive:
            verdict = "FALSE_NEGATIVE"
            verified = True
            reason = "positive evidence in upstream.log but result says unsolved"
        else:
            verdict = "TRUE_NEGATIVE"
            verified = False
            reason = ""

    return {
        "challenge": f"{category}/{chal_id}",
        "category": category,
        "reported_solved": reported_solved,
        "verified_solved": verified,
        "verdict": verdict,
        "reason": reason,
        "steps": result.get("steps_completed", 0),
        "elapsed": result.get("elapsed_seconds", 0),
        "tokens_total": result.get("tokens_total", 0),
    }


def fix_result_json(result_json_path: Path, verified_solved: bool) -> None:
    """Rewrite result.json with corrected solved status."""
    data = json.loads(result_json_path.read_text())
    if data.get("solved") == verified_solved:
        return
    data["solved_original"] = data["solved"]
    data["solved"] = verified_solved
    data["audit_corrected"] = True
    with result_json_path.open("w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir", type=Path, help="Path to batch log directory")
    parser.add_argument("--fix", action="store_true",
                        help="Rewrite result.json files to correct false positives/negatives")
    parser.add_argument("--json", action="store_true",
                        help="Output machine-readable JSON instead of table")
    args = parser.parse_args()

    batch_dir = args.batch_dir.resolve()
    challenges_dir = batch_dir / "challenges"
    if not challenges_dir.is_dir():
        print(f"Error: {challenges_dir} not found", file=sys.stderr)
        sys.exit(1)

    # Detect agent from batch_meta.json or first result.json
    agent = "dcipher"  # default
    meta_path = batch_dir / "batch_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        agent = meta.get("agent_name", agent)
    else:
        # Try first result.json
        for rj in challenges_dir.rglob("result.json"):
            data = json.loads(rj.read_text())
            agent = data.get("agent", agent)
            break

    if agent not in _POSITIVE_PATTERNS:
        print(f"Warning: no audit patterns for agent '{agent}', using dcipher patterns",
              file=sys.stderr)
        agent = "dcipher"

    # Audit all challenges
    results = []
    for result_json in sorted(challenges_dir.rglob("result.json")):
        audit = audit_challenge(result_json, agent)
        audit["_path"] = str(result_json)
        results.append(audit)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    # Summary statistics
    total = len(results)
    by_verdict = defaultdict(list)
    by_category = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "other": 0, "total": 0})
    for r in results:
        by_verdict[r["verdict"]].append(r)
        cat = r["category"]
        by_category[cat]["total"] += 1
        if r["verdict"] == "TRUE_POSITIVE":
            by_category[cat]["tp"] += 1
        elif r["verdict"] == "FALSE_POSITIVE":
            by_category[cat]["fp"] += 1
        elif r["verdict"] == "FALSE_NEGATIVE":
            by_category[cat]["fn"] += 1
        elif r["verdict"] == "TRUE_NEGATIVE":
            by_category[cat]["tn"] += 1
        else:
            by_category[cat]["other"] += 1

    tp = len(by_verdict["TRUE_POSITIVE"])
    fp = len(by_verdict["FALSE_POSITIVE"])
    fn = len(by_verdict["FALSE_NEGATIVE"])
    tn = len(by_verdict["TRUE_NEGATIVE"])
    unverified = len(by_verdict["UNVERIFIED"])
    no_log = len(by_verdict["NO_LOG"])
    reported_solved = tp + fp + unverified + no_log
    real_solved = tp + fn

    print(f"{'='*72}")
    print(f"  Batch Audit Report: {batch_dir.name}")
    print(f"  Agent: {agent}")
    print(f"{'='*72}")
    print()
    print(f"  Total challenges:      {total}")
    print(f"  Reported solved:       {reported_solved}  ({reported_solved/total*100:.1f}%)")
    print(f"  Verified solved:       {real_solved}  ({real_solved/total*100:.1f}%)")
    print()
    print(f"  True Positives:        {tp}")
    print(f"  False Positives:       {fp}")
    print(f"  True Negatives:        {tn}")
    print(f"  False Negatives:       {fn}")
    if unverified:
        print(f"  Unverified:            {unverified}")
    if no_log:
        print(f"  No upstream.log:       {no_log}")
    print()

    # Per-category breakdown
    print(f"  {'Category':<15} {'Total':>6} {'TP':>5} {'FP':>5} {'TN':>5} {'FN':>5} {'Real%':>7}")
    print(f"  {'-'*15} {'-'*6} {'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*7}")
    for cat in sorted(by_category):
        c = by_category[cat]
        real = c["tp"] + c["fn"]
        pct = f"{real/c['total']*100:.1f}%" if c["total"] else "N/A"
        print(f"  {cat:<15} {c['total']:>6} {c['tp']:>5} {c['fp']:>5} {c['tn']:>5} {c['fn']:>5} {pct:>7}")
    print()

    # List false positives
    if by_verdict["FALSE_POSITIVE"]:
        print(f"  False Positives ({fp}):")
        for r in by_verdict["FALSE_POSITIVE"]:
            print(f"    {r['challenge']}")
        print()

    # List false negatives
    if by_verdict["FALSE_NEGATIVE"]:
        print(f"  False Negatives ({fn}):")
        for r in by_verdict["FALSE_NEGATIVE"]:
            print(f"    {r['challenge']}")
        print()

    # Fix mode
    if args.fix:
        fixed = 0
        for r in results:
            if r["verdict"] == "FALSE_POSITIVE":
                fix_result_json(Path(r["_path"]), False)
                fixed += 1
            elif r["verdict"] == "FALSE_NEGATIVE":
                fix_result_json(Path(r["_path"]), True)
                fixed += 1
        print(f"  Fixed {fixed} result.json files.")
        print()

    # Regenerate corrected batch_results.json summary
    if args.fix and (fp + fn) > 0:
        corrected = []
        for r in results:
            data = json.loads(Path(r["_path"]).read_text())
            corrected.append({
                "chal_id": data.get("challenge_id", ""),
                "sample_idx": data.get("sample_idx", 0),
                "category": data.get("category", ""),
                "benchmark": data.get("benchmark", ""),
                "solved": data.get("solved", False),
                "flag": data.get("flag"),
                "error": data.get("error"),
                "duration_s": data.get("elapsed_seconds", 0),
            })
        corrected_path = batch_dir / "batch_results_audited.json"
        with corrected_path.open("w") as f:
            json.dump(corrected, f, indent=2, ensure_ascii=False)
        print(f"  Wrote corrected summary: {corrected_path}")


if __name__ == "__main__":
    main()
