# Seed cyber agent for HGM self-improvement.
# Adapted from HGM's coding_agent.py — same AgenticSystem pattern.

import argparse
import json
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from time import time

from llm_withtools import OPENAI_MODEL, chat_with_agent, convert_msg_history

# Thread-local storage for logger instances
thread_local = threading.local()


def get_thread_logger():
    return getattr(thread_local, "logger", None)


def set_thread_logger(logger):
    thread_local.logger = logger


def setup_logger(log_file="./chat_history.md", level=logging.INFO):
    logger = logging.getLogger(f"CyberAgent-{threading.get_ident()}")
    logger.setLevel(level)
    logger.handlers = []
    file_formatter = logging.Formatter("%(message)s")
    os.makedirs(os.path.dirname(log_file) if os.path.dirname(log_file) else ".", exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    set_thread_logger(logger)
    return logger


def safe_log(message, level=logging.INFO):
    logger = get_thread_logger()
    if logger:
        logger.log(level, message)
    else:
        print(f"Warning: No logger found for thread {threading.get_ident()}")


class AgenticSystem:
    def __init__(
        self,
        chat_history_file="./chat_history.md",
        model=OPENAI_MODEL,
        scoring_url="",
    ):
        self.chat_history_file = chat_history_file
        self.code_model = model

        # Set scoring URL as env var so check_done tool can read it
        os.environ["SCORING_URL"] = scoring_url

        self.logger = setup_logger(chat_history_file)
        with open(chat_history_file, "w") as f:
            f.write("")

    def forward(self, timeout=1800, max_steps=30):
        timeout -= 60
        start_time = time()

        # prompts/system_prompt.py is rendered by harness with benchmark-specific content.
        # Self-improvement can modify this file directly to change prompts/strategy.
        from prompts.system_prompt import SYSTEM_PROMPT, INSTANCE_PROMPT

        instruction = SYSTEM_PROMPT + "\n\n" + INSTANCE_PROMPT

        chat_history, n_llm_calls_used, solved = chat_with_agent(
            instruction,
            model=self.code_model,
            msg_history=[],
            logging=safe_log,
            max_llm_calls=max_steps,
            timeout=timeout - (time() - start_time),
        )
        self.solved = solved
        return chat_history


def main():
    parser = argparse.ArgumentParser(description="Cyber security agent for HGM.")
    parser.add_argument("--chat_history_file", default="./agent.md")
    parser.add_argument("--model", default=OPENAI_MODEL)
    parser.add_argument("--scoring_url", default="")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--step_limit", type=int, default=30)
    parser.add_argument("--outdir", default="./")
    # HGM self-improvement compatibility
    parser.add_argument("--problem_statement", default=None)
    parser.add_argument("--git_dir", default=None)
    parser.add_argument("--base_commit", default=None)
    parser.add_argument("--test_description", default=None)
    parser.add_argument("--self_improve", action="store_true", default=False)
    parser.add_argument("--instance_id", default=None)
    args = parser.parse_args()

    if args.self_improve:
        from coding_agent import AgenticSystem as SWEAgenticSystem
        agentic_system = SWEAgenticSystem(
            problem_statement=args.problem_statement,
            git_tempdir=args.git_dir,
            base_commit=args.base_commit,
            chat_history_file=args.chat_history_file,
            test_description=args.test_description,
            self_improve=True,
            instance_id=args.instance_id,
            model=args.model,
        )
        agentic_system.forward(args.timeout)
        from utils.git_utils import diff_versus_commit
        model_patch = diff_versus_commit(args.git_dir, args.base_commit)
        outfile = os.path.join(args.outdir, "model_patch.diff") if args.outdir else "model_patch.diff"
        with open(outfile, "w") as f:
            f.write(model_patch)
        return

    # Normal mode: prompts/system_prompt.py has been rendered by harness
    agent = AgenticSystem(
        chat_history_file=args.chat_history_file,
        model=args.model,
        scoring_url=args.scoring_url,
    )

    chat_history = agent.forward(timeout=args.timeout, max_steps=args.step_limit)
    solved = agent.solved

    from llm import get_token_usage
    tokens = get_token_usage()

    result = {
        "solved": solved,
        "steps": len(chat_history) if chat_history else 0,
        "prompt_tokens": tokens["prompt_tokens"],
        "completion_tokens": tokens["completion_tokens"],
    }
    result_path = os.path.join(args.outdir, "result.json") if args.outdir else "result.json"
    with open(result_path, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
