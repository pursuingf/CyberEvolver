#!/usr/bin/env python3
import argparse
import json
import sys
import textwrap
from pathlib import Path
from typing import List, Dict, Optional


def cmd_doc() -> str:
    return """
signature: load_skill --name <skill_name>
docstring: Loads a specialized skill module, outputting its Guide (knowledge/workflow) and tool locations.
arguments:
    name (str, required): The exact name of the skill to load.
 """

def get_skills_root() -> Path:
    """
    Resolves the 'skills' directory relative to this script.
    Assumption: script is in ./commands/, skills is in ./skills/
    Path: ../skills
    """
    # resolve() handles symlinks and absolute paths
    script_dir = Path(__file__).resolve().parent
    # Go up one level and look for 'skills'
    skills_root = script_dir.parent / "skills"

    if not skills_root.exists():
        # Fallback: try relative to CWD if script path resolution fails logic
        cwd_skills = Path.cwd() / "skills"
        if cwd_skills.exists():
            return cwd_skills

    return skills_root

def load_index(skills_root: Path) -> List[Dict]:
    """Loads and validates the index.json."""
    index_path = skills_root / "index.json"

    if not index_path.exists():
        print(f"CRITICAL ERROR: Skill index not found at {index_path}", file=sys.stderr)
        print("Hint: Has 'skill.py' been run to generate the index?", file=sys.stderr)
        sys.exit(1)

    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"CRITICAL ERROR: Malformed JSON in {index_path}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"CRITICAL ERROR: Could not read index: {e}", file=sys.stderr)
        sys.exit(1)

def print_available_skills(skills_data: List[Dict]):
    """Helper to print a nice list of available skills."""
    print("\n[!] Available Skills:", file=sys.stderr)
    if not skills_data:
        print("    (No skills found in index)", file=sys.stderr)
    for s in skills_data:
        name = s.get('name', 'unknown')
        desc = s.get('description', '').split('\n')[0] # First line only
        print(f"    - {name}: {desc}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description="Load a CTF Skill Module")
    parser.add_argument("--name", required=True, help="Name of the skill to load")
    args = parser.parse_args()

    target_name = args.name.strip()
    skills_root = get_skills_root()

    # 1. Load Index
    skills_data = load_index(skills_root)

    # 2. Validate Skill Existence
    # We use a case-sensitive match because Linux filesystems are case-sensitive
    skill_entry = next((s for s in skills_data if s['name'] == target_name), None)

    if not skill_entry:
        print(f"ERROR: Skill '{target_name}' not found.", file=sys.stderr)
        print_available_skills(skills_data)
        sys.exit(1)

    # 3. Locate Files
    skill_dir = skills_root / target_name
    guide_path = skill_dir / "SKILL.md"
    tools_path = skill_dir / "tools"

    if not skill_dir.exists():
        print(f"ERROR: Skill '{target_name}' exists in index but directory is missing!", file=sys.stderr)
        print(f"Expected path: {skill_dir}", file=sys.stderr)
        sys.exit(1)

    if not guide_path.exists():
        print(f"ERROR: Skill '{target_name}' is missing 'SKILL.md'. Cannot load knowledge.", file=sys.stderr)
        sys.exit(1)

    # 4. Success Output
    # We print a structured header for the Agent to parse easily
    print(f"=== SKILL LOADED: {target_name} ===")

    # Check for tools and print path info
    if tools_path.exists() and any(tools_path.iterdir()):
        print(f"[SYSTEM] Tools detected at: {tools_path.absolute()}")
        print(f"[SYSTEM] Action Required: export PATH=$PATH:{tools_path.absolute()}")
    else:
        print("[SYSTEM] No specialized tools included in this skill.")

    print("\n--- BEGIN GUIDE ---")
    try:
        with open(guide_path, 'r', encoding='utf-8') as f:
            print(f.read())
    except Exception as e:
        print(f"\n[!] Error reading SKILL.md: {e}", file=sys.stderr)
        sys.exit(1)
    print("\n--- END GUIDE ---")

if __name__ == "__main__":
    main()