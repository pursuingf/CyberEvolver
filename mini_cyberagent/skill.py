import json
import textwrap
import sys
import traceback
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, List, Dict

# ======================
# 📦 Data Structures
# ======================

@dataclass
class SkillMetadata:
    name: str
    description: str
    has_tools: bool
    guide_status: str  # 'present' or 'missing'

# ======================
# 🧠 Skill Class
# ======================

class Skill:
    """
    Represents a specialized CTF Skill Module located in the skills/ directory.
    Strictly adheres to the structure:
      skills/<name>/
      ├── description.md  (The Trigger/Ad)
      ├── SKILL.md        (The Brain)
      └── tools/          (The Hands)
    """
    def __init__(self, dir_path: str):
        self.dir_path = Path(dir_path)
        if not self.dir_path.exists() or not self.dir_path.is_dir():
            raise FileNotFoundError(f"Skill directory not found: {dir_path}")
        
        self.name = self.dir_path.name
        self.metadata = self._extract_metadata()

    def _read_file_safe(self, filename: str, required: bool = True) -> Optional[str]:
        """Safely read a file with error handling."""
        fpath = self.dir_path / filename
        if not fpath.exists():
            if required:
                raise FileNotFoundError(f"Missing required file: {filename} in {self.name}")
            return None
        
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            if required:
                raise IOError(f"Failed to read {filename}: {e}")
            return None

    def _check_tools(self) -> bool:
        """Check if tools directory exists and is not empty."""
        tools_dir = self.dir_path / "tools"
        if not tools_dir.exists():
            return False
        # Return True if there is at least one file that is not hidden
        return any(f.is_file() and not f.name.startswith('.') for f in tools_dir.iterdir())

    def _extract_metadata(self) -> SkillMetadata:
        """Parses the skill directory to build metadata."""
        
        # 1. Get Description (The Trigger)
        # We treat the entire content of description.md as the description/trigger
        raw_desc = self._read_file_safe("description.md", required=True)
        
        # 2. Check Guide (The Brain)
        guide_content = self._read_file_safe("SKILL.md", required=False)
        guide_status = "present" if guide_content else "missing"

        # 3. Check Tools (The Hands)
        has_tools = self._check_tools()

        return SkillMetadata(
            name=self.name,
            description=raw_desc,
            has_tools=has_tools,
            guide_status=guide_status
        )

    def to_index_entry(self) -> Dict:
        """Returns the dictionary format required for index.json."""
        return {
            "name": self.metadata.name,
            "description": self.metadata.description,
            "has_tools": self.metadata.has_tools
        }

    def get_prompt_info(self) -> str:
        """Formatted string for CLI output/debugging."""
        desc = textwrap.indent(self.metadata.description, '')
        
        return (
            f"name: {self.name}\n"
            f"description: \n{desc}\n\n"
        )

# ======================
# 🛠️ Index Manager
# ======================

def refresh_skill_index(skills_root: Path) -> None:
    """Scans the skills directory and updates index.json."""
    index_file = skills_root / "index.json"
    
    # Identify potential skill directories (exclude hidden & files)
    skill_dirs = [
        d for d in skills_root.iterdir() 
        if d.is_dir() and not d.name.startswith('.') and d.name != "__pycache__"
    ]

    valid_skills = []
    print(f"🔍 Scanning {len(skill_dirs)} potential skills in '{skills_root}'...\n")

    for d in sorted(skill_dirs):
        try:
            skill = Skill(str(d))
            valid_skills.append(skill.to_index_entry())
            print(f"✅ {skill.name}")
            print(f"   Tools: {skill.metadata.has_tools} | Guide: {skill.metadata.guide_status}")
            print(f"prompts:\n{skill.get_prompt_info()}")
        except Exception as e:
            print(f"❌ {d.name} → {e}", file=sys.stderr)
            # We explicitly do NOT add invalid skills to the index
            
    # Write Index
    try:
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(valid_skills, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Index updated: {index_file} ({len(valid_skills)} skills)")
    except Exception as e:
        print(f"💥 Failed to write index.json: {e}", file=sys.stderr)
        sys.exit(1)

# ======================
# 🚀 Main Execution
# ======================

if __name__ == "__main__":
    # Default location: ./skills relative to CWD or script location
    # Prefer strict path resolution
    base_path = Path("./skills")
    
    if not base_path.exists():
        # Try finding it relative to the script if running from root
        script_path = Path(__file__).resolve().parent
        base_path = script_path / "skills"
        
    if not base_path.exists():
        print(f"⚠️  Skills directory not found at {base_path}", file=sys.stderr)
        sys.exit(1)

    refresh_skill_index(base_path)
    print()