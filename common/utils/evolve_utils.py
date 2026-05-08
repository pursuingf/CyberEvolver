import re
import os
import json
import tiktoken
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass

from rich import print as rprint
from rich.tree import Tree
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.console import Group

# -----------------------------------------------------------------------------
# 1. Simple Data Holder (Internal Use Only)
# -----------------------------------------------------------------------------
@dataclass
class NodeData:
    name: str
    gen_idx: int
    log_content: str
    full_path: Path

# -----------------------------------------------------------------------------
# 2. Logic: Parse Parent from Folder Name
# -----------------------------------------------------------------------------
def extract_parent_name(folder_name: str) -> Optional[str]:
    """
    Determines parent name based strictly on the folder naming convention:
    Pattern: geni_{PARENT_NAME}_childj
    
    1. It must start with 'gen' followed by digits.
    2. It must end with '_child' followed by digits.
    3. The middle part is the parent name.
    """
    # Regex explanation:
    # ^gen\d+_   : Starts with "gen", one or more digits, and an underscore.
    # (.+)       : Capture Group 1 (The Parent Name) - everything in the middle.
    # _child\d+$ : Ends with underscore, "child", one or more digits.
    pattern = r"^gen\d+_(.+)_child\d+$"
    
    match = re.match(pattern, folder_name)
    if match:
        return match.group(1)
    
    # If regex doesn't match (e.g., 'gen0_root'), we assume it has no parent (Root).
    return None

def get_generation_number(folder_name: str) -> int:
    """Extracts '0' from 'gen0_root' or 'gen1_...'"""
    match = re.match(r"^gen(\d+)", folder_name)
    return int(match.group(1)) if match else -1

# -----------------------------------------------------------------------------
# 3. File Operations
# -----------------------------------------------------------------------------
def read_log(node_path: Path) -> str:
    log_file = node_path / "reflection" / "patch_apply.log"
    if log_file.exists():
        try:
            content = log_file.read_text(encoding='utf-8').strip()
            return content if content else "[Empty Log]"
        except Exception as e:
            return f"[Error reading log: {e}]"
    return "[Log file not found]"

# -----------------------------------------------------------------------------
# 4. Main Visualization Function
# -----------------------------------------------------------------------------
def visualize_evolution_tree(root_directory: str):
    root_path = "evolution_data" / Path(root_directory)
    
    if not root_path.exists():
        rprint(f"[bold red]Error: Directory '{root_directory}' does not exist.[/]")
        return

    # --- Phase A: Scan and Load all Nodes ---
    # Map: node_name -> NodeData object
    all_nodes: Dict[str, NodeData] = {}
    
    # Adjacency List: parent_name -> List[NodeData children]
    children_map: Dict[str, List[NodeData]] = {}
    
    # List of nodes that have no identified parent (Roots)
    roots: List[NodeData] = []

    # Walk through gen0, gen1, etc.
    for gen_folder in root_path.iterdir():
        if gen_folder.is_dir() and gen_folder.name.startswith("gen"):
            
            for node_folder in gen_folder.iterdir():
                if node_folder.is_dir():
                    node_name = node_folder.name
                    
                    # Create data object
                    node_data = NodeData(
                        name=node_name,
                        gen_idx=get_generation_number(node_name),
                        log_content=read_log(node_folder),
                        full_path=node_folder
                    )
                    
                    all_nodes[node_name] = node_data

    # --- Phase B: Build Topology ---
    # Now that we have all nodes, we parse names to link them.
    for name, node in all_nodes.items():
        parent_name = extract_parent_name(name)
        
        if parent_name:
            # Check if the parent actually exists in our scanned data
            if parent_name in all_nodes:
                if parent_name not in children_map:
                    children_map[parent_name] = []
                children_map[parent_name].append(node)
            else:
                # Parent extracted from name, but folder not found on disk.
                # Treat as a separate root or broken link.
                roots.append(node)
        else:
            # No parent pattern matched (e.g. gen0_root) -> It is a Root
            roots.append(node)

    # Sort roots by generation (usually just gen0)
    roots.sort(key=lambda x: x.gen_idx)

    # --- Phase C: Recursive Rendering ---
    
    def add_branch(tree_cursor, current_node: NodeData):
        # 1. Prepare Label
        node_label = Text()
        node_label.append(f"Gen {current_node.gen_idx} ", style="bold blue")
        node_label.append(f":: {current_node.name}", style="bold green")

        # 2. Prepare Log Panel
        # Auto-detect if it's a diff file for coloring
        is_diff = "diff" in current_node.log_content or "@@" in current_node.log_content
        syntax_theme = "monokai" if is_diff else "ansi_dark"
        lexer = "diff" if is_diff else "text"
        
        log_view = Syntax(
            current_node.log_content, 
            lexer, 
            theme=syntax_theme, 
            word_wrap=True
        )

        panel = Panel(
            log_view,
            title="patch_apply.log",
            border_style="dim white",
            expand=False
        )

        # 3. Add to Rich Tree
        # Group ensures the label and the panel stay together
        branch = tree_cursor.add(Group(node_label, panel))

        # 4. Find Children and Recurse
        children = children_map.get(current_node.name, [])
        # Sort children by name to keep order consistent
        children.sort(key=lambda x: x.name)
        
        for child in children:
            add_branch(branch, child)

    # --- Execution ---
    main_tree = Tree(f":seedling: [bold uppercase]Evolution Tree: {root_path.name}[/]")
    
    if not roots:
        main_tree.add("[yellow]No 'gen*' folders found.[/]")
    
    for root in roots:
        add_branch(main_tree, root)

    rprint(main_tree)

