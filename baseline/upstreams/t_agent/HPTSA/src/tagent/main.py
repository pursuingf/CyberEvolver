from agents import Agent, Runner, ItemHelpers
from agents.extensions.handoff_prompt import prompt_with_handoff_instructions
import argparse
import asyncio
import os
import sys
from pathlib import Path

from tagent.agent_dependencies import AgentDeps
from tagent.tools.agent_tools import (
    call_general_agent,
    call_csrf_agent,
    call_xss_agent,
    call_ssti_agent,
    call_sql_agent,
    call_zap_agent,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROMPTS_DIR = _PACKAGE_DIR / "prompts"

DEFAULT_MODEL = "gpt-4.1-2025-04-14"


def _create_supervisor_agent(model: str) -> Agent[AgentDeps]:
    return Agent[AgentDeps](
        name="Supervisor",
        instructions=prompt_with_handoff_instructions(
            (_PROMPTS_DIR / "supervisor_prompt.md").read_text()
        ),
        model=model,
        tools=[
            call_general_agent,
            call_csrf_agent,
            call_xss_agent,
            call_ssti_agent,
            call_sql_agent,
            call_zap_agent,
        ],
    )


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Run HPTSA supervisor with a task prompt.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--prompt",
        type=str,
        help="Task prompt as a string (e.g. 'Hack the website at http://...').",
    )
    group.add_argument(
        "--prompt-file",
        type=Path,
        metavar="PATH",
        help="Path to a file whose contents are the task prompt.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"OpenAI model to use for the supervisor and subagents (default: {DEFAULT_MODEL}).",
    )
    return parser.parse_args()


def _load_prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        return args.prompt
    path = args.prompt_file
    if not path.exists():
        print(f"Error: prompt file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return path.read_text().strip()


async def main():
    args = _parse_args()
    prompt = _load_prompt(args)

    os.makedirs("./agent_workspace", exist_ok=True)
    agent_deps = AgentDeps(
        workspace=Path("./agent_workspace"),
        initial_prompt=prompt,
        model=args.model,
    )
    supervisor_agent = _create_supervisor_agent(args.model)
    result = Runner.run_streamed(
        supervisor_agent,
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

    # # CVE-BENCH success check
    # print("=== Run complete ===")
    # result = subprocess.run(['curl', 'http://target:9091/done'], capture_output=True, text=True)

    # if result.returncode == 0:
    #     print("Command executed successfully:")
    #     print("Standard Output:", result.stdout)
    #     print("Standard Error:", result.stderr)
    # else:
    #     print("Command failed with error code:", result.returncode)
    #     print("Standard Error:", result.stderr)
        


if __name__ == "__main__":
    asyncio.run(main())