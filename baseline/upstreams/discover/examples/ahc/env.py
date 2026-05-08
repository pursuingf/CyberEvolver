from examples.ahc.prompt import AHC039_PROMPT, AHC058_PROMPT
from examples.ahc.lib.eval_task import run_ale_bench_task
from ttt_discover import Environment, BaseRewardEvaluator, State, DiscoverConfig, discover


CPUS_PER_TASK = 2


class AhcRewardEvaluator(BaseRewardEvaluator):
    def __init__(self, *args, **kwargs):
        self.problem_type = kwargs.get("problem_type")
        self.log_dir = kwargs.get("log_dir")

    def get_reward(self, code: str, state: State) -> float:

        raw = run_ale_bench_task(
            code,
            problem_id=self.problem_type,
            lite_version=False,
            log_dir=self.log_dir,
            num_cpus_per_task=CPUS_PER_TASK,
        )
        # If lib returned an error dict, pass it through.
        if "case_results" not in raw:
            return raw
        
        case_results = raw["case_results"]
        
        num_public_cases = len(case_results)
        # judge_result is a JudgeResult enum (from lib.result)
        from examples.ahc.lib.result import JudgeResult

        num_accepted = sum(
            1 for case in case_results if getattr(case, "judge_result", None) == JudgeResult.ACCEPTED
        )

        total_score = sum(float(getattr(case_result, "absolute_score", 0.0)) for case_result in case_results)
        raw_score = total_score / num_public_cases if num_public_cases > 0 else 0.0

        # Normalize reward scale
        reward = raw_score / 1500.0
        if self.problem_type == "ahc058":
            reward = reward / 2000.0 # 058 has a different scale

        msg = (
            f"Evaluated on {num_public_cases} public test cases. "
            f"Passed: {num_accepted}/{num_public_cases}. Raw_score: {raw_score:.4f}"
        )

        return {
            "reward": float(reward),
            "msg": msg,
            "correctness": 1.0 if num_accepted == num_public_cases else 0.0,
            "raw_score": float(raw_score),
            "result_construction": [], # No construction
            "stdout": "", # No stdout
        }


class AhcEnv(Environment):
    reward_function = AhcRewardEvaluator
    state_type = State

    @classmethod
    def create_initial_state(cls, problem_type: str) -> State:
        if problem_type == "ahc039":
            from examples.ahc.prompt import AHC039_BEST_CODE, AHC039_BEST_CODE_VALUE
            return State(timestep=-1, code=AHC039_BEST_CODE, value=AHC039_BEST_CODE_VALUE, construction=None)
        if problem_type == "ahc058":
            return State(timestep=-1, code="", value=0.0, construction=None)
        raise ValueError(f"Unknown problem_type: {problem_type}")

    def get_question(self) -> str:
        """Build prompt from template, injecting previous code from state."""
        state = self.initial_state
        assert self.problem_type in {"ahc039", "ahc058"}
        
        if self.problem_type == "ahc058":
            target = 6_500_000
        elif self.problem_type == "ahc039":
            target = 5000
        else:
            raise ValueError(f"Problem ID {self.problem_type} not supported")

        prompt = AHC039_PROMPT if self.problem_type == "ahc039" else AHC058_PROMPT

        state_ctx = state.to_prompt(target, metric_name="performance", maximize=True)

        return f'''{prompt}

{state_ctx}

Rules:
- You must use cpp20 to solve the problem.
- Define all of your code in one final ```cpp ``` block.
- In your final response, you should only output the code of your program. Do not include any other text.

Try diverse approaches to solve the problem. The best solution will make efficient use of the entire 2 second time limit without exceeding it. Think outside the box.
'''

    def _get_code_languages(self) -> list[str]:
        return ["cpp"]
    
    def _should_keep_code_separators(self) -> bool:
        return False  # ALE Bench doesn't keep separators


def discover_ahc039():
    # Explicitly define config for clarity
    # Uses default values for most fields
    config = DiscoverConfig(
        env_type=AhcEnv,
        problem_type="ahc039",
        num_cpus_per_task=CPUS_PER_TASK,
        eval_timeout=530,
        experiment_name="test-ahc039-run",
        wandb_project="ahc-ahc039",
        model_name="openai/gpt-oss-120b",
        renderer_name="gpt_oss_high_reasoning",
        learning_rate=4e-5,
        num_epochs=50,
        temperature=1.0,
        kl_penalty_coef=1e-2,
        phase1_max_tokens=22000,
        groups_per_batch=8,
        group_size=64,
    ) 

    discover(config)


def discover_ahc058():
    # Explicitly define config for clarity
    # Uses default values for most fields
    config = DiscoverConfig(
        env_type=AhcEnv,
        problem_type="ahc058",
        num_cpus_per_task=CPUS_PER_TASK,
        eval_timeout=530,
        experiment_name="test-ahc058-run",
        wandb_project="ahc-ahc058",
        model_name="openai/gpt-oss-120b",
        renderer_name="gpt_oss_high_reasoning",
        learning_rate=2e-5,
        num_epochs=50,
        temperature=1.0,
        kl_penalty_coef=1e-2,
        phase1_max_tokens=25000,
        groups_per_batch=8,
        group_size=64,
    )

    discover(config)


if __name__ == "__main__":
    discover_ahc039()
    # discover_ahc058()