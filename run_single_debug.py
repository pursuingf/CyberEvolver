#!/usr/bin/env python3
"""
Debug script for running a single CTF challenge.

"""

import os
import sys
import json
import yaml
import argparse
import logging
from pathlib import Path
# Add your project root to path if needed
sys.path.insert(0, str(Path(__file__).parent))

from common.utils.util import *
from langchain_openai import ChatOpenAI
from mini_cyberagent.command import Command
from common.agent_runtime.docker_env import DockerEnvironment, DockerEnvironmentConfig
from common.agent_runtime.challenge_client import ChallengeClient, ChallengeClientConfig


def parse_args():
    parser = argparse.ArgumentParser(description="Debug a single CTF challenge.")
    parser.add_argument("--config", "-c", default="mini_cyberagent/configs/mini_ctf.yaml", help="Config YAML path")
    parser.add_argument("--model", "-m", default="", help="Model name in common/configs/model.yml")
    parser.add_argument("--run-id", "-r", default="debug", help="Run ID (used in log path)")
    parser.add_argument("--challenge-id", "-i", required=True, help="Challenge ID, e.g., 'ic-crypto-5'")
    parser.add_argument("--step-limit", "-s", type=int, default=20, help="Max steps for agent (default: 20)")
    parser.add_argument("--open-log", action="store_true", help="Open log file in default editor after run")
    return parser.parse_args()


def setup_root_logger():
    # Simple console logger for top-level setup errors
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )


def main():
    args = parse_args()
    setup_root_logger()
    logger = logging.getLogger("debug")

    print(f"🔍 Loading config: {args.config}")
    with open(args.config, "r") as f:
        config = yaml.safe_load(f)

    with open("common/configs/model.yml", "r") as f:
        model_config = yaml.safe_load(f)

    agent_code_path = config["agent"].get("agent_code")

    config["agent"]["model_kwargs"] |= model_config[args.model]
    print("🔧 Initializing ChallengeClient...")
    client_config = ChallengeClientConfig(**config["challenge_client"])
    challenge_client = ChallengeClient(config=client_config)

    chal_id = args.challenge_id
    if chal_id not in challenge_client.challenges:
        print(f"❌ Challenge ID '{chal_id}' not found. Available IDs:")
        for cid in sorted(challenge_client.challenges):
            meta = challenge_client.challenges[cid]
            print(f"  - {cid} ({meta['benchmark']}/{meta['category']})")
        sys.exit(1)


    try:
        chal_data = challenge_client.get_challenge_data(chal_id)
    except Exception as e:
        print(f"💥 Failed to load/initialize challenge {chal_id}: {e}")
        sys.exit(1)

    # --- Set up per-challenge logger (to file + console) ---
    timestamp = args.run_id  # reuse run_id as timestamp for simplicity
    log_dir = Path(config["logging"]["log_dir"]) / args.run_id / chal_data["benchmark"] / chal_data["category"]
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"run_{chal_id}_{timestamp}.log"

    challenge_logger = logging.getLogger(f"chal.{chal_id}")
    challenge_logger.setLevel(logging.DEBUG)
    challenge_logger.propagate = False
    challenge_logger.handlers.clear()

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    challenge_logger.addHandler(ch)
    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    challenge_logger.addHandler(fh)

    # Both branches bind Agent/PromptTemplates to module globals so the
    # `import_star_from_file(... globals())` path and the explicit `from
    # mini_cyberagent.agent import ...` fallback are interchangeable.
    global Agent, PromptTemplates
    if agent_code_path:
        challenge_logger.info(f"🔁 Importing * from agent code: {agent_code_path}")
        import_star_from_file(agent_code_path, globals())  # inject into global scope of main()
    else:
        challenge_logger.warning("⚠️ No 'agent_code' in config; falling back to default mini_cyberagent.agent")
        from mini_cyberagent.agent import Agent, PromptTemplates
    challenge_logger.info(f"🚀 Starting debug run for challenge: {chal_id}")
    challenge_logger.info(f"Challenge data keys: {list(chal_data.keys())}")
    challenge_logger.info(f"Target status: {chal_data['target_status']}")
    if chal_data["target_status"] == "running":
        challenge_logger.info(f"Target services:\n{json.dumps(chal_data['target_info'], indent=2)}")
    elif chal_data["target_status"] == "stopped":
        print(f"💥 Failed to load/initialize challenge target {chal_id}: {e}")
        sys.exit(1)

    challenge_logger.info(f"📁 Benchmark: {chal_data['benchmark']}, Category: {chal_data['category']}")
    challenge_logger.info(f"📝 Log file: {log_file.resolve()}")

    try:
        # --- LLM ---
        challenge_logger.info("🤖 Initializing LLM...")
        llm = ChatOpenAI(**config["agent"]["model_kwargs"])
        config["docker_environment"]["container_name"] = "ctfenv-debug" 
        # --- Attacker Env ---
        challenge_logger.info("🐳 Setting up attacker Docker environment...")
        docker_config = DockerEnvironmentConfig(**config["docker_environment"])
        attacker_env = DockerEnvironment(config=docker_config, logger=challenge_logger)
        # print(attacker_env.execute("which one_gadget"))
        # exit()
        try:
            # --- Setup workspace ---
            chal_dir_name = attacker_env._prepare_challenge_files(chal_data)
            chal_data["workspace"] = f"/ctf/{chal_dir_name}"
            challenge_logger.info(f'Workspace set to: {chal_data["workspace"]}')

            # --- Install custom commands ---
            cmd_dir = f"{chal_data['workspace']}/commands"
            attacker_env.execute(f"mkdir -p {cmd_dir}")
            cmd_docs = ""
            for cmd_path in config["agent"]["command_files"]:
                cmd = Command(cmd_path)
                cmd_docs += cmd.get_prompt_info()
                attacker_env.cp_to_container(cmd.file_path, f"{cmd_dir}/{cmd.name}")
                attacker_env.execute(f"chmod +x {cmd.name}", cwd=cmd_dir)
            challenge_logger.info("✅ Custom commands installed.")

            # --- Agent ---
            prompt_templates = PromptTemplates(
                system_prompt_template=config["agent"]["system_template"],
                instance_prompt_template=config["agent"]["instance_template"],
                observation_template=config["agent"]["observation_template"],
                output_parse_error_template=config["agent"]["output_parse_error_template"],
            )
            challenge_logger.info(f"🧠 Initializing Agent with chal data:\n {chal_data}")
        
            agent = Agent(
                chal_data=chal_data,
                cmd_docs=cmd_docs,
                skill_descriptions="",
                prompt_templates=prompt_templates,
                llm=llm,
                env=attacker_env,
                logger=challenge_logger,
            )

            # Simple callback for console progress
            def on_step_callback(step: int, command: str):
                short_cmd = (command[:60] + "...") if len(command) > 60 else command
                challenge_logger.info(f"[Step {step}] 🔧 Executing: {short_cmd}")

            agent.on_step_callback = on_step_callback

            # --- RUN! ---
            challenge_logger.info(f"▶️ Starting agent (max_steps={args.step_limit})...")
            success = agent.run(max_steps=args.step_limit)

            challenge_logger.info(f"✅ Challenge finished. Success: {success}")
            if success:
                print(f"\n🎉 SUCCESS! Flag found for {chal_id}")
            else:
                print(f"\n❌ FAILED. See log for details.")

        finally:
            challenge_logger.info("🧹 Cleaning up environments...")
            try:
                attacker_env.cleanup()
            except Exception as e:
                challenge_logger.error(f"Failed to clean attacker env: {e}")
            #try:
            #    challenge_client.cleanup(chal_id)
            #except Exception as e:
            #    challenge_logger.error(f"Failed to clean target env: {e}")

    except Exception as e:
        challenge_logger.exception("💥 CRITICAL: Unhandled exception in debug run")
        print(f"\n💥 CRITICAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Post-run ---
    print(f"\n📄 Full log: {log_file.resolve()}")
    if args.open_log:
        print("🖥 Opening log file...")
        if sys.platform == "darwin":
            os.system(f"open '{log_file}'")
        elif sys.platform == "linux":
            os.system(f"xdg-open '{log_file}'")
        elif sys.platform == "win32":
            os.startfile(str(log_file))


if __name__ == "__main__":
    main()