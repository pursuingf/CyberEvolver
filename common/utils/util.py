import importlib.util
import sys
from pathlib import Path
import re
from typing import List, Dict, Any, Optional, Mapping
import yaml
from datetime import datetime

def load_prompt_config(prompt_path: str) -> Dict[str, Any]:
    """Load prompt config from YAML. Returns empty dict if not found."""
    yaml_path = Path(prompt_path)
    if not yaml_path.exists():
        print(f"[utils] ⚠️ Warning: {yaml_path} not found. Using empty defaults.")
        return {}
    with yaml_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def llm_invoke(llm, message, meta: Optional[Mapping[str, Any]] = None):
    """Thin LLM invoke wrapper with optional per-call dispatch metadata.

    Retry is intentionally owned by the centralized dispatcher so request
    pacing stays globally coordinated across processes and threads.
    """
    if meta:
        if hasattr(llm, "scope"):
            with llm.scope(dict(meta)):
                return llm.invoke(message)
        if getattr(llm, "accepts_dispatch_meta", False):
            return llm.invoke(message, _dispatch_meta=dict(meta))
    return llm.invoke(message)

def import_star_from_file(file_path: str, target_globals: dict):
    """
    Simulates `from file_path import *` into `target_globals`.
    Only imports names that don't start with '_'.
    """
    file_path = Path(file_path).resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"Agent code file not found: {file_path}")

    module_name = f"dynamic_agent_{file_path.stem}_{abs(hash(str(file_path))) % 10000}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec from {file_path}")

    module = importlib.util.module_from_spec(spec)
    # Inject the file's parent dir into sys.path so seed-style agents (e.g.
    # cyber_evolver/gen0_root/skill_based/agent.py) can resolve sibling
    # modules like `from benchmark_scorers import ...`. Mirrors the pattern
    # used by cyber_evolver/evolve/node.py:153.
    src_dir = str(file_path.parent)
    sys.path.insert(0, src_dir)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise RuntimeError(f"Failed to execute agent module {file_path}: {e}") from e
    finally:
        if sys.path and sys.path[0] == src_dir:
            sys.path.pop(0)

    # Mimic `from module import *`: import all public names
    imported = 0
    for name in dir(module):
        if not name.startswith('_'):
            target_globals[name] = getattr(module, name)
            imported += 1

    print(f" Imported {imported} public symbol(s) from {file_path} into current scope.")


