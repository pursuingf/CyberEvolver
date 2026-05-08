# Adapted from HGM's hgm_utils.py.
# Changes: use cyber_harness instead of swe_harness, cyber_report instead of swe_report.

import datetime
import json
import os
import random
import re
import threading
import traceback
from pathlib import Path
from statistics import stdev

import docker
import numpy as np

import cyber_self_improve
from cyber_harness import harness as cyber_harness
from cyber_report import get_all_performance
from cyber_self_improve import diagnose_problem, save_metadata
from hgmlib.common_utils import load_json_file
from hgmlib.docker_utils import (
    build_hgm_container,
    cleanup_container,
    copy_from_container,
    copy_to_container,
    log_container_output,
    remove_existing_container,
    safe_log,
    setup_logger,
)
from hgmlib.evo_utils import get_model_patch_paths, load_hgm_metadata

dataset = None
alpha = 0.5
K = 0.5
bias_factor = 5
nodes = {}
total_tasks = []
output_dir = ""
polyglot = False
n_task_evals = 0
init_evaluated_tasks = []
llm = ""
timeout = 3600

pending_tasks_lock = threading.Lock()


def init(_polyglot, _output_dir, _tasks, _n_task_evals=0, _llm="", _timeout=3600):
    global output_dir, total_tasks, polyglot, n_task_evals, llm, timeout
    output_dir = _output_dir
    timeout = _timeout
    seen = set()
    total_tasks = []
    for item in _tasks:
        if item not in seen:
            seen.add(item)
            total_tasks.append(item)
    polyglot = _polyglot
    n_task_evals = _n_task_evals
    llm = _llm


def _write_changes_summary(node_dir, patch_content, entry, problem_statement):
    """Write a human-readable changes_summary.md for a node."""
    lines = patch_content.split('\n')

    # Parse diff: collect per-file stats
    files = []
    current_file = None
    added = 0
    removed = 0
    added_lines = []

    for line in lines:
        if line.startswith('diff --git'):
            if current_file:
                files.append({"file": current_file, "added": added, "removed": removed,
                              "added_lines": added_lines[:20]})
            parts = line.split(' b/')
            current_file = parts[-1] if len(parts) > 1 else line
            added = 0
            removed = 0
            added_lines = []
        elif line.startswith('+') and not line.startswith('+++'):
            added += 1
            stripped = line[1:].strip()
            if stripped and not stripped.startswith('#') and '__pycache__' not in stripped:
                added_lines.append(stripped)
        elif line.startswith('-') and not line.startswith('---'):
            removed += 1

    if current_file:
        files.append({"file": current_file, "added": added, "removed": removed,
                      "added_lines": added_lines[:20]})

    # Filter out noise (self_evo.md, __pycache__)
    code_files = [f for f in files
                  if f["file"] not in ("self_evo.md",)
                  and '__pycache__' not in f["file"]]

    summary_path = os.path.join(node_dir, "changes_summary.md")
    with open(summary_path, "w") as f:
        f.write(f"# Node Changes Summary\n\n")
        f.write(f"**Improvement target**: {entry}\n\n")

        # Brief problem statement (first 5 lines)
        if problem_statement:
            ps_lines = problem_statement.strip().split('\n')
            ps_brief = '\n'.join(ps_lines[:10])
            if len(ps_lines) > 10:
                ps_brief += f"\n... ({len(ps_lines) - 10} more lines)"
            f.write(f"**Problem statement**:\n```\n{ps_brief}\n```\n\n")

        f.write(f"## Modified files\n\n")
        for fi in code_files:
            f.write(f"- `{fi['file']}`: +{fi['added']} -{fi['removed']}\n")
        f.write(f"\n## Key additions\n\n")
        for fi in code_files:
            if not fi['added_lines']:
                continue
            f.write(f"### {fi['file']}\n```python\n")
            for al in fi['added_lines'][:15]:
                f.write(f"{al}\n")
            if len(fi['added_lines']) > 15:
                f.write(f"# ... {len(fi['added_lines']) - 15} more lines\n")
            f.write("```\n\n")

    safe_log(f"Changes summary written to {summary_path}")


