from agents import Agent, Runner, FileSearchTool, ItemHelpers
from pathlib import Path
import asyncio
import os
import subprocess
from dotenv import load_dotenv

from tagent.tools.general_tools import pip_install, run_bash, python_script
from tagent.tools.browser_tools import get_page_source_tool, extract_text_tool, extract_hyperlinks_tool, get_elements_tool
from tagent.agent_dependencies import AgentDeps

# Load environment variables from .env file
load_dotenv()

vector_store_id = os.getenv("OPENAI_VECTOR_STORE_ID") 

general_agent = Agent[AgentDeps](
    name="General Agent",
    instructions=Path(os.path.realpath('src/tagent/prompts/general_prompt.md')).read_text(),
    model="gpt-4.1-2025-04-14",
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
            vector_store_ids=[vector_store_id]
        )
    ],
)

async def main():
    prompt = os.getenv("PROMPT")
    os.makedirs('./agent_workspace', exist_ok=True)
    agent_deps = AgentDeps(workspace=Path('./agent_workspace'))
    result = Runner.run_streamed(
        general_agent,
        prompt,
        context=agent_deps,
        max_turns=100,
    )
    print("=== Run starting ===")
    
    async for event in result.stream_events():
        if event.type == "agent_updated_stream_event":
            print(f"Agent updated: {event.new_agent.name}")
            continue
        elif event.type == "run_item_stream_event":
            if event.item.type == "tool_call_item":
                print("-- Tool was called")
                print(event.item.raw_item)
            elif event.item.type == "tool_call_output_item":
                print(f"-- Tool output: {event.item.output}")
            elif event.item.type == "message_output_item":
                print(f"-- Message output:\n {ItemHelpers.text_message_output(event.item)}")
            else:
                pass  # Ignore other event types

    print("=== Run complete ===")
    result = subprocess.run(['curl', 'http://target:9091/done'], capture_output=True, text=True)

    if result.returncode == 0:
        print("Command executed successfully:")
        print("Standard Output:", result.stdout)
        print("Standard Error:", result.stderr)
    else:
        print("Command failed with error code:", result.returncode)
        print("Standard Error:", result.stderr)
        


if __name__ == "__main__":
    asyncio.run(main())