def parse_agent_log(log_file: str | Path) -> dict:
    log_path = Path(log_file)
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    with log_path.open("r", encoding="utf-8", errors='replace') as f:
        log_content = f.read()

    # --- Token parsing ---
    token_pattern = r"(?:token_num|Tokens?)\s*[:'\"’]?\s*(\d+)"
    token_match = re.search(token_pattern, log_content.split('\n')[-2])
    token_num = int(token_match.group(1).strip()) if token_match else 0
    
    steps = []
    # --- Step splitting ---
    step_pattern = r'--- Step \d+/\d+ ---'
    step_splits = re.split(step_pattern, log_content)
    
    if len(step_splits) < 2:
        header = log_content.strip()
        step_contents = []
    else:
        header = step_splits[0].strip()
        step_contents = [s.strip() for s in step_splits[1:] if s.strip()]

    # --- System & Instance Prompts ---
    system_prompt_match = re.search(r"System Prompt:\s*\n(.*?)(?=\n\d{4}-\d{2}-\d{2}|$)", header, re.DOTALL)
    instance_prompt_match = re.search(r"Instance prompt:\s*\n(.*?)(?=\n\d{4}-\d{2}-\d{2}|$)", header, re.DOTALL)

    system_prompt = system_prompt_match.group(1).strip() if system_prompt_match else ""
    instance_prompt = instance_prompt_match.group(1).strip() if instance_prompt_match else ""

    # --- Timestamp regex ---
    # Match YYYY-MM-DD HH:MM:SS,mmm or .mmm.
    ts_pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,. ]\d{3})"

    def parse_ts(ts_str):
        """Parse a log timestamp string into a datetime object."""
        try:
            clean_ts = ts_str.replace(',', '.')
            return datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            return None

    for i, block in enumerate(step_contents, start=1):
        # 1. Extract response.
        response_match = re.search(r'Agent Response:\s*\n(.*?)(?=\n\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,. ]\d{3} -|\Z)', block, re.DOTALL)
        raw_response = response_match.group(1).strip() if response_match else ""
        
        # 2. Extract action.
        command_matches = re.findall(r"```bash\n(.*?)\n```", raw_response.strip(), re.DOTALL)
        command_blocks = [m.strip() for m in command_matches if m.strip()]
        pre_block_match = re.search(r"^(.*?)```", raw_response, re.DOTALL)
        pre_block_text = pre_block_match.group(1).strip() if pre_block_match else ""
        has_thought = len(pre_block_text) >= 5
        action = command_blocks[0] if command_blocks and len(command_blocks)==1 and has_thought else "No valid command blocks in Agent response to execute"

        # 3. Extract observation.
        obs_match = re.search(r'Agent Observation:\s(.*?)(?=\n\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,. ]\d{3} -|\Z)', block, re.DOTALL)
        observation = obs_match.group(1).strip() if obs_match else ""
        
        # 4. === Time calculation ===
        start_time = None
        end_time = None
        duration = 0.0

        # Start time: find the last timestamp before "Agent Response".
        # This usually marks when the model started emitting the response.
        if response_match:
            text_before_response = block[:response_match.start()]
            ts_before_resp = re.findall(ts_pattern, text_before_response)
            if ts_before_resp:
                start_time = parse_ts(ts_before_resp[-1])

        # End time: find the last timestamp before "Agent Observation".
        # This usually marks when execution ended and observation output began.
        if obs_match:
            text_before_obs = block[:obs_match.start()]
            ts_before_obs = re.findall(ts_pattern, text_before_obs)
            if ts_before_obs:
                end_time = parse_ts(ts_before_obs[-1])
        
        # Compute the response-start to observation-start delta.
        if start_time and end_time:
            diff = (end_time - start_time).total_seconds()
            duration = max(0.0, diff)

        # Format output.
        display_obs = observation if len(observation) < 50001 else "output too long! ONLY display the first 50000 characters" + observation[:50000]

        steps.append({
            "step": i,
            "raw_response": raw_response,
            "action": action,
            "observation": display_obs,
            "execute_time": duration,
            "start_time": start_time.strftime("%H:%M:%S.%f")[:-3] if start_time else None,
            "end_time": end_time.strftime("%H:%M:%S.%f")[:-3] if end_time else None
        })

    last_output_end = log_content.rfind("</output>")
    final_message = "" if last_output_end == -1 else log_content[last_output_end + len("</output>"):].strip()

    return {
        "raw_content":log_content,
        "steps": steps,
        "system_prompt": system_prompt,
        "instance_prompt": instance_prompt,
        "final_message": final_message,
        "token_num": token_num
    }

def chunk_log_data(parsed_log: dict, max_batch: int = 4) -> List[dict]:
    steps = parsed_log["steps"]
    n_steps = len(steps)
    if n_steps == 0:
        return [{"system_prompt": parsed_log["system_prompt"], "instance_prompt": parsed_log["instance_prompt"], "steps": [], "final_message": parsed_log["final_message"], "is_last": True, "step_range": (0, 0)}]

    step_chars = [len(s.get("action", "")) + len(s.get("observation", "")) for s in steps]
    total_chars = sum(step_chars)
    target_per_chunk = total_chars / max_batch if max_batch > 0 else total_chars

    cuts = []
    cumsum = 0
    target_cum = target_per_chunk
    for i in range(n_steps):
        cumsum += step_chars[i]
        if len(cuts) < max_batch - 1 and cumsum >= target_cum:
            cuts.append(i + 1)
            target_cum += target_per_chunk
    while len(cuts) < max_batch - 1: cuts.append(n_steps)
    cuts = cuts[:max_batch - 1] + [n_steps]

    chunks = []
    start = 0
    for idx, end in enumerate(cuts):
        chunk_steps = steps[start:end]
        if not chunk_steps: 
            start = end
            continue
        chunks.append({
            "system_prompt": parsed_log["system_prompt"],
            "instance_prompt": parsed_log["instance_prompt"],
            "steps": chunk_steps,
            "final_message": parsed_log["final_message"] if idx == len(cuts) - 1 else "",
            "is_last": (idx == len(cuts) - 1),
            "step_range": (chunk_steps[0]["step"], chunk_steps[-1]["step"])
        })
        start = end
    if not chunks: chunks.append({"system_prompt": parsed_log["system_prompt"], "steps": [], "is_last": True, "step_range": (0,0), "final_message": ""})
    return chunks
