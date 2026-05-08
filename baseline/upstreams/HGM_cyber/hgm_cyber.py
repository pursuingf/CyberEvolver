# Adapted from HGM's hgm.py.
# Changes: load tasks from cvebench.json instead of SWE-bench dataset,
#          use cyber_harness instead of swe_harness.

import argparse
import datetime
import json
import math
import os
import random
import sys
import string
import threading
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from statistics import stdev

import numpy as np

import hgm_cyber_utils
from config import load_config
from tree import Node
from hgmlib.common_utils import load_json_file
from hgmlib.docker_utils import copy_src_files, setup_logger
from hgmlib.evo_utils import load_hgm_metadata


def _load_node_meta(output_dir, commit_id):
    """Load per-node metadata.json — returns dict with tokens, entry, etc."""
    meta_path = os.path.join(output_dir, commit_id, "metadata.json")
    if os.path.exists(meta_path):
        try:
            return load_json_file(meta_path)
        except Exception:
            pass
    return {}


def _node_token_summary(output_dir, commit_id):
    """Get token summary for a node (eval + self-improve)."""
    meta = _load_node_meta(output_dir, commit_id)
    perf = meta.get("overall_performance", {})
    si = meta.get("self_improve_tokens", {})
    diag = meta.get("diagnose_tokens", {})
    eval_in = perf.get("eval_prompt_tokens", 0)
    eval_out = perf.get("eval_completion_tokens", 0)
    si_in = si.get("prompt_tokens", 0) + diag.get("prompt_tokens", 0)
    si_out = si.get("completion_tokens", 0) + diag.get("completion_tokens", 0)
    return {"eval_in": eval_in, "eval_out": eval_out, "si_in": si_in, "si_out": si_out}


def _get_patch_summary(output_dir, commit_id):
    """Get a brief description of what a node changed: files + line counts."""
    patch_path = os.path.join(output_dir, commit_id, "model_patch.diff")
    if not os.path.exists(patch_path):
        return ""
    try:
        with open(patch_path) as f:
            lines = f.readlines()
        file_stats = {}
        cur = None
        for line in lines:
            if line.startswith("diff --git"):
                cur = line.split(" b/")[-1].strip()
                if "__pycache__" in cur or cur == "self_evo.md":
                    cur = None
                    continue
                file_stats[cur] = [0, 0]
            elif cur and line.startswith("+") and not line.startswith("+++"):
                file_stats[cur][0] += 1
            elif cur and line.startswith("-") and not line.startswith("---"):
                file_stats[cur][1] += 1
        parts = [f"{f}(+{a}-{d})" for f, (a, d) in file_stats.items()]
        return "  ".join(parts[:4])
    except Exception:
        return ""


def _get_entry_name(output_dir, commit_id):
    """Get the improvement entry (task name) for a node."""
    meta = _load_node_meta(output_dir, commit_id)
    return meta.get("entry", "")