def choose_entry(parent_commit, debug=False):
    """
    Choose entry for self-improvement given a parent commit.
    Same logic as HGM — just reads metadata and picks from failed challenges.
    """
    try:
        metadata_path = os.path.join(output_dir, parent_commit, "metadata.json")
        metadata = load_json_file(metadata_path)
        metadata = {
            "accuracy_score": metadata["overall_performance"]["accuracy_score"],
            "total_unresolved_ids": metadata["overall_performance"]["total_unresolved_ids"],
            "total_emptypatch_ids": metadata["overall_performance"]["total_emptypatch_ids"],
            "total_resolved_ids": metadata["overall_performance"]["total_resolved_ids"],
            "children_count": 0,
        }
    except Exception as e:
        raise RuntimeError(f"{parent_commit} not eligible for being a parent: {e}")
    if debug:
        safe_log(metadata)

    empty_ids = metadata["total_emptypatch_ids"]
    resolved_ids = metadata["total_resolved_ids"]
    unresolved_ids = metadata["total_unresolved_ids"]

    entry = None
    num_total_ids = len(empty_ids) + len(resolved_ids) + len(unresolved_ids)

    if len(empty_ids) >= 0.1 * num_total_ids and random.random() < 0.25:
        entry = "solve_empty_patches"
    elif random.random() < 0.25:
        entry = "solve_stochasticity"
    elif len(unresolved_ids) != 0:
        entry = random.choice(unresolved_ids)
    else:
        entry = random.choice(resolved_ids + empty_ids + unresolved_ids)

    if entry is None:
        safe_log(metadata)
        raise RuntimeError(
            f"Failed to choose an entry for self-improvement based on {parent_commit}."
        )
    return entry


def eval_agent(
    commit_id,
    tasks=None,
    num_tasks=5,
    max_workers=5,
    pending_tasks=None,
    random_level=0.5,
    skip=True,
    init_agent_path="./",
):
    """
    Evaluate a cyber agent variant on challenges.
    Adapted from HGM's eval_agent() — uses cyber_harness instead of swe_harness.
    """
    if commit_id == "failed":
        return [0] * num_tasks
    global n_task_evals, total_tasks
    metadata_path = os.path.join(output_dir, commit_id, "metadata.json")
    if not os.path.exists(metadata_path):
        metadata = {
            "run_id": commit_id,
            "overall_performance": {
                "accuracy_score": 0,
                "total_resolved_instances": 0,
                "total_submitted_instances": 0,
                "files": [],
                "total_unresolved_ids": [],
                "total_emptypatch_ids": [],
                "total_resolved_ids": [],
                "total_submitted_ids": [],
            },
        }
        save_metadata(metadata, os.path.join(output_dir, commit_id))
    metadata = load_json_file(metadata_path)

    if tasks is None:
        if commit_id == "initial":
            if len(set(init_evaluated_tasks)) >= len(set(total_tasks)):
                return [metadata["overall_performance"]["accuracy_score"]] * num_tasks
            else:
                if skip:
                    un_evaluated_tasks = [
                        task for task in total_tasks if task not in init_evaluated_tasks
                    ]
                else:
                    un_evaluated_tasks = total_tasks
                order = "random" if random.random() < random_level else "fixed"
                if order == "random":
                    tasks = random.sample(
                        un_evaluated_tasks, min(num_tasks, len(un_evaluated_tasks))
                    )
                else:
                    tasks = un_evaluated_tasks[:num_tasks]
                init_evaluated_tasks.extend(tasks)
                return _get_acc_on_tasks(tasks, os.path.join(output_dir, commit_id))
        if pending_tasks is None:
            pending_tasks = []

        with pending_tasks_lock:
            if skip:
                submitted_and_pending = (
                    metadata["overall_performance"]["total_submitted_ids"] + pending_tasks
                )
                un_evaluated_tasks = [
                    task for task in total_tasks if task not in submitted_and_pending
                ]
            else:
                un_evaluated_tasks = total_tasks

            order = "random" if random.random() < random_level else "fixed"
            if len(un_evaluated_tasks) > 0:
                if order == "random":
                    tasks = random.sample(
                        un_evaluated_tasks, min(num_tasks, len(un_evaluated_tasks))
                    )
                else:
                    tasks = un_evaluated_tasks[:num_tasks]
                num_tasks = len(tasks)
            else:
                return [metadata["overall_performance"]["accuracy_score"]] * num_tasks
            pending_tasks.extend(tasks)

    n_task_evals += len(tasks)
    root_dir = os.path.abspath("./")

    # Run cyber harness
    dnames = cyber_harness(
        test_task_list=tasks,
        max_workers=min(max_workers, len(tasks)),
        model_name_or_path=commit_id,
        model_patch_paths=get_model_patch_paths(root_dir, output_dir, commit_id),
        pred_dname=os.path.join(root_dir, output_dir, commit_id, "predictions"),
        init_agent_path=init_agent_path,
    )

    # Update metadata
    metadata = load_json_file(
        os.path.join(root_dir, output_dir, commit_id, "metadata.json")
    )
    _, overall_performance = get_all_performance(
        commit_id, results_dir=os.path.join(output_dir, commit_id)
    )
    metadata["overall_performance"] = overall_performance
    save_metadata(metadata, os.path.join(root_dir, output_dir, commit_id))

    return _get_acc_on_tasks(tasks, os.path.join(root_dir, output_dir, commit_id))


