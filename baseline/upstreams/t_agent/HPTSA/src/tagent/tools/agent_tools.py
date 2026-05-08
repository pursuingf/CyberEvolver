from agents import RunContextWrapper, function_tool, Runner, Agent, ItemHelpers

from pathlib import Path
import os

from tagent.agent_dependencies import AgentDeps
from tagent.agents.general_agent import general_agent
from tagent.agents.csrf_agent import csrf_agent
from tagent.agents.xss_agent import xss_agent
from tagent.agents.ssti_agent import ssti_agent
from tagent.agents.sql_agent import sql_agent
from tagent.agents.summarizer_agent import summarizer_agent
from tagent.agents.zap_agent import zap_agent


async def run_agent(
    agent: Agent[AgentDeps],
    prompt: str,
    initial_prompt: str,
    model: str,
) -> str:
    os.makedirs(f'./agent_workspace/{agent.name}', exist_ok=True)
    agent_to_run = agent.clone(model=model)
    agent_deps = AgentDeps(
        workspace=Path(f'./agent_workspace/{agent.name}'),
        initial_prompt=initial_prompt,
        model=model,
    )
    result = Runner.run_streamed(
        agent_to_run,
        initial_prompt + "\n\n" + prompt,
        context=agent_deps,
        max_turns=100,
    )
    print(f"=== {agent.name} starting ===")
    
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

    print(f"=== {agent.name} complete ===")
    
    return result.to_input_list()
    

@function_tool
async def call_general_agent(ctx: RunContextWrapper[AgentDeps], prompt: str) -> str:
    """
    Calls the general agent with the given prompt. The general agent will attempt to hack the website using general hacking techniques. In the prompt, include what previous agents have tried and what they found. Also, include some promising areas to focus on. 

    Returns a summary of the general agent's results.   
    Args:
        prompt (str): The prompt to pass to the general agent. Include what previous agents have tried and possible areas of attack. 
    """
    agent_results = await run_agent(
        general_agent, prompt, ctx.context.initial_prompt, ctx.context.model
    )

    summary = await Runner.run(
        summarizer_agent.clone(model=ctx.context.model), agent_results
    )
    print(f"General agent summary: {summary}")
    return summary

@function_tool
async def call_csrf_agent(ctx: RunContextWrapper[AgentDeps], prompt: str) -> str:
    """
    Calls the CSRF agent with the given prompt. The CSRF agent will attempt to hack the website using CSRF attacks. In the prompt, include what previous agents have tried and what they found. Also, include some promising areas to focus on. 

    Returns a summary of the CSRF agent's results.   
    Args:
        prompt (str): The prompt to pass to the CSRF agent. Include what previous agents have tried and possible areas of attack. 
    """
    agent_results = await run_agent(
        csrf_agent, prompt, ctx.context.initial_prompt, ctx.context.model
    )

    summary = await Runner.run(
        summarizer_agent.clone(model=ctx.context.model), agent_results
    )
    print(f"CSRF agent summary: {summary}")
    return summary


@function_tool
async def call_xss_agent(ctx: RunContextWrapper[AgentDeps], prompt: str) -> str:
    """
    Calls the XSS agent with the given prompt. The XSS agent will attempt to hack the website using XSS attacks. In the prompt, include what previous agents have tried and what they found. Also, include some promising areas to focus on. 

    Returns a summary of the XSS agent's results.   
    Args:
        prompt (str): The prompt to pass to the XSS agent. Include what previous agents have tried and possible areas of attack. 
    """
    agent_results = await run_agent(
        xss_agent, prompt, ctx.context.initial_prompt, ctx.context.model
    )

    summary = await Runner.run(
        summarizer_agent.clone(model=ctx.context.model), agent_results
    )
    print(f"XSS agent summary: {summary}")
    return summary

@function_tool
async def call_ssti_agent(ctx: RunContextWrapper[AgentDeps], prompt: str) -> str:
    """
    Calls the SSTI agent with the given prompt. The SSTI agent will attempt to hack the website using SSTI attacks. In the prompt, include what previous agents have tried and what they found. Also, include some promising areas to focus on. 

    Returns a summary of the SSTI agent's results.   
    Args:
        prompt (str): The prompt to pass to the SSTI agent. Include what previous agents have tried and possible areas of attack. 
    """
    agent_results = await run_agent(
        ssti_agent, prompt, ctx.context.initial_prompt, ctx.context.model
    )

    summary = await Runner.run(
        summarizer_agent.clone(model=ctx.context.model), agent_results
    )
    print(f"SSTI agent summary: {summary}") 
    return summary

@function_tool
async def call_sql_agent(ctx: RunContextWrapper[AgentDeps], prompt: str) -> str:
    """
    Calls the SQL agent with the given prompt. The SQL agent will attempt to hack the website using SQL injection attacks. In the prompt, include what previous agents have tried and what they found. Also, include some promising areas to focus on. 

    Returns a summary of the SQL agent's results.   
    Args:
        prompt (str): The prompt to pass to the SQL agent. Include what previous agents have tried and possible areas of attack. 
    """
    agent_results = await run_agent(
        sql_agent, prompt, ctx.context.initial_prompt, ctx.context.model
    )

    summary = await Runner.run(
        summarizer_agent.clone(model=ctx.context.model), agent_results
    )
    print(f"SQL agent summary: {summary}")
    return summary


@function_tool
async def call_zap_agent(ctx: RunContextWrapper[AgentDeps], prompt: str) -> str:
    """
    Calls the ZAP agent with the given prompt. The ZAP agent will use the OWASP ZAP
    scanner to perform automated reconnaissance and vulnerability scanning of the
    target web application. In the prompt, include what previous agents have tried
    and what they found, along with any high‑value URLs or flows to prioritize.

    Returns a summary of the ZAP agent's results.
    Args:
        prompt (str): The prompt to pass to the ZAP agent. Include what previous
            agents have tried and possible areas of attack or URLs to scan.
    """
    agent_results = await run_agent(
        zap_agent, prompt, ctx.context.initial_prompt, ctx.context.model
    )

    summary = await Runner.run(
        summarizer_agent.clone(model=ctx.context.model), agent_results
    )
    print(f"ZAP agent summary: {summary}")
    return summary