def print_progress(output_dir, max_task_evals):
    """Print a human-readable tree with per-node stats."""
    nodes_dict = {}
    for nid, node in hgm_cyber_utils.nodes.items():
        tokens = _node_token_summary(output_dir, node.commit_id)
        entry = _get_entry_name(output_dir, node.commit_id) if node.commit_id != "initial" else ""
        patch = _get_patch_summary(output_dir, node.commit_id) if node.commit_id != "initial" else ""
        children = [c.id for c in node.children]
        resolved = 0
        submitted = 0
        meta = _load_node_meta(output_dir, node.commit_id)
        perf = meta.get("overall_performance", {})
        resolved = perf.get("total_resolved_instances", 0)
        submitted = perf.get("total_submitted_instances", 0)
        nodes_dict[nid] = {
            "commit": node.commit_id,
            "utility": f"{node.mean_utility:.2f}" if node.num_evals > 0 else "-",
            "evals": node.num_evals,
            "solved": resolved,
            "submitted": submitted,
            "children": children,
            "parent": node.parent_id,
            "tokens": tokens,
            "entry": entry,
            "patch": patch,
        }

    # Aggregate totals
    total_eval_in = sum(n["tokens"]["eval_in"] for n in nodes_dict.values())
    total_eval_out = sum(n["tokens"]["eval_out"] for n in nodes_dict.values())
    total_si_in = sum(n["tokens"]["si_in"] for n in nodes_dict.values())
    total_si_out = sum(n["tokens"]["si_out"] for n in nodes_dict.values())
    total_solved = sum(n["solved"] for n in nodes_dict.values())
    total_submitted = sum(n["submitted"] for n in nodes_dict.values())

    total_in = total_eval_in + total_si_in
    total_out = total_eval_out + total_si_out

    ts = datetime.datetime.now().strftime("%H:%M:%S")
    sep = "=" * 90
    print(f"\n{sep}")
    print(f"  [{ts}] CYBER AGENT EVOLUTION")
    print(f"  progress: {hgm_cyber_utils.n_task_evals}/{max_task_evals} evals  |  {len(nodes_dict)} nodes  |  solved {total_solved}/{total_submitted}")
    print(f"  tokens (input/output):  eval {total_eval_in:,}/{total_eval_out:,}  |  evo {total_si_in:,}/{total_si_out:,}  |  total {total_in:,}/{total_out:,}")
    print(sep)

    def _fmt_tok(tok):
        """Format token dict as 'in/out' string."""
        eval_total = tok["eval_in"] + tok["eval_out"]
        si_total = tok["si_in"] + tok["si_out"]
        if eval_total == 0 and si_total == 0:
            return ""
        parts = []
        if eval_total > 0:
            parts.append(f"eval={tok['eval_in']:,}/{tok['eval_out']:,}")
        if si_total > 0:
            parts.append(f"evo={tok['si_in']:,}/{tok['si_out']:,}")
        return "  ".join(parts)

    def print_tree(nid, prefix="", is_last=True):
        n = nodes_dict[nid]
        connector = "└─" if is_last else "├─"
        line = f"{prefix}{connector} " if prefix else ""

        # Node info line
        status = f"solved={n['solved']}/{n['submitted']}" if n["submitted"] > 0 else ""
        util = f"u={n['utility']}" if n["evals"] > 0 else ""
        evals_str = f"evals={n['evals']}"

        parts = [f"[{nid}]", n["commit"][:20]]
        if util:
            parts.append(util)
        parts.append(evals_str)
        if status:
            parts.append(status)
        print(f"{line}{' | '.join(parts)}")

        # Second line: entry + files changed
        ext = "   " if is_last else "│  "
        if n["entry"] or n["patch"]:
            detail = ""
            if n["entry"]:
                detail += f"entry={n['entry']}"
            if n["patch"]:
                if detail:
                    detail += "  "
                detail += f"files=[{n['patch']}]"
            print(f"{prefix}{ext}  {detail}")

        # Third line: token usage (if any)
        tok_str = _fmt_tok(n["tokens"])
        if tok_str:
            print(f"{prefix}{ext}  {tok_str}")

        children = n["children"]
        for i, child in enumerate(children):
            ext = "   " if is_last else "│  "
            print_tree(child, prefix + ext, i == len(children) - 1)

    print_tree(0)
    print(sep + "\n")
    sys.stdout.flush()


def update_metadata(output_dir, n_task_evals, max_task_evals=0):
    with open(os.path.join(output_dir, "hgm_metadata.jsonl"), "a") as f:
        f.write(
            json.dumps(
                {
                    "n_task_evals": n_task_evals,
                    "nodes": [
                        node.save_as_dict()
                        for node in hgm_cyber_utils.nodes.values()
                        if node.commit_id != "initial"
                    ],
                },
                indent=2,
            )
            + "\n"
        )
    json.dump(
        hgm_cyber_utils.init_evaluated_tasks,
        open(os.path.join(output_dir, "init_evaluated_tasks.json"), "w"),
    )
    # Print progress after each metadata update
    try:
        print_progress(output_dir, max_task_evals)
    except Exception as e:
        print(f"[progress display error: {e}]")


def _empty_metadata():
    return {
        "overall_performance": {
            "accuracy_score": 0,
            "total_resolved_instances": 0,
            "total_submitted_instances": 0,
            "files": [],
            "total_resolved_ids": [],
            "total_unresolved_ids": [],
            "total_emptypatch_ids": [],
            "total_submitted_ids": [],
        }
    }


