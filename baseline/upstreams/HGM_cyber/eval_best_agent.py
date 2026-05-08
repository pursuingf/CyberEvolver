#!/usr/bin/env python3
"""
Final evaluation of best HGM cyber agent variant(s) on full benchmark.

Standalone script — no coupling to the evolution loop.
Reads evolution output, finds best node(s), runs full benchmark pass@n.

All (node × run × task) triples share one ThreadPoolExecutor(max_workers).

Usage:
    python eval_best_agent.py \
        --evolve_output_dir output_hgm_cyber/Kimi-K2.5-sii__cvebench__20260422_184532 \
        --model Kimi-K2.5-sii \
        --pass_n 3 \
        --top_k 2
"""

import argparse
import datetime
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(_SCRIPT_DIR))
sys.path.insert(1, str(_PROJECT_ROOT))

from hgmlib.common_utils import load_json_file
from hgmlib.evo_utils import get_model_patch_paths, load_hgm_metadata


# ─────────────────────────────────────────────────────────────────────────────
# Model probe
# ─────────────────────────────────────────────────────────────────────────────

def probe_model(model, timeout_s=30, max_retries=3, retry_interval=10):
    """Check that the model endpoint is reachable and responding."""
    import yaml
    from llm import create_client

    print(f"  Probing model: {model}")
    sys.stdout.flush()

    for attempt in range(1, max_retries + 1):
        try:
            client, actual_model = create_client(model)
            response = client.chat.completions.create(
                model=actual_model,
                messages=[{"role": "user", "content": "Reply exactly: OK"}],
                max_tokens=4,
                temperature=0,
                timeout=timeout_s,
            )
            content = response.choices[0].message.content.strip()
            print(f"  Model probe OK: {actual_model} -> '{content}'")
            sys.stdout.flush()
            return True
        except Exception as e:
            print(f"  Model probe attempt {attempt}/{max_retries} failed: {e}")
            sys.stdout.flush()
            if attempt < max_retries:
                time.sleep(retry_interval)

    print(f"  ERROR: Model {model} not reachable after {max_retries} attempts")
    sys.stdout.flush()
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Find best nodes from evolution output
# ─────────────────────────────────────────────────────────────────────────────

def find_best_nodes(evolve_output_dir, top_k=1):
    """Find top-K nodes by actual solved/submitted ratio from each node's metadata.json.

    Reads ground-truth performance from per-node metadata.json rather than
    hgm_metadata.jsonl, which may contain corrupted mean_utility/num_evals
    due to the upstream list-aliasing bug in tree.py (see BUGFIX.md).
    """
    metadata_path = os.path.join(evolve_output_dir, "hgm_metadata.jsonl")
    if not os.path.exists(metadata_path):
        raise FileNotFoundError(f"No metadata found: {metadata_path}")

    metadata = load_hgm_metadata(metadata_path, last_only=True)
    nodes = metadata.get("nodes", [])

    # Collect all commit_ids (initial + evolved)
    commit_ids = [("initial", 0, None)]
    for node in nodes:
        commit_ids.append((node["commit_id"], node["id"], node.get("parent_id")))

    # Read actual performance from each node's metadata.json
    all_nodes = []
    for commit_id, node_id, parent_id in commit_ids:
        node_meta_path = os.path.join(evolve_output_dir, commit_id, "metadata.json")
        if not os.path.exists(node_meta_path):
            continue
        node_meta = load_json_file(node_meta_path)
        perf = node_meta.get("overall_performance", {})
        resolved = perf.get("total_resolved_instances", 0)
        submitted = perf.get("total_submitted_instances", 0)
        if submitted > 0:
            all_nodes.append({
                "commit_id": commit_id,
                "id": node_id,
                "parent_id": parent_id,
                "mean_utility": resolved / submitted,
                "num_evals": submitted,
            })

    if not all_nodes:
        print("  WARNING: No evaluated nodes found. Using initial node.")
        all_nodes = [{"commit_id": "initial", "id": 0, "parent_id": None,
                      "mean_utility": 0.0, "num_evals": 0}]

    # Sort by mean utility (desc), then num_evals (desc)
    all_nodes.sort(key=lambda n: (n["mean_utility"], n["num_evals"]), reverse=True)
    return all_nodes[:top_k]


# ─────────────────────────────────────────────────────────────────────────────
# Flat parallel evaluation
# ─────────────────────────────────────────────────────────────────────────────

