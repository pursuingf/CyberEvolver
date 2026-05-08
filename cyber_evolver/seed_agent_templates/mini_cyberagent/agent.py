import re
import os
import time
from typing import List, Dict,Any
import logging
from dataclasses import dataclass, field
from langchain_openai import ChatOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from jinja2 import Template
import subprocess
from jinja2.filters import FILTERS
import shlex
from benchmark_scorers import benchmark_scorer_registry

def regex_search(value, pattern):
    if not value: return None
    match = re.search(pattern, str(value), re.DOTALL)
    return match.group(0) if match else None

FILTERS['regex_search'] = regex_search

@dataclass
class PromptTemplates:
    system_prompt_template: str
    instance_prompt_template: str
    observation_template: str
    output_parse_error_template: str

class Agent:
    def __init__(self,
                 chal_data: Dict[str, Any],
                 cmd_docs: str,
                 skill_descriptions:str,
                 prompt_templates: PromptTemplates,
                 llm: ChatOpenAI,
                 env,
                 logger: logging.Logger = None
                 ):
        self.start_time = time.time()

        self.prompt_templates = prompt_templates
        self.llm = llm
        self.env = env

        self.memory: List[Dict[str, str]] = []
        self.cwd = chal_data["workspace"]
        self.chal_data = chal_data

        self.logger = logger or logging.getLogger("Agent")

        self.on_step_callback = None
        self.token_num = 0

        system_prompt = Template(self.prompt_templates.system_prompt_template).render(
            cmd_docs=cmd_docs,
            command_docs=cmd_docs,
            skill_descriptions=skill_descriptions,
        )
        self.memory.append({
            "role": "system",
            "content": system_prompt
        })
        self.logger.info(f"System Prompt: \n{system_prompt}")

        instance_prompt = Template(self.prompt_templates.instance_prompt_template).render(instance_data=chal_data)
        self.memory.append({
            "role": "user",
            "content": instance_prompt
        })
        self.logger.info(f"Instance prompt: \n{instance_prompt}")

    def forward(self) -> str:
        """Call the LLM with the current memory."""
        response = self.llm.invoke(self.memory)
        if response and response.content:
            self.token_num = response.usage_metadata['total_tokens']
            return response.content
        else:
            raise ValueError("LLM response is blank.")


    def _parse_action(self, llm_output: str) -> str | None:
        """Extract bash command from LLM output using regex."""
        pattern = r"```bash\n(.*?)\n```"
        matches = re.findall(pattern, llm_output.strip(), re.DOTALL)
        command_blocks = [m.strip() for m in matches if m.strip()]

        pre_block_match = re.search(r"^(.*?)```", llm_output, re.DOTALL)
        pre_block_text = pre_block_match.group(1).strip() if pre_block_match else ""
        has_thought = len(pre_block_text) >= 5

        parse_result = {
            "raw_output": llm_output,
            "success": has_thought and len(command_blocks) == 1,
            "command_blocks": command_blocks,
            "has_thought": has_thought,
            "command_blocks_num": len(command_blocks),
            "first_command_block": command_blocks[0] if command_blocks else None,
        }

        return parse_result


    def step(self, action_cmd: str) -> str:
        """
        Execute the given action command in the environment and return the raw, unprocessed output.
        - Though No persistent shell or session state is maintained between calls, the Filesystem is constant and tmux is available to bridge the gap between stateless calls.
        - Each invocation is isolated (no shared context, history, or environment carry-over beyond explicit exports).
        - The command is pre-pended with a PATH extension to include `{self.cwd}/commands`, enabling use of custom CLI tools.
        """
        exec_result = self.env.execute(f"export PATH=\"$PATH:{self.cwd}/commands\" && " + action_cmd, cwd=self.cwd, timeout=120)
        return {"output": exec_result['output'], "returncode": exec_result['returncode']}

    def run(self, max_steps: int = 20):
        """Main loop to solve the challenge."""
        solved = False
        for step_num in range(max_steps):
            self.current_step = step_num
            self.logger.info(f"--- Step {step_num + 1}/{max_steps} ---")

            llm_response = self.forward()
            self.logger.info(f"Agent Response:\n{llm_response}")
            self.memory.append({"role": "assistant", "content": llm_response})

            parse_result = self._parse_action(llm_response)

            if parse_result["success"]:
                exec_result = self.step(parse_result["first_command_block"])
                observation = Template(self.prompt_templates.observation_template).render(
                    output=exec_result["output"],
                    returncode=exec_result["returncode"],
                    cwd=self.cwd
                )
                if self.on_step_callback:
                    self.on_step_callback(step_num, parse_result["first_command_block"])

                score_result = benchmark_scorer_registry.score_step(
                    action=parse_result["first_command_block"],
                    observation=observation,
                    chal_data=self.chal_data,
                    agent_state={"step_num": step_num, "max_steps": max_steps},
                    env=self.env,
                    logger=self.logger,
                )
                self.logger.info("Benchmark scorer response: %s", score_result)
                score_message = str(score_result.get("message", "") or "").strip()
                if score_message:
                    observation = observation + "\n" + score_message
                if score_result.get("done"):
                    self.logger.info("Benchmark scorer marked challenge solved.")
                    solved = True
            else:
                self.logger.info("Failed to parse valid action — appending error prompt")
                observation = Template(self.prompt_templates.output_parse_error_template).render(parse_result=parse_result)


            self.memory.append({"role": "user", "content": observation})
            self.logger.info(f"Agent Observation: {observation if len(observation)<100000 else f'Output {len(observation)} chars.'+observation[:9999]}")
            if solved:
                break

        if not solved:
            self.logger.warning("Max steps reached without finding the flag.")

        return solved
