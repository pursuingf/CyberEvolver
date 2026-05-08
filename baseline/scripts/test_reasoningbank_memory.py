#!/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python
"""Test ReasoningBank memory accumulation over 5 challenges.

Runs 5 challenges via the batch runner, then post-hoc feeds each trajectory
into a ReasoningBank instance to test distillation and memory accumulation.

Usage:
    python baseline/scripts/test_reasoningbank_memory.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "baseline" / "upstreams" / "ReasoningBank"))

from reasoningbank.memory.json import JSONMemoryBackend
from reasoningbank.distillation.distill import judge_trajectory, distill_trajectory
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_KEY = "Qwen3-235B-A22B-Instruct-2507"
PYTHON = "/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python"

TEST_CHALLENGES = [
    "2021q-for-lazy_leaks",         # forensics — telnet pcap
    "2023q-rev-baby_s_first",       # rev — easy reversing
    "2019f-msc-alive",              # misc — trivial
    "2021q-msc-weak_password",      # misc — password cracking
    "2018q-cry-babycrypto",         # crypto — diffie-hellman
]

OUTPUT_DIR = _ROOT / "baseline" / "logs" / "rb_memory_test"

# ---------------------------------------------------------------------------
# LLM wrapper for distillation (uses the same endpoint directly via requests)
# ---------------------------------------------------------------------------

import requests

LLM_BASE = "http://gw-bzokqkvr2cblz8ok6y.cn-wulanchabu-acdr-1.pai-eas.aliyuncs.com/api/predict/llm_hg_qwen3_235b_a22b/v1"
LLM_KEY = "NTc0ODlmNDY5ZWVlNTk5NjVmOTM4NDMwNjFlYzRjNmQwMjZjOWI4Yg=="


class DirectLLM:
    """Simple LLM wrapper that calls the API directly (LangChain-compatible invoke)."""

    def invoke(self, prompt: str) -> str:
        resp = requests.post(
            f"{LLM_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {LLM_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "Qwen3-235B-A22B-Instruct-2507",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000,
                "temperature": 0.3,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    memory_path = OUTPUT_DIR / "memory_bank.json"
    if memory_path.exists():
        memory_path.unlink()

    # Step 1: Run 5 challenges via batch runner
    print("=" * 60)
    print("STEP 1: Running 5 challenges")
    print("=" * 60)

    challenges_str = ",".join(TEST_CHALLENGES)
    cmd = [
        PYTHON, str(_ROOT / "baseline" / "batch" / "run_batch_baseline.py"),
        "--agent", "reasoningbank_agent",
        "--model", MODEL_KEY,
        "--benchmark", "nyu_ctf",
        "--step-limit", "30",
        "--max-workers", "1",
        "--challenges", challenges_str,
    ]

    print(f"Running: {' '.join(cmd[-8:])}")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ROOT), timeout=1200)
    print(result.stdout[-2000:] if result.stdout else "(no stdout)")
    if result.returncode != 0:
        print(f"STDERR: {result.stderr[-1000:]}")

    # Step 2: Find the log directory
    print("\n" + "=" * 60)
    print("STEP 2: Collecting trajectories and distilling memories")
    print("=" * 60)

    # Find the latest run dir
    agent_log_root = _ROOT / "baseline" / "logs" / "batch" / "reasoningbank_agent" / MODEL_KEY
    if not agent_log_root.exists():
        print(f"ERROR: {agent_log_root} not found")
        sys.exit(1)
    run_dirs = sorted(agent_log_root.iterdir(), key=lambda p: p.name)
    if not run_dirs:
        print("ERROR: no run directories found")
        sys.exit(1)
    run_dir = run_dirs[-1]
    print(f"Using run dir: {run_dir}")

    # Step 3: Initialize ReasoningBank components
    print("\nLoading sentence-transformers model...")
    embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    memory_backend = JSONMemoryBackend(filepath=str(memory_path))
    llm = DirectLLM()

    # Step 4: Process each challenge
    all_results = []
    challenges_dir = run_dir / "challenges"

    for chal_id in TEST_CHALLENGES:
        print(f"\n--- Processing: {chal_id} ---")

        # Find trajectory
        traj_files = list(challenges_dir.rglob(f"*{chal_id}*/trajectory.txt"))
        result_files = list(challenges_dir.rglob(f"*{chal_id}*/result.json"))

        if not traj_files:
            print(f"  No trajectory found, skipping")
            all_results.append({"challenge": chal_id, "status": "no_trajectory"})
            continue

        trajectory = traj_files[0].read_text(errors="replace")
        result_data = json.loads(result_files[0].read_text()) if result_files else {}
        solved = result_data.get("solved", False)

        print(f"  Solved: {solved}, Trajectory: {len(trajectory)} chars")

        # Build query
        query = f"CTF challenge: {chal_id} (category: {result_data.get('category', 'unknown')})"

        # Judge trajectory
        print(f"  Judging trajectory...")
        try:
            is_success = judge_trajectory(trajectory[:8000], query, llm)
            print(f"  Judgment: {'SUCCESS' if is_success else 'FAILURE'}")
        except Exception as exc:
            print(f"  Judge failed: {exc}")
            is_success = solved

        # Distill
        print(f"  Distilling memories...")
        try:
            distilled = distill_trajectory(trajectory[:8000], query, llm, is_success)
            print(f"  Distilled: {len(distilled)} memory items")
            for item in distilled:
                print(f"    - [{item.get('title', '?')}] {item.get('description', '')[:80]}")
        except Exception as exc:
            print(f"  Distill failed: {exc}")
            distilled = []

        # Store
        if distilled:
            query_embedding = embedding_model.encode(query)
            experience = {
                "embedding": query_embedding.tolist(),
                "metadata": {
                    "query": query,
                    "trajectory": trajectory[:3000],
                    "distilled_items": json.dumps(distilled),
                },
                "document": query,
            }
            memory_backend.add([experience])
            print(f"  Stored in memory bank")

        all_results.append({
            "challenge": chal_id,
            "solved": solved,
            "is_success_judged": is_success,
            "num_distilled": len(distilled),
            "distilled_items": distilled,
        })

    # Step 5: Summary
    print("\n" + "=" * 60)
    print("MEMORY BANK SUMMARY")
    print("=" * 60)

    # Read final memory state
    if memory_path.exists():
        bank_data = json.loads(memory_path.read_text())
        print(f"\nTotal stored experiences: {len(bank_data)}")
    else:
        bank_data = []
        print("\nMemory bank is empty")

    # Print all distilled memories
    for i, entry in enumerate(all_results):
        print(f"\n{'─'*50}")
        print(f"Challenge: {entry['challenge']}")
        print(f"Solved: {entry.get('solved', '?')}, Judged: {entry.get('is_success_judged', '?')}")
        items = entry.get("distilled_items", [])
        if items:
            for item in items:
                print(f"\n  Title: {item.get('title', '?')}")
                print(f"  Description: {item.get('description', '')}")
                content = item.get("content", "")
                # Print first 300 chars of content
                if len(content) > 300:
                    print(f"  Content: {content[:300]}...")
                else:
                    print(f"  Content: {content}")
        else:
            print(f"  (no memories distilled)")

    # Save full summary
    summary_path = OUTPUT_DIR / "memory_summary.json"
    with summary_path.open("w") as f:
        json.dump({
            "run_dir": str(run_dir),
            "challenges": all_results,
            "memory_bank_size": len(bank_data),
        }, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nSaved summary to: {summary_path}")
    print(f"Memory bank at: {memory_path}")


if __name__ == "__main__":
    main()