def _build_work_items(nodes, all_tasks, pass_n, evolve_output_dir):
    """Build flat list of (node, run_idx, task_id, pred_dir, patch_paths)."""
    root_dir = os.path.abspath("./")
    items = []
    for node in nodes:
        commit_id = node["commit_id"]
        if commit_id == "initial":
            patch_paths = []
        else:
            patch_paths = get_model_patch_paths(root_dir, evolve_output_dir, commit_id)

        for run_idx in range(pass_n):
            pred_dir = os.path.join(
                evolve_output_dir, "final_eval", commit_id, f"run_{run_idx}", "predictions"
            )
            os.makedirs(pred_dir, exist_ok=True)
            for task_id in all_tasks:
                items.append({
                    "node": node,
                    "run_idx": run_idx,
                    "task_id": task_id,
                    "pred_dir": Path(pred_dir),
                    "patch_paths": patch_paths,
                })
    return items


def _run_single_task(item, model, init_agent_path, step_limit, evaluation_timeout):
    """Evaluate one (node, run, task) triple. Called by the pool."""
    from cyber_harness import process_entry

    node = item["node"]
    run_idx = item["run_idx"]
    task_id = item["task_id"]
    pred_dir = item["pred_dir"]
    patch_paths = item["patch_paths"]
    commit_id = node["commit_id"]

    result = process_entry(
        chal_id=task_id,
        pred_dname=pred_dir,
        model_name_or_path=f"{commit_id}_run{run_idx}",
        model_patch_paths=patch_paths,
        init_agent_path=init_agent_path,
    )

    status = "SOLVED" if result.get("success") else "FAIL"
    # Check actual solved status from result.json
    solved = False
    for p in pred_dir.rglob("result.json"):
        try:
            data = load_json_file(str(p))
            if data.get("instance_id") == task_id:
                solved = data.get("solved", False)
                break
        except Exception:
            pass

    ts = datetime.datetime.now().strftime("%H:%M:%S")
    tag = "✓" if solved else "✗"
    print(f"  [{ts}] {tag} node[{node['id']}] run{run_idx} {task_id}")
    sys.stdout.flush()

    return {
        "node_id": node["id"],
        "commit_id": commit_id,
        "run_idx": run_idx,
        "task_id": task_id,
        "solved": solved,
        "success": result.get("success", False),
    }


def run_all_evaluations(
    nodes, all_tasks, pass_n, evolve_output_dir,
    model, max_workers, step_limit, evaluation_timeout,
):
    """Run all evaluations in a single flat ThreadPoolExecutor."""
    import cyber_harness

    # Set harness globals (read by process_entry)
    cyber_harness.llm = model
    cyber_harness.timeout = evaluation_timeout
    cyber_harness.step_limit = step_limit

    root_dir = os.path.abspath("./")
    init_agent_path = os.path.join(root_dir, "initial_cyber", "default_agent", "src")

    work_items = _build_work_items(nodes, all_tasks, pass_n, evolve_output_dir)
    total = len(work_items)
    n_nodes = len(nodes)

    print(f"\n  Total work items: {total} ({n_nodes} nodes × {pass_n} runs × {len(all_tasks)} tasks)")
    print(f"  Parallel workers: {max_workers}")
    print(f"  Starting evaluation...\n")
    sys.stdout.flush()

    results = []
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {
            executor.submit(
                _run_single_task, item, model, init_agent_path,
                step_limit, evaluation_timeout,
            ): item
            for item in work_items
        }

        for future in as_completed(future_to_item):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                item = future_to_item[future]
                print(f"  [ERROR] node[{item['node']['id']}] run{item['run_idx']} "
                      f"{item['task_id']}: {e}")
                results.append({
                    "node_id": item["node"]["id"],
                    "commit_id": item["node"]["commit_id"],
                    "run_idx": item["run_idx"],
                    "task_id": item["task_id"],
                    "solved": False,
                    "success": False,
                })

            completed += 1
            if completed % 10 == 0 or completed == total:
                solved_so_far = sum(1 for r in results if r["solved"])
                print(f"  --- progress: {completed}/{total}  solved so far: {solved_so_far} ---")
                sys.stdout.flush()

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Collect results per (node, run) and compute pass@n
# ─────────────────────────────────────────────────────────────────────────────