def initialize_run(
    output_dir,
    self_improve_llm,
    tasks,
    initial_agent_name,
    prevrun_dir=None,
    timeout=3600,
    max_workers=20,
):
    hgm_cyber_utils.init(False, output_dir, tasks, 0, self_improve_llm, timeout)

    initial_folder = "initial_cyber/"
    initial_src = os.path.join(initial_folder, initial_agent_name, "src")

    # Copy seed agent to output_dir/initial/
    initial_out = os.path.join(output_dir, "initial")
    os.makedirs(initial_out, exist_ok=True)
    if not os.path.exists(os.path.join(initial_out, "src")):
        os.system(f"cp -r {initial_folder}/{initial_agent_name}/* {initial_out}/")

    # Ensure initial metadata exists
    meta_path = os.path.join(initial_out, "metadata.json")
    if not os.path.exists(meta_path):
        import json as _json
        with open(meta_path, "w") as f:
            _json.dump({"run_id": "initial", **_empty_metadata()}, f, indent=2)

    Node(commit_id="initial")
    if prevrun_dir:
        hgm_cyber_utils.init_evaluated_tasks = load_json_file(
            os.path.join(prevrun_dir, "init_evaluated_tasks.json")
        )
        metadata_path = os.path.join(prevrun_dir, "hgm_metadata.jsonl")
        metadata = load_hgm_metadata(metadata_path, last_only=True)
        for node in metadata["nodes"]:
            commit_id = node["commit_id"]
            parent_id = node["parent_id"]
            Node(commit_id, parent_id=parent_id, id=node["id"])
        for node in hgm_cyber_utils.nodes.values():
            if node.parent_id is not None:
                parent = hgm_cyber_utils.nodes[node.parent_id]
                parent.add_child(node)

    n_task_evals = 0
    submitted_ids = defaultdict(set)
    for node in hgm_cyber_utils.nodes.values():
        node_meta_path = os.path.join(output_dir, node.commit_id, "metadata.json")
        if os.path.exists(node_meta_path):
            metadata = load_json_file(node_meta_path)
        else:
            metadata = _empty_metadata()
        perf = metadata.get("overall_performance", metadata)
        submitted_ids[node.id] = set(perf.get("total_submitted_ids", []))
        node.utility_measures = [
            1 for _ in range(perf.get("total_resolved_instances", 0))
        ] + [
            0 for _ in range(
                perf.get("total_submitted_instances", 0)
                - perf.get("total_resolved_instances", 0)
            )
        ]
        if node.commit_id != "initial":
            n_task_evals += perf.get("total_submitted_instances", 0)
    hgm_cyber_utils.n_task_evals = n_task_evals
    return initial_src, submitted_ids


