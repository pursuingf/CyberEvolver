from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentDeps:
    workspace: Path
    initial_prompt: str = ""
    model: str = "gpt-4.1-2025-04-14"