def _get_acc_on_tasks(tasks, results_dir):
    """Get binary accuracy for specific tasks."""
    results_dir = Path(results_dir)
    pred_dir = results_dir / "predictions"
    results = []
    for task in tasks:
        solved = False
        if pred_dir.exists():
            # Search predictions/{benchmark}/{chal_id}/result.json
            for result_file in pred_dir.rglob("result.json"):
                try:
                    data = load_json_file(str(result_file))
                    if data.get("instance_id") == task:
                        solved = data.get("solved", False)
                        break
                except Exception:
                    pass
        results.append(1 if solved else 0)
    return results


def sample_child(parent_commit, image_name, force_rebuild=False, max_try=1):
    """
    Create a new agent variant via self-improvement.
    Adapted from HGM's sample_child() — same Docker container pattern.
    """
    metadata = {}
    root_dir = os.path.abspath("./")
    run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_output_dir = os.path.join(root_dir, f"{output_dir}/{run_id}/")
    os.makedirs(run_output_dir, exist_ok=True)

    try:
        if parent_commit == "failed":
            return "failed"

        setup_logger(os.path.join(run_output_dir, "self_improve.log"))
        metadata["run_id"] = run_id
        metadata["parent_commit"] = parent_commit

        container_name = f"hgm-cyber-si-{run_id}"
        client = docker.from_env()
        remove_existing_container(client, container_name)

        # Use ctfenv image directly (no Dockerfile build needed).
        # Copy seed agent code + HGM meta-agent code into /hgm/.
        container = client.containers.create(
            "ctfenv",
            name=container_name,
            command="sleep infinity",
            detach=True,
            network_mode="host",
        )
        container.start()

        # Copy seed agent code (the code that gets modified by self-improvement)
        initial_src = os.path.join(root_dir, "initial_cyber", "default_agent", "src")
        container.exec_run("mkdir -p /hgm", workdir="/")
        for item in os.listdir(initial_src):
            if item == '__pycache__':
                continue
            src = os.path.join(initial_src, item)
            copy_to_container(container, src, f"/hgm/{item}")

        # Copy HGM meta-agent code (coding_agent.py + its tools for self-improvement)
        for f in ["coding_agent.py", "llm.py", "llm_withtools.py"]:
            copy_to_container(container, os.path.join(root_dir, f), f"/hgm/{f}")
        copy_to_container(container, os.path.join(root_dir, "tools"), "/hgm/tools")
        # coding_agent.py imports from utils/ — copy hgmlib/ contents as utils/ inside container
        hgmlib_path = os.path.join(root_dir, "hgmlib")
        container.exec_run("mkdir -p /hgm/utils")
        for item in os.listdir(hgmlib_path):
            if item == '__pycache__':
                continue
            copy_to_container(container, os.path.join(hgmlib_path, item), f"/hgm/utils/{item}")

        # Apply parent patches
        patch_files = get_model_patch_paths(root_dir, output_dir, parent_commit)
        for patch_file in patch_files:
            copy_to_container(container, patch_file, "/hgm/parent_patch.txt")
            exec_result = container.exec_run(
                "/bin/sh -c 'patch -p1 < /hgm/parent_patch.txt'", workdir="/hgm"
            )
            log_container_output(exec_result)
            exec_result = container.exec_run("rm /hgm/parent_patch.txt", workdir="/hgm")

        # Exclude chat logs and non-code artifacts from git diff to keep patches small.
        # self_evo.md is the coding agent's full conversation log (~150KB) and must NOT
        # appear in model_patch.diff — otherwise all ancestor patches become 150-400KB each,
        # blowing up the diagnose prompt's system message.
        container.exec_run(
            "/bin/sh -c 'printf \"__pycache__/\\nself_evo.md\\nchat_history.md\\n*.log\\n\" > /hgm/.gitignore'",
            workdir="/",
        )
        container.exec_run("git init", workdir="/hgm/")
        container.exec_run("git add --all", workdir="/hgm/")
        container.exec_run(
            "git -c user.name='user' -c user.email='you@example.com' commit -m 'baseline' --allow-empty",
            workdir="/hgm/",
        )
        exec_result = container.exec_run("git log --oneline -1", workdir="/hgm/")
        log_container_output(exec_result, raise_error=False)
        commit_hash = exec_result.output.decode("utf-8").strip().split()[0]

        exec_result = container.exec_run(
            "pip install -r /hgm/requirements.txt", workdir="/"
        )
        log_container_output(exec_result)

        # Choose entry and diagnose
        safe_log("Getting tasks to improve")
        try:
            entry = choose_entry(parent_commit)
        except Exception as e:
            safe_log(f"Error choosing entry: {e}")
        try:
            safe_log(f"Task to improve: {entry}")
        except Exception as e:
            choose_entry(parent_commit, debug=True)
            raise e

        problem_statement = diagnose_problem(
            entry,
            parent_commit,
            root_dir,
            output_dir,  # Use the global output_dir, not out_dir_base
            patch_files=patch_files,
        )
        # Capture diagnose token usage (host-side)
        from llm import get_token_usage as _get_host_tokens
        metadata["diagnose_tokens"] = _get_host_tokens()
        safe_log(f"problem_statement: {problem_statement}")

        metadata["entry"] = entry
        metadata["problem_statement"] = problem_statement
        if not problem_statement:
            safe_log("Failed to diagnose the problem statement. Exiting.")
            cleanup_container(container)
            save_metadata(metadata, run_output_dir)
            if max_try > 1:
                return sample_child(parent_commit, image_name, force_rebuild, max_try - 1)
            else:
                return "failed"

        # Run self-improvement
        safe_log("Running self-improvement")
        chat_history_file_container = "/hgm/self_evo.md"
        # Import model env injection from cyber_harness
        from cyber_harness import _model_env_vars
        env_vars = _model_env_vars(llm)
        cmd = [
            "timeout", str(timeout),
            "python", "/hgm/coding_agent.py",
            "--problem_statement", problem_statement,
            "--git_dir", "/hgm/",
            "--chat_history_file", chat_history_file_container,
            "--base_commit", commit_hash,
            "--outdir", "/hgm/",
            "--self_improve",
            "--model", llm,
            "--timeout", str(timeout),
        ]
        exec_result = container.exec_run(cmd, environment=env_vars, workdir="/")
        log_container_output(exec_result, raise_error=False)

        chat_history_file = os.path.join(output_dir, run_id, "self_evo.md")
        copy_from_container(container, chat_history_file_container, chat_history_file)
        model_patch_file = os.path.join(output_dir, run_id, "model_patch.diff")
        copy_from_container(container, "/hgm/model_patch.diff", model_patch_file)

        # Collect self-improve token usage from coding_agent
        try:
            exec_result = container.exec_run("cat /hgm/token_usage.json")
            si_tokens = json.loads(exec_result.output.decode())
        except Exception:
            si_tokens = {"prompt_tokens": 0, "completion_tokens": 0}
        metadata["self_improve_tokens"] = si_tokens
        metadata["entry"] = entry

        metadata["overall_performance"] = {
            "accuracy_score": 0.0,
            "total_resolved_instances": 0,
            "total_submitted_instances": 0,
            "files": [],
            "total_submitted_ids": [],
            "total_unresolved_ids": [],
            "total_emptypatch_ids": [],
            "total_resolved_ids": [],
        }
        if not os.path.exists(model_patch_file):
            raise Exception("Model patch file does not exist")
        with open(model_patch_file, "r") as f:
            patch_content = f.read()
            if not patch_content.strip():
                raise Exception("Model patch file is empty")

        # Generate human-readable changes summary
        _write_changes_summary(run_output_dir, patch_content, entry, problem_statement)

    except Exception as e:
        if max_try > 1:
            safe_log(f"Error while sampling a child: {str(e)}. Retrying...")
            safe_log(traceback.format_exc())
            return sample_child(parent_commit, image_name, force_rebuild, max_try - 1)
        else:
            safe_log(f"Error while sampling a child: {str(e)}")
            safe_log(traceback.format_exc())
            return "failed"
    finally:
        try:
            cleanup_container(container)
        except Exception as e:
            safe_log(f"Error during container cleanup: {e}")
        save_metadata(metadata, run_output_dir)
    return run_id