def visualize_evolution_path(root_directory: str,node_name: str):
    root_directory = "evolution_data" / Path(root_directory)
    if not root_directory.exists():
        rprint(f"[bold red]Error: Directory '{root_directory}' does not exist.[/]")
        return
    
    main_tree = Tree(f":seedling: [bold uppercase]Evolution Path: {node_name}[/]")
    path = []
    while node_name :
        gen_idx = get_generation_number(node_name)
        node_folder = root_directory / f"gen_{gen_idx}" /node_name

        if not node_folder.exists():
            rprint(f"[bold red]Error: Directory '{node_folder}' does not exist.[/]")
            return
        
        node_data = NodeData(
            name=node_name,
            gen_idx=gen_idx,
            log_content=read_log(node_folder),
            full_path=node_folder
        )
        path.append(node_data)

        node_name = extract_parent_name(node_name)

    for current_node in reversed(path):
        # 1. Prepare Label
        node_label = Text()
        node_label.append(f"Gen {current_node.gen_idx} ", style="bold blue")
        node_label.append(f":: {current_node.name}", style="bold green")

        # 2. Prepare Log Panel
        # Auto-detect if it's a diff file for coloring
        is_diff = "diff" in current_node.log_content or "@@" in current_node.log_content
        syntax_theme = "monokai" if is_diff else "ansi_dark"
        lexer = "diff" if is_diff else "text"
        
        log_view = Syntax(
            current_node.log_content, 
            lexer, 
            theme=syntax_theme, 
            word_wrap=True
        )

        panel = Panel(
            log_view,
            title="patch_apply.log",
            border_style="dim white",
            expand=False
        )

        # 3. Add to Rich Tree
        # Group ensures the label and the panel stay together
        branch = main_tree.add(Group(node_label, panel))  
    rprint(main_tree)      
                    


def chunk_log(log_data: dict, model_max_token: int = 32768 , reserved_output: int = 2000) -> int:
    try:
        enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        enc = tiktoken.get_encoding("gpt2")  # Fallback encoding.

    # 2. Base capacity setup.
    # Effective limit = model max tokens minus tokens reserved for output.
    limit = model_max_token - reserved_output
            
    # 3. Account for the final chunk overhead from system and instance prompts.
    sys_tokens = len(enc.encode(log_data.get("system_prompt", "")))
    inst_tokens = len(enc.encode(log_data.get("instance_prompt", "")))
    prompt_overhead = sys_tokens + inst_tokens

    if prompt_overhead > limit:
        raise ValueError("System/Instance prompts plus reserved output exceed the model token limit")
    limit -= prompt_overhead

            # 4. Greedy pass over steps.
    current_chunk_tokens = 0
    steps = log_data.get("steps", [])
    n_steps = len(steps)
    cuts = []

    for step in steps:
        # Estimate tokens for one step using the JSON-like representation.
        step_str = step["action"] + step["observation"]
        step_tokens = len(enc.encode(step_str))
        # Start a new chunk when the current chunk cannot fit this step.
        if current_chunk_tokens + step_tokens > limit:
            cuts.append(step["step"])
            current_chunk_tokens = step_tokens
        else:
            current_chunk_tokens += step_tokens
    if not cuts or cuts[-1] != n_steps: cuts.append(n_steps)

    chunks = []
    start = 0
    for idx, end in enumerate(cuts):
        chunk_steps = steps[start:end]
        if not chunk_steps: 
            start = end
            continue
        chunks.append({
            "system_prompt": log_data["system_prompt"],
            "instance_prompt": log_data["instance_prompt"],
            "steps": chunk_steps,
            "final_message": log_data["final_message"] if idx == len(cuts) - 1 else "",
            "is_last": (idx == len(cuts) - 1),
            "step_range": (chunk_steps[0]["step"], chunk_steps[-1]["step"])
        })
        start = end
    if not chunks: chunks.append({"system_prompt": log_data["system_prompt"], "steps": [], "is_last": True, "step_range": (0,0), "final_message": ""})
    return chunks
    
