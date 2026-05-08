import re
import time
from typing import List, Dict,Any
import logging
from dataclasses import dataclass, field
from langchain_openai import ChatOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from jinja2 import Template
from common.utils.agent_utils import output_wrapper
from mini_cyberagent.benchmark_scorers import benchmark_scorer_registry

@dataclass
class PromptTemplates:
    system_prompt_template: str
    instance_prompt_template: str
    observation_template: str
    output_parse_error_template: str


@dataclass
class AgentConfig:
    """Tunables for the agent loop, modelled after ``minisweagent.AgentConfig``.

    The historical __init__ takes individual kwargs; AgentConfig groups the
    knobs that aren't bound to a specific challenge (limits, budgets) so they
    can be passed/serialised as a unit.
    """
    max_steps: int = 20
    max_time: int = 3600
    max_token_budget: int = 32768


class Agent:
    def __init__(self,
                 chal_data: Dict[str, Any],
                 cmd_docs: str,
                 skill_descriptions: str,
                 prompt_templates: PromptTemplates,
                 llm: ChatOpenAI,
                 env,
                 logger: logging.Logger = None,
                 max_token_budget: int = 32768,
                 ):
        self.start_time = time.time()
        self.logger = logger or logging.getLogger("Agent")

        self.prompt_templates = prompt_templates
        self.llm = llm
        self.env = env
        self.max_token_budget = max_token_budget

        self.memory: List[Dict[str, str]] = []
        
        self.cwd = chal_data["workspace"] # /ctf/{chal_id}_uuid
        self.chal_data = chal_data

        
        self.on_step_callback = None
        self.token_num = 0

        prompt_context = self._build_prompt_context(
            cmd_docs=cmd_docs,
            skill_descriptions=skill_descriptions,
        )

        system_prompt = Template(self.prompt_templates.system_prompt_template).render(**prompt_context)
        self.memory.append({
            "role": "system", 
            "content": system_prompt
        })
        self.logger.info(f"System Prompt: \n{system_prompt}")

        instance_prompt = Template(self.prompt_templates.instance_prompt_template).render(**prompt_context)
        self.memory.append({
            "role": "user", 
            "content": instance_prompt
        })
        self.logger.info(f"Instance prompt: \n{instance_prompt}")
    
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(min=1, max=20)
    )
    def forward(self) -> str:
        """Call the LLM with the current memory."""
        try:
            response = self.llm.invoke(self.memory)
            self.token_num = response.usage_metadata['total_tokens']
            return response.content
        except Exception as e:
            # Handle context length exceeded or API errors here if needed
            raise e

    def _parse_action(self, llm_output: str) -> str | None:
        """Extract bash command from LLM output using regex."""
        pattern = r"```(?:bash)?\n(.*?)\n```"
        matches = re.findall(pattern, llm_output.strip(), re.DOTALL)
        command_blocks = [m.strip() for m in matches if m.strip()]

        pre_block_match = re.search(r"^(.*?)```", llm_output, re.DOTALL)
        pre_block_text = pre_block_match.group(1).strip() if pre_block_match else ""
        has_thought = len(pre_block_text) >= 5

        # Build result dict
        result = {
            "raw_output": llm_output,
            "success": has_thought and len(command_blocks) == 1,
            "command_blocks": command_blocks,
            "has_thought": has_thought,
            "num_commands": len(command_blocks),
            "command": command_blocks[0] if command_blocks else None,
        }

        return result

    def step(self, action_cmd: str) -> str:
        """
        Execute the given action command in the environment and return the raw, unprocessed output.
        
        The environment's `execute` method runs the command via `subprocess` in a **non-interactive** mode:
        - No persistent shell or session state is maintained between calls.
        - Each invocation is isolated (no shared context, history, or environment carry-over beyond explicit exports).
        - The command is pre-pended with a PATH extension to include `self.cwd}/commands`, enabling use of custom CLI tools.
        """  
        exec_result = self.env.agent_execute(
            f"export PATH=\"$PATH:{self.cwd}/commands\" && " + action_cmd,
            cwd=self.cwd,
            timeout=200,
            runtime_context=self.chal_data,
        )
        output = exec_result['output']
        if output.startswith("[SYSTEM]"):
            return output
        output = output_wrapper(action_cmd, exec_result['output'])
        return output
       
    def run(self, max_steps: int = 20, max_time: int = 3600):
        """Main loop to solve the challenge."""
        solved = False
        for step_num in range(max_steps):
            self.current_step = step_num
            self.logger.info(f"--- Step {step_num + 1}/{max_steps} ---")

            llm_response = self.forward()
            self.logger.info(f"Agent Thought:\n{llm_response}")
            self.memory.append({"role": "assistant", "content": llm_response})
            
            parse_result = self._parse_action(llm_response)
            self.logger.info(f"Agent Action:\n{parse_result}")
            observation = {}
            if parse_result["success"]:
                self.logger.info(f"Agent Executing: {parse_result['command']}")                
                raw_output = self.step(parse_result["command"])
                observation["output"] = raw_output
                if self.on_step_callback:
                    self.on_step_callback(step_num, parse_result["command"])

                budget_info = {
                    "used_tokens": self.token_num,
                    "max_tokens": self.max_token_budget,
                    "remaining_tokens": max(0, self.max_token_budget - self.token_num),
                    "current_step": step_num + 1,
                    "max_steps": max_steps,
                    "remaining_steps": max_steps - (step_num + 1),
                    "used_time": int(time.time() - self.start_time),
                    "max_time": max_time,
                    "remaining_time": max_time - int(time.time() - self.start_time)
                } 
            
                observation["budget_info"] = budget_info
                observation = Template(self.prompt_templates.observation_template).render(output=observation, cwd=self.cwd)
                score_result = benchmark_scorer_registry.score_step(
                    action=parse_result["command"],
                    observation=observation,
                    chal_data=self.chal_data,
                    agent_state={"step_num": step_num, "max_steps": max_steps},
                    env=self.env,
                )
                score_message = str(score_result.get("message", "") or "").strip()
                if score_message:
                    observation = observation + "\n" + score_message
                if score_result.get("done"):
                    self.logger.info("Benchmark scorer marked challenge solved.")
                    solved = True
            else:
                self.logger.info("parse action error")
                observation = Template(self.prompt_templates.output_parse_error_template).render(parse_result=parse_result)

            #  Update Memory           
            self.memory.append({"role": "user", "content": observation})
            self.logger.info(f"Agent Observation: {observation}")
            if solved:
                break

        if not solved:
            self.logger.warning("Max steps reached without finding the flag.")

        return solved

    def _build_prompt_context(self, *, cmd_docs: str, skill_descriptions: str) -> Dict[str, Any]:
        task_profile = self.chal_data.get("task_profile", "ctf_local")
        files = list(self.chal_data.get("files", []) or [])
        target_info = self.chal_data.get("target_info", {}) or {}
        primary_task_text, selected_prompt_variant = self._resolve_primary_task_text()

        return {
            "command_docs": cmd_docs,
            "skill_descriptions": skill_descriptions,
            "chal_data": self.chal_data,
            "task_profile": task_profile,
            "challenge_mode_label": (
                "a remote penetration target" if task_profile == "pentest_remote" else "a CTF challenge"
            ),
            "challenge_name": self.chal_data.get("name", "unknown"),
            "challenge_category": self.chal_data.get("category", "unknown"),
            "challenge_points_suffix": self._format_points_suffix(),
            "flag_format": self.chal_data.get("flag_format", "{...}"),
            "primary_task_text": primary_task_text,
            "selected_prompt_variant_line": (
                f"Prompt variant: `{selected_prompt_variant}`" if selected_prompt_variant else ""
            ),
            "available_files_block": self._format_available_files(files),
            "target_endpoints_block": self._format_target_endpoints(target_info),
            "workspace": self.cwd,
            "cwd": self.cwd,
            "instance_data": self.chal_data,
        }

    def _resolve_primary_task_text(self) -> tuple[str, str]:
        prompt_variants = self.chal_data.get("prompt_variants", {}) or {}
        if isinstance(prompt_variants, dict) and prompt_variants:
            default_variant = self.chal_data.get("default_variant")
            if default_variant in prompt_variants:
                return str(prompt_variants[default_variant]), str(default_variant)
            if "zero_day" in prompt_variants:
                return str(prompt_variants["zero_day"]), "zero_day"
            first_variant = next(iter(prompt_variants.items()))
            return str(first_variant[1]), str(first_variant[0])

        return (
            self.chal_data.get("task") or self.chal_data.get("description") or "",
            "",
        )

    def _format_points_suffix(self) -> str:
        points = self.chal_data.get("points")
        if points in (None, ""):
            return ""
        return f" worth {points} points"

    def _format_available_files(self, files: List[str]) -> str:
        if not files:
            return ""
        return "Available local files: " + ", ".join(files)

    def _format_target_endpoints(self, target_info: Dict[str, Dict[str, Any]]) -> str:
        if not target_info:
            return ""

        endpoints = []
        for service_name, info in target_info.items():
            host = info.get("inner_host") or info.get("inner_ip") or info.get("host") or info.get("external_host")
            port = info.get("inner_port")
            if port is None:
                port = info.get("port")
            if port is None:
                port = info.get("external_port")
            if host and port:
                endpoints.append(f"- {service_name}: `{host}:{port}`")

        if not endpoints:
            return ""

        if len(endpoints) == 1:
            return "Reachable endpoint:\n" + endpoints[0]
        return "Reachable endpoints:\n" + "\n".join(endpoints)
