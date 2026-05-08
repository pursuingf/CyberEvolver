from pathlib import Path
import asyncio

from ttt_discover import Environment, BaseRewardEvaluator, State, DiscoverConfig, discover

from examples.gpu_mode.lib.libkernelbot.consts import ModalGPU, SubmissionMode
from examples.gpu_mode.lib.libkernelbot.launchers import ModalLauncher
from examples.gpu_mode.lib.libkernelbot.report import RunProgressReporter
from examples.gpu_mode.lib.libkernelbot.run_eval import FullResult
from examples.gpu_mode.lib.libkernelbot.task import (
    LeaderboardTask,
    build_task_config,
    make_task_definition,
)
from examples.gpu_mode.lib.libkernelbot.submission import compute_score
from examples.gpu_mode.prompt import (
    TRIMUL_PROMPT,
    MLA_DECODE_PROMPT,
    MLA_DECODE_PROMPT_END,
)


class SimpleReporter(RunProgressReporter):
    """Minimal reporter that prints to console."""

    async def _update_message(self):
        print(f"[{self.title}]")
        for line in self.lines:
            print(f"  {line}")

    async def display_report(self, title: str, report):
        print(f"\n=== {title} ===")
        print(f"Report has {len(report.data)} items")


def load_task(task_name: str = "trimul") -> LeaderboardTask:
    """Load a LeaderboardTask from its YAML definition."""
    PROJECT_ROOT = Path(__file__).resolve().parent
    task_map = {
        "trimul": PROJECT_ROOT / "lib" / "bioml" / "trimul" / "task.yml",
        "mla_decode_nvidia": PROJECT_ROOT / "lib" / "mla-decode" / "task.yml",
    }
    task_yaml = task_map[task_name]
    definition = make_task_definition(task_yaml)
    return definition.task


async def run_on_modal(
    submission_code: str,
    gpu_type: str = "H100",
    mode: str = "leaderboard",
    task_name: str = "trimul",
    app_name: str = "discord-bot-runner",
) -> tuple[FullResult, LeaderboardTask]:
    """
    Run a submission on Modal using the official task definition.

    Args:
        submission_code: Contents of the user's `submission.py`
        gpu_type: One of ModalGPU names (A100, H100, B200)
        mode: One of: test, benchmark, leaderboard, profile, private
        task_name: One of "trimul" or "mla_decode_nvidia"
    """
    task = load_task(task_name)
    try:
        mode_enum = SubmissionMode(mode)
    except ValueError as e:
        valid = ", ".join(m.value for m in SubmissionMode)
        raise ValueError(f"Invalid mode '{mode}'. Valid modes: {valid}") from e

    config = build_task_config(
        task=task,
        submission_content=submission_code,
        arch=None,
        mode=mode_enum,
    )

    launcher = ModalLauncher(add_include_dirs=[], app_name=app_name)
    gpu_enum = ModalGPU[gpu_type.upper()]

    task_display_name = task_name.capitalize()
    reporter = SimpleReporter(f"{task_display_name} on {gpu_enum.name} (Modal)")
    print(
        f"Submitting {task_display_name} task to Modal on {gpu_enum.name} with mode='{mode_enum.value}'..."
    )

    result = await launcher.run_submission(config, gpu_enum, reporter)
    return result, task


def get_gpu_mode_error(msg: str) -> dict:
    return {
        "reward": 0.0,
        "msg": msg,
        "correctness": 0.0,
        "raw_score": -1_000_000,
        "result_construction": [],
        "stdout": "",
    }


