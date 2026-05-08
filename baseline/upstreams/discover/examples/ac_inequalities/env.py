import numpy as np
from types import SimpleNamespace
from typing import Any, Tuple

from ttt_discover import Environment, SandboxRewardEvaluator, State, DiscoverConfig, discover

from examples.ac_inequalities.prompt import AC1_EVAL_FUNCTION, AC1_LITERATURE, AC2_LITERATURE, ae_verifier_program


CPUS_PER_TASK = 2


### VERIFIER HELPERS ###
def evaluate_sequence(sequence: list[float]) -> float:
    """
    Evaluates a sequence of coefficients with enhanced security checks.
    Returns np.inf if the input is invalid.
    """
    # --- Security Checks ---

    # Verify that the input is a list
    if not isinstance(sequence, list):
        return np.inf

    # Reject empty lists
    if not sequence:
        return np.inf

    # Check each element in the list for validity
    for x in sequence:
        # Reject boolean types (as they are a subclass of int) and
        # any other non-integer/non-float types (like strings or complex numbers).
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return np.inf

        # Reject Not-a-Number (NaN) and infinity values.
        if np.isnan(x) or np.isinf(x):
            return np.inf

    # Convert all elements to float for consistency
    sequence = [float(x) for x in sequence]

    # Protect against negative numbers
    sequence = [max(0, x) for x in sequence]

    # Protect against numbers that are too large
    sequence = [min(1000.0, x) for x in sequence]

    n = len(sequence)
    b_sequence = np.convolve(sequence, sequence)
    max_b = max(b_sequence)
    sum_a = np.sum(sequence)

    # Protect against the case where the sum is too close to zero
    if sum_a < 0.01:
        return np.inf

    return float(2 * n * max_b / (sum_a**2))


# IMPORTANT:
# We want the injected sandbox helper to be named `evaluate_sequence` (this is what
# the prompt instructs the model-generated code to call), but we also want to keep
# two distinct implementations for AC1 vs AC2. We achieve this by defining two
# separate functions whose *source* both say `def evaluate_sequence(...)`, and we
# keep references to them under distinct names.
evaluate_sequence_ac1 = evaluate_sequence


def evaluate_sequence(sequence: list[float]) -> float:
    # Verify that the input is a list
    if not isinstance(sequence, list):
        raise ValueError("Invalid sequence type")

    # Reject empty lists
    if not sequence:
        raise ValueError("Empty sequence")

    # Check each element in the list for validity
    for x in sequence:
        # Reject boolean types (as they are a subclass of int) and
        # any other non-integer/non-float types (like strings or complex numbers).
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            raise ValueError("Invalid sequence element type")

        # Reject Not-a-Number (NaN) and infinity values.
        if np.isnan(x) or np.isinf(x):
            raise ValueError("Invalid sequence element value")

    # Convert all elements to float for consistency
    sequence = [float(x) for x in sequence]

    # Protect against negative numbers
    sequence = [max(0, x) for x in sequence]

    # Check if sum of sequence will be too close to zero
    if np.sum(sequence) < 0.01:
        raise ValueError("Sum of sequence is too close to zero.")
    
    # Protect against numbers that are too large
    sequence = [min(1000.0, x) for x in sequence]

    convolution_2 = np.convolve(sequence, sequence)
    # --- Security Checks ---

    # Calculate the 2-norm squared: ||f*f||_2^2
    num_points = len(convolution_2)
    x_points = np.linspace(-0.5, 0.5, num_points + 2)
    x_intervals = np.diff(x_points) # Width of each interval
    y_points = np.concatenate(([0], convolution_2, [0]))
    l2_norm_squared = 0.0
    for i in range(len(convolution_2) + 1):  # Iterate through intervals
        y1 = y_points[i]
        y2 = y_points[i+1]
        h = x_intervals[i]
        # Integral of (mx + c)^2 = h/3 * (y1^2 + y1*y2 + y2^2) where m = (y2-y1)/h, c = y1 - m*x1, interval is [x1, x2], y1 = mx1+c, y2=mx2+c
        interval_l2_squared = (h / 3) * (y1**2 + y1 * y2 + y2**2)
        l2_norm_squared += interval_l2_squared

    # Calculate the 1-norm: ||f*f||_1
    norm_1 = np.sum(np.abs(convolution_2)) / (len(convolution_2) + 1)

    # Calculate the infinity-norm: ||f*f||_inf
    norm_inf = np.max(np.abs(convolution_2))
    C_lower_bound = l2_norm_squared / (norm_1 * norm_inf)
    return C_lower_bound


evaluate_sequence_ac2 = evaluate_sequence


