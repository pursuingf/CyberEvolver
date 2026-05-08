"""Single-sample agent runner for one (node, challenge, sample_id) tuple."""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from common.agent_runtime.challenge_client import ChallengeClient
from common.agent_runtime.docker_manager import GlobalDockerManager
from common.utils.container_paths import opaque_token
from common.utils.process_pool_guards import close_task_log_handler
from common.utils.runtime_policy import resolve_target_scope
from common.utils.safe_logging import safe_format_exception, safe_log_exception
from common.utils.target_runtime import ChallengeRuntimeCoordinator

from run_evolve.dispatcher_helpers import sync_agent_runtime_network
from run_evolve.lifecycle import finish_challenge_with_logging
from run_evolve.runtime_args import filter_challenge_client_runtime_args


def run_node_task(
    node,
    chal_id: str,
    chal_data: Dict,
    sample_id: int,
    llm: Any,
    max_steps: int = 30,
    docker_manager: Optional[GlobalDockerManager] = None,
    logger_level: int = logging.INFO,
) -> Dict:
    from mini_cyberagent.command import Command
    from mini_cyberagent.skill import Skill
    import tempfile

    start_time = time.time()
    log_file = Path(node.logs_path) / f"{chal_id}_run{sample_id}.log"

    # Logger setup
    logger = logging.getLogger(f"{node.node_id}.{chal_id}.{sample_id}")
    logger.setLevel(logger_level)
    logger.handlers = []
    logger.propagate = False
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(fh)

    success = False
    steps = 0
    token_num = 0
    error_msg = None
    env = docker_manager.allocate_environment(chal_data, chal_id=chal_id) if docker_manager else None
    sample_challenge_client = None

    workspace_token = opaque_token(chal_id)
    workspace_path = f"{docker_manager.run_root}/workspace_{workspace_token}_{uuid.uuid4().hex[:8]}"

    try:
        # Attach stable metadata to LLM (without relying on dynamic node imports)
        task_llm = llm
        if hasattr(llm, "with_meta"):
            task_llm = llm.with_meta(
                {
                    "component": "agent",
                    "chal_id": chal_id,
                    "node_id": node.node_id,
                    "sample_id": sample_id,
                }
            )

        # Load agent code
        AgentClass, PromptTemplatesClass, prompt_templates = node.load_node_resources()

        env.mkdir(workspace_path)
        cache_path = docker_manager.prepare_challenge_cache(chal_id, chal_data, env=env)
        env.hardlink_dir_content(cache_path, workspace_path)

        # Load commands
        cmd_docs = ""
        cmd_dir_container = f"{workspace_path}/commands"
        env.mkdir(cmd_dir_container)
        if Path(node.commands_dir).exists():
            for cmd_file in os.listdir(node.commands_dir):
                if cmd_file in {"__init__.py", "__pycache__", "_"} or cmd_file.startswith("test_"):
                    continue
                if '.' in cmd_file and not (cmd_file.endswith(".py") or cmd_file.endswith(".sh")):
                    continue
                local_cmd_path = Path(node.commands_dir) / cmd_file
                try:
                    cmd_obj = Command(str(local_cmd_path))
                    cmd_docs += cmd_obj.get_prompt_info()
                    env.cp_to_container(str(local_cmd_path), f"{cmd_dir_container}/{cmd_obj.name}")
                    env.execute(f"chmod +x {cmd_dir_container}/{cmd_obj.name}")
                except Exception as e:
                    logger.error(f"Error loading command {cmd_file}: {e}")

        # Load skills
        skills_dir_container = f"{workspace_path}/skills"
        skills_index_data = []
        skill_descriptions = ""
        if Path(node.skills_dir).exists():
            env.cp_to_container(str(node.skills_dir), skills_dir_container)
            env.execute(f"find {skills_dir_container} -path '*/tools/*' -type f -exec chmod +x {{}} +")
            for entry in Path(node.skills_dir).iterdir():
                if not entry.is_dir() or entry.name.startswith('.') or entry.name == "__pycache__":
                    continue
                try:
                    skill = Skill(str(entry))
                    skills_index_data.append(skill.to_index_entry())
                    skill_descriptions += skill.get_prompt_info()
                except Exception as e:
                    logger.warning(f"Skipping invalid skill '{entry.name}': {e}")

            if skills_index_data:
                try:
                    skills_index_data.sort(key=lambda x: x['name'])
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as tmp:
                        json.dump(skills_index_data, tmp, indent=2)
                        tmp_path = tmp.name

                    env.cp_to_container(tmp_path, f"{skills_dir_container}/index.json")
                    os.remove(tmp_path)
                except Exception as e:
                    logger.error(f"Error creating skills/index.json: {e}")

        chal_data_runtime = deepcopy(chal_data)
        chal_data_runtime["workspace"] = workspace_path

        challenge_runtime_args = filter_challenge_client_runtime_args(
            getattr(docker_manager, "default_runtime_args", {}) if docker_manager else {}
        )
        target_scope = resolve_target_scope(chal_data=chal_data_runtime, runtime_args=challenge_runtime_args)
        runtime_coordinator = getattr(env, "runtime_coordinator", None)
        if target_scope == "per_agent":
            base_challenge_client = getattr(runtime_coordinator, "challenge_client", None)
            if base_challenge_client is None:
                raise RuntimeError("target_scope=per_agent requires a runtime CTF manager")
            sample_challenge_client = ChallengeClient(config=deepcopy(base_challenge_client.config), logger=logger)
            if challenge_runtime_args:
                sample_challenge_client.remember_runtime_args(chal_id, challenge_runtime_args)
            sample_runtime_data = sample_challenge_client.get_challenge_data(
                chal_id,
                auto_init=True,
                runtime_args=challenge_runtime_args or None,
            )
            chal_data_runtime["target_status"] = sample_runtime_data.get(
                "target_status",
                chal_data_runtime.get("target_status", ""),
            )
            chal_data_runtime["target_info"] = deepcopy(sample_runtime_data.get("target_info", {}) or {})
            chal_data_runtime["runtime"] = deepcopy(sample_runtime_data.get("runtime", {}) or {})
            runtime_coordinator = ChallengeRuntimeCoordinator(
                challenge_client=sample_challenge_client,
                challenge_id=chal_id,
                logger=logger,
            )
            env.runtime_coordinator = runtime_coordinator

        if runtime_coordinator is not None:
            preflight = runtime_coordinator.ensure_target_available(chal_data_runtime)
            if preflight.recovered:
                logger.info(
                    "Recovered target before agent run for %s (target_changed=%s)",
                    chal_id,
                    preflight.target_changed,
                )
        sync_agent_runtime_network(env, chal_data_runtime)

        prompts = PromptTemplatesClass(
            system_prompt_template=prompt_templates.get("system_template.txt", ""),
            instance_prompt_template=prompt_templates.get("instance_template.txt", ""),
            observation_template=prompt_templates.get("observation_template.txt", ""),
            output_parse_error_template=prompt_templates.get("output_parse_error_template.txt", "")
        )

        agent = AgentClass(
            chal_data=chal_data_runtime,
            cmd_docs=cmd_docs,
            skill_descriptions=skill_descriptions,
            prompt_templates=prompts,
            llm=task_llm,
            env=env,
            logger=logger
        )

        success = agent.run(max_steps=max_steps)
        steps = agent.current_step + 1
        if hasattr(task_llm, "totals"):
            token_num = int(getattr(task_llm, "totals").total_tokens)
        else:
            token_num = agent.token_num

    except Exception as e:
        safe_log_exception(logger, "Run failed", exc=e)
        error_msg = safe_format_exception(e)
    finally:
        if docker_manager and workspace_path:
            try:
                env.execute(
                    f'if [ -z "$(ls -d /tmp/finished_* 2>/dev/null)" ]; then '
                    f'mv "{workspace_path}" "/tmp/finished_{workspace_path[-8:]}"; '
                    f'else rm -rf "{workspace_path}"; fi'
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass
        if docker_manager and env is not None:
            try:
                docker_manager.release_environment(env)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass
        if sample_challenge_client is not None:
            try:
                finish_challenge_with_logging(
                    challenge_client=sample_challenge_client,
                    chal_id=chal_id,
                    logger=logger,
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass
            try:
                sample_challenge_client.close()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass

        duration = time.time() - start_time
        logger.info(f"Finished. Success: {success}, Steps: {steps}, Duration: {duration:.2f}s, Tokens: {token_num} {'Traceback:'+error_msg if error_msg else '' }")
        close_task_log_handler(logger, fh)

    return {
        "node_id": node.node_id,
        "chal_id": chal_id,
        "success": success,
        "steps": steps,
        "token_num": token_num,
        "duration": duration,
        "error": error_msg
    }
