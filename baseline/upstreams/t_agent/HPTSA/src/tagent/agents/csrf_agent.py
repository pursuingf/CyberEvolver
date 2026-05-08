from agents import FileSearchTool
from pathlib import Path
import os
from dotenv import load_dotenv

from tagent.agents.general_agent import general_agent

load_dotenv()

vector_store_id = os.getenv("OPENAI_VECTOR_STORE_ID") 

csrf_agent = general_agent.clone(
    name="CSRF Agent",
    instructions=Path(os.path.realpath('src/tagent/prompts/csrf_prompt.md')).read_text(),
)