def verify_ac1_solution(result: list[float]) -> bool:
    try:
        value = evaluate_sequence_ac1(result)
        if value == np.inf:
            return False
    except Exception:
        return False
    return True


def verify_ac2_solution(result: list[float]) -> bool:
    try:
        value = evaluate_sequence_ac2(result)
        if value == np.inf:
            return False
    except Exception:
        return False
    return True


### REWARD EVALUATOR ###
class ACInequalitiesRewardEvaluator(SandboxRewardEvaluator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.problem_type == "ac1":
            self.verifier_src = evaluate_sequence_ac1
        elif self.problem_type == "ac2":
            self.verifier_src = evaluate_sequence_ac2
        else:
            raise ValueError(
                f"Unknown problem_type: {self.problem_type}. Must be 'ac1' or 'ac2'"
            )

    def get_program_entrypoint(self) -> str:
        if self.problem_type == "ac1":
            return "propose_candidate"
        elif self.problem_type == "ac2":
            return "construct_function"
        else:
            raise ValueError(f"Unknown problem_type: {self.problem_type}. Must be 'ac1' or 'ac2'")

    def get_reward(self, code: str, state: State) -> float:
        output, error_msg = self.execute_code(code, state)
        if error_msg: 
            return self._get_failure_entry(error_msg)

        # NOTE: Be careful with conditional-expression precedence.
        # We want to reject invalid solutions for *both* AC1 and AC2.
        is_valid = verify_ac1_solution(output) if self.problem_type == "ac1" else verify_ac2_solution(output)
        if not is_valid:
            return self._get_failure_entry("Invalid solution.")

        if self.problem_type == "ac1":
            result = evaluate_sequence_ac1(output)
            reward = 1.0 / (1e-8 + result)
        else:
            result = evaluate_sequence_ac2(output)
            reward = result

        return {
            "reward": reward,
            "msg": f"Success; raw_score={result}",
            "correctness": 1.0,
            "raw_score": result,
            "result_construction": output,
            "stdout": getattr(self, '_last_stdout', ''),
        }


### ENVIRONMENT ###
class AutoCorrInequalityEnv(Environment):
    reward_function = ACInequalitiesRewardEvaluator
    state_type = State
    construction_length_limits = (1000, 100000) # For sampler, unique for ac

    @classmethod
    def create_initial_state(cls, problem_type: str) -> State:
        rng = np.random.default_rng(12345)
        construction = [rng.random()] * rng.integers(1000, 8000)
        if problem_type == "ac1":
            from examples.ac_inequalities.env import evaluate_sequence_ac1
            from examples.ac_inequalities.prompt import example_ae_program_random_init
            initial_value = -evaluate_sequence_ac1(construction)
            code = "```python\n" + example_ae_program_random_init(1000) + "\n```"
            return State(timestep=-1, construction=construction, code=code, value=initial_value)
        elif problem_type == "ac2":
            from examples.ac_inequalities.env import evaluate_sequence_ac2
            from examples.ac_inequalities.prompt import thetaevolve_initial_program_prev_init
            initial_value = evaluate_sequence_ac2(construction)
            code = "```python\n" + thetaevolve_initial_program_prev_init + "\n```"
            return State(timestep=-1, construction=construction, code=code, value=initial_value)
        raise ValueError(f"Unknown problem_type: {problem_type}")

    def is_maximize(self) -> bool:
        if self.problem_type == "ac1":
            return False # Minimize upper bound
        elif self.problem_type == "ac2":
            return True # Maximize lower bound
        else:
            raise ValueError(f"Unknown problem_type: {self.problem_type}. Must be 'ac1' or 'ac2'")

    def get_question(self) -> str:
        """Build prompt from template, injecting previous code from state."""
        state = self.initial_state

        budget_s = 1000

        if self.problem_type == "ac1":
            metric_name = "upper bound"
            target = 1.5030
            is_maximize = False
        elif self.problem_type == "ac2":
            metric_name = "lower bound"
            target = 0.97
            is_maximize = True
        else:
            raise ValueError(f"Unknown problem_type: {self.problem_type}. Must be 'ac1' or 'ac2'")

        state_ctx = self.initial_state.to_prompt(target, metric_name=metric_name, maximize=is_maximize)

        if state.construction:
            state_ctx += f"\nLength of the construction: {len(state.construction)}"

        if self.problem_type == "ac1":
            return f'''Act as an expert software developer and inequality specialist specializing in creating step functions with certain properties.

Your task is to generate the sequence of non-negative heights of a step function, that minimizes the following evaluation function:

{AC1_EVAL_FUNCTION}

{AC1_LITERATURE}

Your task is to write a search function that searches for the best sequence of coefficients. Your function will have {budget_s} seconds to run, and after that it has to have returned the best sequence it found. If after {budget_s} seconds it has not returned anything, it will be terminated with negative infinity points. All numbers in your sequence have to be positive or zero. Larger sequences with {budget_s}s of items often have better attack surface, but too large sequences with 100s of thousands of items may be too slow to search.

You may code up any search method you want, and you are allowed to call the evaluate_sequence() function as many times as you want. You have access to it, you don't need to code up the evaluate_sequence() function.

{state_ctx}

You may want to start your search from one of the constructions we have found so far, which you can access through the 'height_sequence_1' global variable. 
However, you are encouraged to explore solutions that use other starting points to prevent getting stuck in a local minimum.

Reason about how you could further improve this construction.
Ideally, try to do something different than the above algorithm. Could be using different algorithmic ideas, adjusting your heuristics, adjusting / sweeping your hyperparemeters, etc. 
Unless you make a meaningful improvement, you will not be rewarded.

Rules:
- You must define the `propose_candidate` function as this is what will be invoked.
- You can use scientific libraries like scipy, numpy, cvxpy[CBC,CVXOPT,GLOP,GLPK,GUROBI,MOSEK,PDLP,SCIP,XPRESS,ECOS], math.
- You can use up to 2 CPUs.
- Make all helper functions top level and have no closures from function nesting. Don't use any lambda functions.
- No filesystem or network IO.
- Do not import evaluate_sequence yourself. Assume it will already be imported and can be directly invoked.
- **Print statements**: Use `print()` to log progress, intermediate bounds, timing info, etc. Your output will be shown back to you.
- Include a short docstring at the top summarizing your algorithm.

Make sure to think and return the final program between ```python and ```.'''

        elif self.problem_type == "ac2":
            return f''''Act as an expert software developer and inequality specialist specializing in creating step functions with certain properties.

Your task is to generate the sequence of non-negative heights of a step functions, that maximizes the following evaluation function:

```python
{ae_verifier_program}
```

{AC2_LITERATURE}
Your task is to write a search function, construct_function(), that searches for the best sequence of coefficients. Your function will have {budget_s} seconds to run, and after that it has to have returned the best sequence it found. If after {budget_s} seconds it has not returned anything, it will be terminated with negative infinity points. All numbers in your sequence have to be positive or zero. Larger sequences with {budget_s}s of items often have better attack surface, but too large sequences with 100s of thousands of items may be too slow to search.

You may code up any search method you want, and you are allowed to call the evaluate_sequence() function as many times as you want. You have access to it, you don't need to code up the evaluate_sequence() function.

{state_ctx}

You may want to start your search from one of the constructions we have found so far, which you can access through the 'height_sequence_1' global variable. 
However, you are encouraged to explore solutions that use other starting points to prevent getting stuck in a local minimum.

Reason about how you could further improve this construction.
Ideally, try to do something different than the above algorithm. Could be using different algorithmic ideas, adjusting your heuristics, adjusting / sweeping your hyperparemeters, etc. 
Unless you make a meaningful improvement, you will not be rewarded, if you are stuck you should think about how to get unstuck.

Rules:
- You must define the `construct_function` function as this is what will be invoked.
- You can use scientific libraries like scipy, numpy, cvxpy[CBC,CVXOPT,GLOP,GLPK,GUROBI,MOSEK,PDLP,SCIP,XPRESS,ECOS], math.
- You can use up to 2 CPUs.
- Make all helper functions top level and have no closures from function nesting. Don't use any lambda functions.
- No filesystem or network IO.
- Do not import evaluate_sequence yourself. Assume it will already be imported and can be directly invoked. Do not import height_sequence_1 yourself; it will already be available.
- **Print statements**: Use `print()` to log progress, intermediate bounds, timing info, etc. Your output will be shown back to you.
- Include a short docstring at the top summarizing your algorithm.

Make sure to think and return the final program between ```python and ```.'''


def discover_ac(problem_type: str):
    # Explicitly define config for clarity
    # Uses default values for most fields
    config = DiscoverConfig(
        env_type=AutoCorrInequalityEnv,
        problem_type=problem_type,
        num_cpus_per_task=CPUS_PER_TASK,
        eval_timeout=1100,
        experiment_name=f"test-{problem_type}-run",
        wandb_project=f"ac-inequalities-{problem_type}",
        model_name="openai/gpt-oss-120b",
        renderer_name="gpt_oss_high_reasoning",
        learning_rate=4e-5,
        num_epochs=50,
        temperature=1.0,
        kl_penalty_coef=0.1,
        phase1_max_tokens=26000,
        groups_per_batch=8,
        group_size=64,
    )
    
    # Run discovery
    discover(config)


if __name__ == "__main__":
    discover_ac("ac1")
    # discover_ac("ac2")