def parse_agent_log(log_file: str | Path) -> dict:
    log_path = Path(log_file)
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    with log_path.open("r", encoding="utf-8", errors='replace') as f:
        log_content = f.read()

    token_pattern = r'token_num:\s*(\d+)'
    token_match = re.search(token_pattern,log_content.split('\n')[-2])
    token_num = int(token_match.group(1).strip())
    rprint(f"total token num: {token_num}")
    steps = []
    step_pattern = r'--- Step \d+/\d+ ---'
    step_splits = re.split(step_pattern, log_content)
        
    if len(step_splits) < 2:
        header = log_content.strip()
        step_contents = []
    else:
        header = step_splits[0].strip()
        step_contents = [s.strip() for s in step_splits[1:] if s.strip()]

        # Parse System & Instance Prompts
    system_prompt_match = re.search(r"System Prompt:\s*\n(.*?)(?=\n\d{4}-\d{2}-\d{2}|$)", header, re.DOTALL)
    instance_prompt_match = re.search(r"Instance prompt:\s*\n(.*?)(?=\n\d{4}-\d{2}-\d{2}|$)", header, re.DOTALL)

    system_prompt = system_prompt_match.group(1).strip() if system_prompt_match else ""
    instance_prompt = instance_prompt_match.group(1).strip() if instance_prompt_match else ""

    for i, block in enumerate(step_contents, start=1):
        action_match = re.search(r'Agent Thought:\s*\n(.*?)(?=\n\s*Agent Observation:|\Z)', block, re.DOTALL)
        action = action_match.group(1).strip() if action_match else ""
        obs_match = re.search(r'Agent Observation: <output>\s*(.*?)\s*</output>', block, re.DOTALL)
        observation = obs_match.group(1).strip() if obs_match else ""
        steps.append({"step": i, "action": action, "observation": observation if len(observation)<50001 else "output too long! ONLY display the first 50000 characters"+observation[:50000]})

    last_output_end = log_content.rfind("</output>")
    final_message = "" if last_output_end == -1 else log_content[last_output_end + len("</output>"):].strip()
    return {
        "steps": steps,
        "system_prompt": system_prompt,
        "instance_prompt": instance_prompt,
        "final_message": final_message,
        "token_num":token_num
    }

def check_average_coderefiner_tokens(root_dir:str):
    try:
        enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        enc = tiktoken.get_encoding("gpt2")  # Fallback encoding.

    summary_tokens = []
    for root,_,files in os.walk(root_dir):
        for file in files:
            if file != "evolution_plan.md":
                continue
            with open(Path(root) / Path(file),'r') as f:
                token = len(enc.encode(f.read()))
                summary_tokens.append(token)
                rprint(f"Current node : {root}. Coderefiner costs {token} tokens to evolve")
    rprint(f"Average Tokens cost : {sum(summary_tokens) // len(summary_tokens)}")

if __name__ == "__main__":
    # visualize_evolution_tree("archieved/evo_test_012022q-pwn-ezrop")
    # visualize_evolution_path("archieved/evo_test_012022q-pwn-ezrop","gen2_gen1_gen0_root_child2_child1")

    sample_log = os.getenv("EVOLVE_UTILS_SAMPLE_LOG")
    if sample_log:
        log_data = parse_agent_log(sample_log)
        chunks = chunk_log(log_data)
        print("chunk number:", len(chunks))

    #check_average_coderefiner_tokens("evolution_data")
    pass
