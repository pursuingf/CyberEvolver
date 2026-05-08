#!/usr/bin/env python3
"""
Prepare Mind2Web data for ACE framework.

Downloads the Mind2Web dataset from HuggingFace and converts it into
step-level ACE samples with candidate element selection formulation.

Each web navigation task has multiple steps. Each step becomes one ACE sample:
- context: Compact list of ~200 candidate elements (tag + text + key attributes)
- question: Task description + previous action history
- target: Correct action representation (OP + element info + value)

Usage:
    python -m eval.mind2web.prepare_data
"""
import os
import re
import json
import random
from collections import Counter

# Number of negative candidates to sample per step (+ all positives)
MAX_NEG_CANDIDATES = 199
# Random seed for reproducibility
SEED = 42

OUTPUT_DIR = "./eval/mind2web/data"


def extract_element_text(html: str, backend_node_id: str, max_chars: int = 200) -> str:
    """
    Extract visible text content for an element identified by backend_node_id
    from the cleaned HTML.
    
    Searches for the element tag, then finds <text> nodes in the ~500 chars
    following it.
    """
    pattern = f'<\\w+\\s+backend_node_id="{backend_node_id}"[^>]*>'
    match = re.search(pattern, html)
    if not match:
        return ""
    
    start = match.start()
    snippet = html[start:start + 600]
    
    # Extract text node contents within this snippet
    texts = re.findall(r'<text backend_node_id="\d+">([^<]*)</text>', snippet)
    text_content = " ".join(t.strip() for t in texts if t.strip())
    
    if len(text_content) > max_chars:
        text_content = text_content[:max_chars] + "..."
    
    return text_content


def get_candidate_repr(candidate: dict, html: str, idx: int) -> str:
    """
    Create a compact text representation of a candidate element.
    
    Format: [idx] <tag> "text_content" (id=..., name=..., ...)
    """
    tag = candidate["tag"]
    backend_id = candidate["backend_node_id"]
    
    # Extract text from HTML
    text = extract_element_text(html, backend_id)
    
    # Get useful attributes
    try:
        attrs = json.loads(candidate["attributes"])
    except (json.JSONDecodeError, TypeError):
        attrs = {}
    
    useful_attrs = {}
    for key in ["id", "name", "aria-label", "placeholder", "alt", "title",
                 "type", "role", "href", "value"]:
        if key in attrs:
            val = str(attrs[key])[:80]
            useful_attrs[key] = val
    
    # Build representation
    parts = [f"[{idx}] <{tag}>"]
    if text:
        parts.append(f'"{text}"')
    if useful_attrs:
        attr_str = ", ".join(f'{k}="{v}"' for k, v in useful_attrs.items())
        parts.append(f"({attr_str})")
    
    return " ".join(parts)


def build_target(action_repr: str, correct_idx: int, operation: dict) -> str:
    """
    Build the target answer string.
    
    Format: [idx] OP element_description: value
    e.g.: [3] SELECT [combobox] Reservation type: Pickup
    """
    op = operation["op"]
    value = operation.get("value", "")
    
    # The action_repr format is: [tag]  text -> OP: value
    # Extract the element description from action_repr
    # e.g. "[combobox]  Reservation type -> SELECT: Pickup"
    elem_desc = action_repr.split(" -> ")[0].strip() if " -> " in action_repr else action_repr
    
    if value:
        return f"[{correct_idx}] {op} {elem_desc}: {value}"
    else:
        return f"[{correct_idx}] {op} {elem_desc}"


def process_step(task: dict, step_idx: int, rng: random.Random) -> dict:
    """
    Convert a single step within a task into an ACE-format sample.
    
    Returns None if the step can't be processed (e.g., no pos_candidates).
    """
    action = task["actions"][step_idx]
    action_repr = task["action_reprs"][step_idx]
    html = action["cleaned_html"]
    operation = action["operation"]
    
    pos_candidates = action["pos_candidates"]
    neg_candidates = action["neg_candidates"]
    
    if not pos_candidates:
        return None
    
    # Sample negative candidates
    n_neg = min(MAX_NEG_CANDIDATES, len(neg_candidates))
    sampled_neg = rng.sample(neg_candidates, n_neg) if n_neg > 0 else []
    
    # Combine and shuffle candidates
    all_candidates = []
    
    for pc in pos_candidates:
        all_candidates.append(("pos", pc))
    for nc in sampled_neg:
        all_candidates.append(("neg", nc))
    
    rng.shuffle(all_candidates)
    
    # Build candidate list and find correct index
    candidate_reprs = []
    correct_idx = -1
    for i, (label, cand) in enumerate(all_candidates):
        repr_str = get_candidate_repr(cand, html, i)
        candidate_reprs.append(repr_str)
        if label == "pos" and correct_idx == -1:
            correct_idx = i
    
    if correct_idx == -1:
        return None
    
    # Build context (candidate list)
    context = "Candidate elements on the current webpage:\n" + "\n".join(candidate_reprs)
    
    # Build question (task + history)
    question_parts = [f"Task: {task['confirmed_task']}"]
    question_parts.append(f"Website: {task['website']} (Domain: {task['domain']})")
    
    if step_idx > 0:
        question_parts.append("\nActions completed so far:")
        for j in range(step_idx):
            question_parts.append(f"  Step {j+1}: {task['action_reprs'][j]}")
    
    question_parts.append(
        "\nFrom the candidate elements listed in the context, "
        "select the correct element index and specify the action "
        "(CLICK, TYPE, or SELECT with value if applicable).\n"
        "Answer format: [element_index] ACTION_TYPE [element_tag] element_text: value"
    )
    
    question = "\n".join(question_parts)
    
    # Build target
    target = build_target(action_repr, correct_idx, operation)
    
    return {
        "context": context,
        "question": question,
        "target": target,
        "annotation_id": task["annotation_id"],
        "step_idx": step_idx,
        "total_steps": len(task["actions"]),
        "domain": task["domain"],
        "website": task["website"],
        "action_repr": action_repr,
        "operation": operation,
        "n_candidates": len(all_candidates),
        "correct_candidate_idx": correct_idx
    }


