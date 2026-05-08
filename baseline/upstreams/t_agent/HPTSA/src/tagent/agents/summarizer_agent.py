from agents import Agent
from pathlib import Path
import os

summarizer_agent = Agent(
    name="Summarizer Agent",
    instructions=Path(os.path.realpath('src/tagent/prompts/summarizer_prompt.md')).read_text(),
    model="gpt-4.1-2025-04-14",
)



