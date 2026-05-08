from agents import FileSearchTool
from pathlib import Path
import os
from dotenv import load_dotenv

from tagent.tools.general_tools import pip_install, run_bash, python_script
from tagent.tools.browser_tools import (
    get_page_source_tool,
    extract_text_tool,
    extract_hyperlinks_tool,
    get_elements_tool,
)
from tagent.tools.zap_tools import zap_baseline_scan
from tagent.agents.general_agent import general_agent

load_dotenv()

vector_store_id = os.getenv("OPENAI_VECTOR_STORE_ID")

zap_agent = general_agent.clone(
    name="ZAP Agent",
    instructions=Path(
        os.path.realpath("src/tagent/prompts/zap_prompt.md")
    ).read_text(),
    tools=[
        pip_install,
        python_script,
        run_bash,
        get_page_source_tool,
        extract_text_tool,
        extract_hyperlinks_tool,
        get_elements_tool,
        FileSearchTool(
            max_num_results=3,
            vector_store_ids=[vector_store_id],
        ),
        zap_baseline_scan,
    ],
)