def collect_results(raw_results, nodes, all_tasks, pass_n, evolve_output_dir):
    """Aggregate raw results into per-node summaries and pass@n."""
    all_node_results = {}

    for node in nodes:
        commit_id = node["commit_id"]
        node_runs = []

        for run_idx in range(pass_n):
            run_results = [
                r for r in raw_results
                if r["commit_id"] == commit_id and r["run_idx"] == run_idx
            ]

            solved_ids = [r["task_id"] for r in run_results if r["solved"]]
            failed_ids = [r["task_id"] for r in run_results if not r["solved"]]

            # Read token stats from result.json files
            pred_dir = Path(os.path.join(
                evolve_output_dir, "final_eval", commit_id,
                f"run_{run_idx}", "predictions"
            ))
            total_prompt = 0
            total_completion = 0
            for p in pred_dir.rglob("result.json"):
                try:
                    data = load_json_file(str(p))
                    total_prompt += data.get("prompt_tokens", 0)
                    total_completion += data.get("completion_tokens", 0)
                except Exception:
                    pass

            summary = {
                "node_id": node["id"],
                "commit_id": commit_id,
                "run_idx": run_idx,
                "total_challenges": len(all_tasks),
                "solved": len(solved_ids),
                "failed": len(failed_ids),
                "accuracy": len(solved_ids) / max(len(all_tasks), 1),
                "solved_ids": solved_ids,
                "failed_ids": failed_ids,
                "error_ids": [],
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
            }

            # Save per-run summary
            eval_dir = os.path.join(
                evolve_output_dir, "final_eval", commit_id, f"run_{run_idx}"
            )
            os.makedirs(eval_dir, exist_ok=True)
            with open(os.path.join(eval_dir, "eval_summary.json"), "w") as f:
                json.dump(summary, f, indent=2)

            node_runs.append(summary)

        # Compute pass@n
        pass_at_n = _compute_pass_at_n(node_runs)
        all_node_results[commit_id] = {
            "node": node,
            "runs": node_runs,
            "pass_at_n": pass_at_n,
        }

    return all_node_results


