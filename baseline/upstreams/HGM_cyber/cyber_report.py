# Cyber security evaluation report.
# Adapted from HGM's swe_bench/report.py — simplified for binary solved/not.

import json
import os
from pathlib import Path

from hgmlib.common_utils import load_json_file


def make_report(pred_dnames, run_ids=None, output_dir=".", num_eval_procs=1):
    """
    Generate evaluation report from cyber agent predictions.
    Same interface as swe_bench/report.py:make_report().

    For cyber challenges, evaluation is simple: check result.json for 'solved' field.
    No need for SWE-bench's complex test harness.
    """
    for i, pred_dname in enumerate(pred_dnames):
        pred_dname = Path(pred_dname)
        if not pred_dname.exists():
            continue

        # Read all prediction files
        for pred_file in pred_dname.glob("*.json"):
            if pred_file.name.startswith("_"):
                continue
            try:
                pred = load_json_file(str(pred_file))
                instance_id = pred.get("instance_id", pred_file.stem)
                solved = pred.get("solved", False)

                # Write report in SWE-bench compatible format
                report_dir = Path(output_dir) / "logs" / "run_evaluation" / pred_dname.name / pred_dname.name / instance_id
                report_dir.mkdir(parents=True, exist_ok=True)
                report = {
                    instance_id: {
                        "tests_status": {
                            "FAIL_TO_PASS": {
                                "success": [instance_id] if solved else [],
                                "failure": [] if solved else [instance_id],
                            },
                            "PASS_TO_PASS": {
                                "success": [],
                                "failure": [],
                            },
                        }
                    }
                }
                with open(report_dir / "report.json", "w") as f:
                    json.dump(report, f, indent=2)
            except Exception as e:
                print(f"Error processing {pred_file}: {e}")


def get_all_performance(commit_id, results_dir="."):
    """
    Get overall performance from prediction results.
    Returns (commit_id, overall_performance_dict).
    Compatible with HGM's utils/evo_utils.py:get_all_performance().
    """
    results_dir = Path(results_dir)
    pred_dir = results_dir / "predictions"

    resolved_ids = []
    unresolved_ids = []
    emptypatch_ids = []
    submitted_ids = []
    total_prompt_tokens = 0
    total_completion_tokens = 0

    # Walk predictions/{benchmark}/{chal_id}/result.json
    if pred_dir.exists():
        for result_file in pred_dir.rglob("result.json"):
            try:
                pred = load_json_file(str(result_file))
                instance_id = pred.get("instance_id", result_file.parent.name)
                solved = pred.get("solved", False)
                submitted_ids.append(instance_id)
                total_prompt_tokens += pred.get("prompt_tokens", 0)
                total_completion_tokens += pred.get("completion_tokens", 0)
                if solved:
                    resolved_ids.append(instance_id)
                else:
                    steps = pred.get("steps", 0)
                    if steps == 0:
                        emptypatch_ids.append(instance_id)
                    else:
                        unresolved_ids.append(instance_id)
            except Exception:
                continue

    total = len(submitted_ids) if submitted_ids else 1
    accuracy = len(resolved_ids) / total

    overall_performance = {
        "accuracy_score": accuracy,
        "total_resolved_instances": len(resolved_ids),
        "total_submitted_instances": len(submitted_ids),
        "files": [],
        "total_resolved_ids": resolved_ids,
        "total_unresolved_ids": unresolved_ids,
        "total_emptypatch_ids": emptypatch_ids,
        "total_submitted_ids": submitted_ids,
        "eval_prompt_tokens": total_prompt_tokens,
        "eval_completion_tokens": total_completion_tokens,
    }

    return commit_id, overall_performance