class GpuModeRewardEvaluator(BaseRewardEvaluator):

    def __init__(self, *args, **kwargs):
        self.problem_type = kwargs.get("problem_type")
        self.log_dir = kwargs.get("log_dir")
        if self.problem_type == "trimul":
            self.score_scale = 1500
            self.gpu_type = "H100"
            self.task_name = "trimul"
            self.app_name = "discord-bot-runner"
        elif self.problem_type == "mla_decode_nvidia":
            self.score_scale = 5000
            self.gpu_type = "H200"
            self.task_name = "mla_decode_nvidia"
            self.app_name = "discord-bot-runner-mla-decode-nvidia"
        else:
            raise ValueError(f"Unknown problem_type: {self.problem_type}")

    def get_reward(self, code: str, state: State) -> dict:
        # Prevent no triton kernel code
        if "@triton.jit" not in code:
            return get_gpu_mode_error("Code must contain @triton.jit.")
        # Prevent identity kernel for trimul
        if self.problem_type == "trimul" and "identity" in code:
            return get_gpu_mode_error("Identity kernel is not allowed.")
        
        result, task = asyncio.run(
            run_on_modal(
                submission_code=code,
                gpu_type=self.gpu_type,
                mode="leaderboard",
                task_name=self.task_name,
                app_name=self.app_name,
            )
        )
        if not result.success:
            return get_gpu_mode_error(f"Error: Failed to run test: {result.error}.")
        if "test" not in result.runs:
            return get_gpu_mode_error("Unexpected result: Failed to find test results.")
        test_results = result.runs["test"]
        if not test_results.run.success:
            return get_gpu_mode_error(f"Failed to run tests: {test_results.run.stderr}")
        if not test_results.run.passed:
            return get_gpu_mode_error("Failed to pass test cases.")
        if task is None or "leaderboard" not in result.runs:
            return get_gpu_mode_error("No leaderboard run in result.")
        try:
            score_seconds = compute_score(result, task, submission_id=-1)
            score_us = score_seconds * 1_000_000
            msg = (
                f"\nOverall leaderboard score (microseconds, {task.ranking_by.value}): "
                f"{score_us} us"
            )
            reward = self.score_scale / score_us
            return {
                "reward": float(reward),
                "msg": msg,
                "correctness": 1.0,
                "raw_score": float(score_us),
                "result_construction": [],
                "stdout": "",
            }
        except Exception as e:
            return get_gpu_mode_error(f"Could not compute leaderboard score: {e}")


class GpuModeEnv(Environment):
    reward_function = GpuModeRewardEvaluator
    state_type = State

    @classmethod
    def create_initial_state(cls, problem_type: str) -> State:
        if problem_type == "mla_decode_nvidia":
            from examples.gpu_mode.prompt import MLA_DECODE_INITIAL_STATE, MLA_DECODE_INITIAL_VALUE
            return State(timestep=-1, code=MLA_DECODE_INITIAL_STATE, value=MLA_DECODE_INITIAL_VALUE, construction=None)
        if problem_type == "trimul":
            return State(timestep=-1, code="", value=-1_000_000, construction=None)
        raise ValueError(f"Unknown problem_type: {problem_type}")

    def _should_keep_code_separators(self) -> bool:
        return False
    
    def is_maximize(self) -> bool:
        return False

    def get_question(self) -> str:
        """Build prompt from template, injecting previous code from state."""
        state = self.initial_state
        target = 1000 if self.problem_type == "trimul" else 1700

        state_ctx = state.to_prompt(target, metric_name="runtime (microseconds)", maximize=False, language="python")

        if self.problem_type == "trimul":
            return f"""{TRIMUL_PROMPT}

{state_ctx}

Rules:
- The tensors arguments passed in will be already on your cuda device.
- Define all of your code in one final ```python ``` block.
- We will test the correctness of your kernel on multiple input shapes, make sure to support different potential test cases.
- You are allowed to use mixed precision computations, but make sure your final output is in float32.
- You must use trition 3.3.1 and these kernels will be run on an H100.
- You do not have to implement everything in triton, you may choose to have some of the operations done in pytorch. However, you must implement at least part of the operations in a kernel.
- Include a short docstring at the top summarizing your algorithm.
"""

        if self.problem_type == "mla_decode_nvidia":
            
            return f"""{MLA_DECODE_PROMPT}

{state_ctx}

{MLA_DECODE_PROMPT_END}
"""

        raise ValueError(
            f"Unknown problem_type: {self.problem_type}. "
            "Must be 'trimul' or 'mla_decode_nvidia'"
        )


def discover_gpu_mode(problem_type: str):
    config = DiscoverConfig(
        env_type=GpuModeEnv,
        problem_type=problem_type,
        eval_timeout=530,
        experiment_name=f"test-gpu-mode-{problem_type}-run",
        wandb_project="gpu-mode",
    )
    discover(config)


if __name__ == "__main__":
    discover_gpu_mode("trimul")
    # discover_gpu_mode("mla_decode_nvidia")