def _compute_pass_at_n(run_summaries):
    """pass@n: challenge solved if solved in ANY of the n runs."""
    if not run_summaries:
        return {}

    all_tasks = set()
    for s in run_summaries:
        all_tasks.update(s["solved_ids"])
        all_tasks.update(s["failed_ids"])

    solved_union = set()
    for s in run_summaries:
        solved_union.update(s["solved_ids"])

    n = len(run_summaries)
    total = len(all_tasks)
    total_prompt = sum(s["prompt_tokens"] for s in run_summaries)
    total_completion = sum(s["completion_tokens"] for s in run_summaries)

    return {
        "n": n,
        "total_challenges": total,
        "pass_at_n_solved": len(solved_union),
        "pass_at_n_accuracy": len(solved_union) / max(total, 1),
        "pass_at_n_solved_ids": sorted(solved_union),
        "per_run_accuracy": [s["accuracy"] for s in run_summaries],
        "per_run_solved": [s["solved"] for s in run_summaries],
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Print report
# ─────────────────────────────────────────────────────────────────────────────

def print_report(all_results, evolve_dir):
    print("\n" + "=" * 70)
    print("  FINAL RESULTS")
    print("=" * 70)

    for commit_id, data in all_results.items():
        node = data["node"]
        pan = data["pass_at_n"]
        n = pan["n"]
        total = pan["total_challenges"]
        solved = pan["pass_at_n_solved"]
        acc = pan["pass_at_n_accuracy"]
        tok_in = pan["total_prompt_tokens"]
        tok_out = pan["total_completion_tokens"]

        evo_u = node["mean_utility"]
        evo_u_str = f"{evo_u:.3f}" if evo_u != float("inf") else "inf"

        print(f"\n  Node [{node['id']}] {commit_id}")
        print(f"    Evolution utility: {evo_u_str} ({node['num_evals']} partial evals)")
        if n == 1:
            print(f"    Full benchmark:   {solved}/{total} solved ({acc:.1%})")
        else:
            print(f"    pass@{n}:           {solved}/{total} solved ({acc:.1%})")
            per_run = pan["per_run_solved"]
            for i, s in enumerate(per_run):
                run_acc = s / max(total, 1)
                print(f"      run {i}: {s}/{total} ({run_acc:.1%})")
        print(f"    Tokens (in/out):  {tok_in:,} / {tok_out:,}")
        print(f"    Solved: {', '.join(pan['pass_at_n_solved_ids']) or 'none'}")

    # Save aggregate results
    final_results_path = os.path.join(evolve_dir, "final_eval", "results.json")
    os.makedirs(os.path.dirname(final_results_path), exist_ok=True)
    serializable = {}
    for k, v in all_results.items():
        serializable[k] = {
            "node_id": v["node"]["id"],
            "commit_id": v["node"]["commit_id"],
            "evolution_utility": v["node"]["mean_utility"],
            "pass_at_n": v["pass_at_n"],
        }
    with open(final_results_path, "w") as f:
        json.dump(serializable, f, indent=2)

    print(f"\n  Results saved: {final_results_path}")
    print("=" * 70)
    sys.stdout.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Evaluate best HGM cyber agent on full benchmark")
    parser.add_argument("--evolve_output_dir", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--benchmark", default="cvebench")
    parser.add_argument("--benchmark_json", default=None)
    parser.add_argument("--categories", default=None, help="Comma-separated category filter (e.g. 'pwn,web')")
    parser.add_argument("--pass_n", type=int, default=1, help="Number of runs for pass@n")
    parser.add_argument("--top_k", type=int, default=1, help="Evaluate top-K nodes")
    parser.add_argument("--max_workers", type=int, default=8)
    parser.add_argument("--step_limit", type=int, default=30)
    parser.add_argument("--evaluation_timeout", type=int, default=900)
    parser.add_argument("--node", type=str, default=None,
                        help="Specific node commit_id to evaluate (skip auto-selection)")
    parser.add_argument("--server_url", type=str, default=None,
                        help="CTF server URL (default: use cyber_harness default)")
    parser.add_argument("--skip_probe", action="store_true", help="Skip model probe")
    args = parser.parse_args()

    evolve_dir = os.path.abspath(args.evolve_output_dir)
    if not os.path.isdir(evolve_dir):
        print(f"ERROR: {evolve_dir} does not exist")
        sys.exit(1)

    print("=" * 70)
    print("  HGM CYBER AGENT — FINAL EVALUATION")
    print("=" * 70)
    print(f"  evolve dir:  {evolve_dir}")
    print(f"  model:       {args.model}")
    print(f"  benchmark:   {args.benchmark}")
    print(f"  pass@n:      {args.pass_n}")
    print(f"  top_k:       {args.top_k}")
    print(f"  workers:     {args.max_workers}")
    print(f"  step_limit:  {args.step_limit}")
    print(f"  timeout:     {args.evaluation_timeout}s")
    print("=" * 70)
    sys.stdout.flush()

    # ── Probe model ──
    if not args.skip_probe:
        if not probe_model(args.model):
            sys.exit(1)

    # ── Select nodes ──
    if args.node:
        nodes = [{"commit_id": args.node, "id": -1, "parent_id": None,
                  "mean_utility": 0, "num_evals": 0}]
        print(f"\n  Using specified node: {args.node}")
    else:
        nodes = find_best_nodes(evolve_dir, top_k=args.top_k)
        print(f"\n  Selected {len(nodes)} node(s) for evaluation:")
        for n in nodes:
            u = n["mean_utility"]
            u_str = f"{u:.3f}" if u != float("inf") else "inf"
            print(f"    [{n['id']}] {n['commit_id']}  utility={u_str}  evals={n['num_evals']}")
    sys.stdout.flush()

    # ── Load tasks ──
    from cyber_harness import _load_benchmark, _BENCHMARK_JSON_MAP
    import cyber_harness
    cyber_harness.benchmark_name = args.benchmark
    if args.benchmark_json:
        bench_data = load_json_file(args.benchmark_json)
    else:
        bench_data = _load_benchmark(args.benchmark)
    all_tasks = list(bench_data.keys())

    # Filter by categories
    if args.categories:
        cats = set(c.strip() for c in args.categories.split(","))
        all_tasks = [t for t in all_tasks if bench_data[t].get("category", "") in cats]
        print(f"  Filtered to {len(all_tasks)} tasks in categories: {cats}")
    print(f"  Benchmark: {len(all_tasks)} challenges from {args.benchmark}")

    # ── Set server_url if provided ──
    if args.server_url:
        import cyber_harness
        cyber_harness.server_url = args.server_url

    # ── Run evaluations (flat parallel) ──
    raw_results = run_all_evaluations(
        nodes=nodes,
        all_tasks=all_tasks,
        pass_n=args.pass_n,
        evolve_output_dir=evolve_dir,
        model=args.model,
        max_workers=args.max_workers,
        step_limit=args.step_limit,
        evaluation_timeout=args.evaluation_timeout,
    )

    # ── Collect and report ──
    all_results = collect_results(
        raw_results, nodes, all_tasks, args.pass_n, evolve_dir
    )
    print_report(all_results, evolve_dir)


if __name__ == "__main__":
    main()