def main():
    from datasets import load_dataset
    
    print("=" * 60)
    print("Mind2Web Data Preparation for ACE")
    print("=" * 60)
    
    # Load dataset
    print("\nLoading Mind2Web dataset from HuggingFace...")
    ds = load_dataset("osunlp/Mind2Web", split="train")
    print(f"Loaded {len(ds)} tasks")
    
    rng = random.Random(SEED)
    
    # Convert all steps to ACE samples
    print("\nConverting steps to ACE samples...")
    all_samples = []
    skipped = 0
    
    for task_idx in range(len(ds)):
        task = ds[task_idx]
        n_steps = len(task["actions"])
        
        for step_idx in range(n_steps):
            sample = process_step(task, step_idx, rng)
            if sample:
                all_samples.append(sample)
            else:
                skipped += 1
        
        if (task_idx + 1) % 100 == 0:
            print(f"  Processed {task_idx + 1}/{len(ds)} tasks, "
                  f"{len(all_samples)} samples so far...")
    
    print(f"\nTotal samples: {len(all_samples)} (skipped {skipped} steps with no pos_candidates)")
    
    # Domain statistics
    domain_counts = Counter(s["domain"] for s in all_samples)
    print(f"\nDomain distribution:")
    for domain, cnt in domain_counts.most_common():
        print(f"  {domain}: {cnt} samples")
    
    # Split by task annotation_id (stratified by domain)
    # Group tasks by domain
    task_ids_by_domain = {}
    for task in ds:
        domain = task["domain"]
        if domain not in task_ids_by_domain:
            task_ids_by_domain[domain] = []
        task_ids_by_domain[domain].append(task["annotation_id"])
    
    train_task_ids = set()
    val_task_ids = set()
    test_task_ids = set()
    
    for domain, task_ids in task_ids_by_domain.items():
        rng.shuffle(task_ids)
        n = len(task_ids)
        n_train = int(n * 0.6)
        n_val = int(n * 0.15)
        
        train_task_ids.update(task_ids[:n_train])
        val_task_ids.update(task_ids[n_train:n_train + n_val])
        test_task_ids.update(task_ids[n_train + n_val:])
    
    # Split samples
    train_samples = [s for s in all_samples if s["annotation_id"] in train_task_ids]
    val_samples = [s for s in all_samples if s["annotation_id"] in val_task_ids]
    test_samples = [s for s in all_samples if s["annotation_id"] in test_task_ids]
    
    print(f"\n=== Data Split (by task, stratified by domain) ===")
    print(f"Train: {len(train_samples)} samples from {len(train_task_ids)} tasks")
    print(f"Val:   {len(val_samples)} samples from {len(val_task_ids)} tasks")
    print(f"Test:  {len(test_samples)} samples from {len(test_task_ids)} tasks")
    
    # Save to JSONL
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    for name, samples in [("train", train_samples), ("val", val_samples), ("test", test_samples)]:
        path = os.path.join(OUTPUT_DIR, f"mind2web_{name}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for s in samples:
                f.write(json.dumps(s, ensure_ascii=True) + "\n")
        print(f"Saved {len(samples)} samples to {path}")
    
    # Also save a smaller train subset for quick experiments
    # Take first 200 train samples (roughly ~30 tasks)
    train_small = train_samples[:200]
    path = os.path.join(OUTPUT_DIR, "mind2web_train_200.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for s in train_small:
            f.write(json.dumps(s, ensure_ascii=True) + "\n")
    print(f"Saved {len(train_small)} samples to {path} (small train set)")
    
    # Print sample
    print(f"\n=== Example Sample ===")
    ex = train_samples[0]
    print(f"Context (first 500 chars):\n{ex['context'][:500]}\n...")
    print(f"\nQuestion:\n{ex['question']}")
    print(f"\nTarget: {ex['target']}")
    print(f"\nMetadata: domain={ex['domain']}, website={ex['website']}, "
          f"step={ex['step_idx']+1}/{ex['total_steps']}")


if __name__ == "__main__":
    main()