def main():
    parser = argparse.ArgumentParser(description="HGM Cyber - Optimistic Tree Search for Cyber Agents")
    parser.add_argument("--config", type=str, default="config_cyber.yaml")
    parser.add_argument("--max_task_evals", type=int, default=None)
    parser.add_argument("--max_workers", type=int, default=None)
    parser.add_argument("--continue_from", type=str, default=None)
    parser.add_argument("--output_dir", type=str, default=None)
    parser.add_argument("--model", type=str, default=None, help="Set all three LLM roles at once")
    parser.add_argument("--self_improve_llm", type=str, default=None)
    parser.add_argument("--downstream_llm", type=str, default=None)
    parser.add_argument("--diagnose_llm", type=str, default=None)
    parser.add_argument("--alpha", type=float, default=None)
    parser.add_argument("--cool_down", dest="cool_down", action="store_true")
    parser.add_argument("--no_cool_down", dest="cool_down", action="store_false")
    parser.add_argument("--beta", type=float, default=None)
    parser.add_argument("--self_improve_timeout", type=int, default=None)
    parser.add_argument("--evaluation_timeout", type=int, default=None)
    parser.add_argument("--n_pseudo_descendant_evals", type=int, default=None)
    parser.add_argument("--eval_random_level", type=float, default=None)
    parser.add_argument("--step_limit", type=int, default=None, help="Max tool-calling steps per challenge evaluation")
    parser.add_argument("--initial_agent_name", type=str, default="default_agent")
    parser.add_argument("--benchmark", type=str, default="cvebench", help="Benchmark name (cvebench, nyu_ctf, autopenbench)")
    parser.add_argument("--benchmark_json", type=str, default=None)
    parser.add_argument("--categories", type=str, default=None, help="Comma-separated category filter (e.g. 'pwn,web')")
    parser.add_argument("--server_url", type=str, default=None, help="CTF server URL")
    parser.add_argument("--max_input_tokens", type=int, default=None,
                        help="Stop evolution when total input tokens exceed this limit")
    parser.add_argument("--max_output_tokens", type=int, default=None,
                        help="Stop evolution when total output tokens exceed this limit")
    parser.add_argument("--max_total_tokens", type=int, default=None,
                        help="Stop evolution when input+output tokens exceed this limit")

    parser.set_defaults(cool_down=None)
    args = parser.parse_args()

    # --model sets all three LLM roles; individual flags override
    if args.model is not None:
        if args.self_improve_llm is None:
            args.self_improve_llm = args.model
        if args.downstream_llm is None:
            args.downstream_llm = args.model
        if args.diagnose_llm is None:
            args.diagnose_llm = args.model

    overrides = {}
    if args.max_task_evals is not None:
        overrides["execution.max_task_evals"] = args.max_task_evals
    if args.max_workers is not None:
        overrides["execution.max_workers"] = args.max_workers
    if args.continue_from is not None:
        overrides["paths.continue_from"] = args.continue_from
    if args.output_dir is not None:
        overrides["paths.output_dir"] = args.output_dir
    if args.self_improve_llm is not None:
        overrides["llm.self_improve_llm"] = args.self_improve_llm
    if args.downstream_llm is not None:
        overrides["llm.downstream_llm"] = args.downstream_llm
    if args.diagnose_llm is not None:
        overrides["llm.diagnose_llm"] = args.diagnose_llm
    if args.alpha is not None:
        overrides["optimization.alpha"] = args.alpha
    if args.cool_down is not None:
        overrides["optimization.cool_down"] = args.cool_down
    if args.beta is not None:
        overrides["optimization.beta"] = args.beta
    if args.self_improve_timeout is not None:
        overrides["execution.self_improve_timeout"] = args.self_improve_timeout
    if args.evaluation_timeout is not None:
        overrides["execution.evaluation_timeout"] = args.evaluation_timeout
    if args.n_pseudo_descendant_evals is not None:
        overrides["optimization.n_pseudo_descendant_evals"] = args.n_pseudo_descendant_evals
    if args.eval_random_level is not None:
        overrides["optimization.eval_random_level"] = args.eval_random_level
    if args.step_limit is not None:
        overrides["execution.step_limit"] = args.step_limit
    if args.initial_agent_name is not None:
        overrides["paths.initial_agent_name"] = args.initial_agent_name

    config = load_config(args.config, **overrides)

    if not config.paths.initial_agent_name:
        parser.error("Initial agent name must be provided.")

    llm_cfg = config.llm
    opt_cfg = config.optimization
    exec_cfg = config.execution
    path_cfg = config.paths

    # Variables for this run
    # Directory name: {model}__{benchmark}__{timestamp}
    benchmark_name = args.benchmark
    model_short = llm_cfg.downstream_llm.replace("/", "_")

    if path_cfg.output_dir:
        output_dir = os.path.abspath(path_cfg.output_dir)
        run_id = os.path.basename(os.path.normpath(output_dir))
    elif not path_cfg.continue_from:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"{model_short}__{benchmark_name}__{ts}"
        output_dir = os.path.abspath(os.path.join("./output_hgm_cyber", run_id))
    else:
        run_id = os.path.basename(os.path.normpath(path_cfg.continue_from))
        output_dir = os.path.abspath(os.path.join("./output_hgm_cyber", run_id))

    os.makedirs(output_dir, exist_ok=True)
    print(f"Working directory: {os.getcwd()}")
    print(f"Using config file: {args.config}")
    print(f"Output directory: {output_dir}")

    # Save run config for traceability
    run_config = {
        "run_id": run_id,
        "model": llm_cfg.downstream_llm,
        "benchmark": benchmark_name,
        "max_task_evals": exec_cfg.max_task_evals,
        "max_workers": exec_cfg.max_workers,
        "step_limit": exec_cfg.step_limit,
        "evaluation_timeout": exec_cfg.evaluation_timeout,
        "self_improve_timeout": exec_cfg.self_improve_timeout,
        "alpha": opt_cfg.alpha,
        "config": config.to_dict(),
    }
    with open(os.path.join(output_dir, "run_config.json"), "w") as f:
        json.dump(run_config, f, indent=2)

    # Set downstream LLM for harness
    import cyber_harness
    import cyber_self_improve
    cyber_harness.llm = llm_cfg.downstream_llm
    cyber_harness.timeout = exec_cfg.evaluation_timeout
    cyber_harness.step_limit = exec_cfg.step_limit
    cyber_harness.benchmark_name = args.benchmark
    if args.server_url:
        cyber_harness.server_url = args.server_url
    cyber_self_improve.diagnose_llm = llm_cfg.diagnose_llm
    cyber_self_improve.self_improve_llm = llm_cfg.self_improve_llm

    logger = setup_logger(os.path.join(output_dir, "hgm_outer.log"))

    # Load tasks from benchmark
    if args.benchmark_json:
        bench_data = load_json_file(args.benchmark_json)
    else:
        bench_data = cyber_harness._load_benchmark(args.benchmark)
    tasks = list(bench_data.keys())

    # Filter by categories if specified
    if args.categories:
        cats = set(c.strip() for c in args.categories.split(","))
        tasks = [t for t in tasks if bench_data[t].get("category", "") in cats]
        logger.info(f"Filtered to {len(tasks)} tasks in categories: {cats}")
    random.seed(42)
    random.shuffle(tasks)

    src_path, submitted_ids = initialize_run(
        output_dir,
        llm_cfg.self_improve_llm,
        tasks,
        path_cfg.initial_agent_name,
        prevrun_dir=path_cfg.continue_from,
        timeout=exec_cfg.self_improve_timeout,
        max_workers=exec_cfg.max_workers,
    )
    total_num_tasks = len(hgm_cyber_utils.total_tasks)

    logger.info(f"Starting HGM Cyber run {run_id} with configuration: {config.to_dict()}")

    def TS_sample(evals):
        alphas = [1 + np.sum(de) for de in evals]
        betas = [1 + len(de) - np.sum(de) for de in evals]
        if opt_cfg.cool_down:
            alphas = np.array(alphas) * (
                10000
                if exec_cfg.max_task_evals == hgm_cyber_utils.n_task_evals
                else exec_cfg.max_task_evals**opt_cfg.beta
                / (exec_cfg.max_task_evals - hgm_cyber_utils.n_task_evals) ** opt_cfg.beta
            )
            betas = np.array(betas) * (
                10000
                if exec_cfg.max_task_evals == hgm_cyber_utils.n_task_evals
                else exec_cfg.max_task_evals**opt_cfg.beta
                / (exec_cfg.max_task_evals - hgm_cyber_utils.n_task_evals) ** opt_cfg.beta
            )
        thetas = np.random.beta(alphas, betas)
        return np.argmax(thetas)

    n_pending_expands = 0
    n_pending_measures = 0
    budget_exceeded = False
    lock = threading.Lock()

    def _check_token_budget():
        """Check if token budget is exceeded. Called under lock after metadata update."""
        nonlocal budget_exceeded
        if budget_exceeded:
            return True
        if args.max_input_tokens is None and args.max_output_tokens is None and args.max_total_tokens is None:
            return False
        # Aggregate tokens from all nodes
        total_in = 0
        total_out = 0
        for node in hgm_cyber_utils.nodes.values():
            tok = _node_token_summary(output_dir, node.commit_id)
            total_in += tok["eval_in"] + tok["si_in"]
            total_out += tok["eval_out"] + tok["si_out"]
        if args.max_input_tokens and total_in >= args.max_input_tokens:
            logger.info(f"[BUDGET] Input token limit reached: {total_in:,} >= {args.max_input_tokens:,}")
            budget_exceeded = True
        if args.max_output_tokens and total_out >= args.max_output_tokens:
            logger.info(f"[BUDGET] Output token limit reached: {total_out:,} >= {args.max_output_tokens:,}")
            budget_exceeded = True
        if args.max_total_tokens and (total_in + total_out) >= args.max_total_tokens:
            logger.info(f"[BUDGET] Total token limit reached: {total_in + total_out:,} >= {args.max_total_tokens:,}")
            budget_exceeded = True
        return budget_exceeded

    def expand():
        with lock:
            nodes = [
                node
                for node in hgm_cyber_utils.nodes.values()
                if node.num_evals > 0  # Has been evaluated at least once
            ]
            if not nodes:
                return  # No evaluated nodes yet, skip expand
            decendant_evals = [
                node.get_decendant_evals(num_pseudo=opt_cfg.n_pseudo_descendant_evals)
                for node in nodes
            ]
            selected_node = nodes[TS_sample(decendant_evals)]
        logger.info(f"[EXPAND] parent={selected_node.commit_id} (node {selected_node.id})")
        child_commit = hgm_cyber_utils.sample_child(
            selected_node.commit_id,
            image_name=path_cfg.initial_agent_name + ":latest",
        )
        with lock:
            if child_commit != "failed":
                child_node = Node(child_commit, parent_id=selected_node.id)
                selected_node.children.append(child_node)
                logger.info(f"[EXPAND OK] new node {child_node.id}: {child_commit} (parent={selected_node.id})")
                update_metadata(output_dir, hgm_cyber_utils.n_task_evals, exec_cfg.max_task_evals)
                _check_token_budget()
            else:
                logger.info(f"[EXPAND FAIL] parent={selected_node.id}")

    def sample():
        time.sleep(random.random())
        with lock:
            nonlocal n_pending_expands, n_pending_measures
            if hgm_cyber_utils.n_task_evals >= exec_cfg.max_task_evals:
                return
            if budget_exceeded:
                return
            # Only expand if there are evaluated nodes to branch from
            has_evaluated_nodes = any(
                n.num_evals > 0
                for n in hgm_cyber_utils.nodes.values()
            )
            if (
                has_evaluated_nodes
                and hgm_cyber_utils.n_task_evals**opt_cfg.alpha
                >= len(hgm_cyber_utils.nodes) - 1 + n_pending_expands
            ):
                n_pending_expands += 1
                is_expand = True
            else:
                is_expand = False
        if is_expand:
            expand()
            with lock:
                n_pending_expands -= 1
                return

        with lock:
            nodes = hgm_cyber_utils.nodes[0].get_sub_tree(fn=lambda node: node)
            nodes = [
                node for node in nodes if len(submitted_ids[node.id]) < total_num_tasks
            ]
            evals = [node.utility_measures for node in nodes]
            if len(evals) == 0:
                return
            selected_node = nodes[TS_sample(evals)]
            available_tasks = [
                task
                for task in hgm_cyber_utils.total_tasks
                if task not in submitted_ids[selected_node.id]
            ]
            if len(available_tasks) == 0:
                return
            if random.random() < opt_cfg.eval_random_level:
                selected_node_tasks = random.choice(available_tasks)
            else:
                selected_node_tasks = available_tasks[0]
            submitted_ids[selected_node.id].add(selected_node_tasks)
            n_pending_measures += 1

        logger.info(f"[EVAL] node {selected_node.id} ({selected_node.commit_id[:20]}) on {selected_node_tasks}")
        evals = hgm_cyber_utils.eval_agent(
            selected_node.commit_id,
            tasks=[selected_node_tasks],
            init_agent_path=src_path,
        )
        with lock:
            selected_node.utility_measures += evals
            n_pending_measures -= 1
            solved_str = "SOLVED" if evals and evals[0] == 1 else "FAILED"
            logger.info(f"[EVAL {solved_str}] node {selected_node.id}: {selected_node_tasks} -> {evals}")
            update_metadata(output_dir, hgm_cyber_utils.n_task_evals, exec_cfg.max_task_evals)
            _check_token_budget()

    try:
        with ThreadPoolExecutor(max_workers=exec_cfg.max_workers) as executor:
            futures = [
                executor.submit(expand)
                for _ in range(
                    len(hgm_cyber_utils.nodes) - 1,
                    min(5, int(exec_cfg.max_workers**opt_cfg.alpha)),
                )
            ]
            for future in as_completed(futures):
                future.result()

        with ThreadPoolExecutor(max_workers=exec_cfg.max_workers) as executor:
            futures = [
                executor.submit(sample)
                for _ in range(int(exec_cfg.max_task_evals * 100))
            ]
            for future in as_completed(futures):
                future.result()

    except Exception as e:
        logger.error(f"Error: {e}")
        logger.error(traceback.format_exc())
        print(repr(e))

    # Final summary
    print("\n" + "=" * 90)
    print("  RUN COMPLETE")
    print("=" * 90)
    print_progress(output_dir, exec_cfg.max_task_evals)

    # Find best node
    best_node = None
    best_utility = -1
    for node in hgm_cyber_utils.nodes.values():
        if node.num_evals > 0 and node.mean_utility > best_utility:
            best_utility = node.mean_utility
            best_node = node
    if best_node:
        print(f"  Best node: [{best_node.id}] {best_node.commit_id}  utility={best_utility:.3f}  evals={best_node.num_evals}")
    print(f"  Output: {output_dir}")
    print("=" * 90 + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
