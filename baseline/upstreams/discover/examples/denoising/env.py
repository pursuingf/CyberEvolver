import inspect
from typing import Any
import numpy as np
from ttt_discover import Environment, SandboxRewardEvaluator, State, DiscoverConfig, discover

from examples.denoising.utils import EVALUATE_MSE_FUNC, EVALUATE_POISSON_FUNC, BASELINES, evaluate_mse, evaluate_poisson, run_denoising_eval
from examples.denoising.prompt import SYSTEM_PROMPT
from ttt_discover.tinker_utils.dataset_builder import VerifyResult
from ttt_discover.tinker_utils.state import to_json_serializable

BASELINES = {
    "pancreas": {
        "baseline_mse": 0.304721,
        "baseline_poisson": 0.257575,
        "perfect_mse": 0.000000,
        "perfect_poisson": 0.031739,
    },
    "pbmc": {
        "baseline_mse": 0.270945,
        "baseline_poisson": 0.300447,
        "perfect_mse": 0.000000,
        "perfect_poisson": 0.043569,
    },
    "tabula": {
        "baseline_mse": 0.261763,
        "baseline_poisson": 0.206542,
        "perfect_mse": 0.000000,
        "perfect_poisson": 0.026961,
    },
}


class DenoisingState(State):
    mse: float
    poisson: float

    def __init__(self, timestep: int, construction: list[Any], code: str, value: float = None, mse: float = None, poisson: float = None, parent_values: list[float] = None, parents: list[dict] = None, id: str = None, observation: str = ""):
        super().__init__(timestep, construction, code, value, parent_values, parents, id, observation)
        self.mse = mse
        self.poisson = poisson
        
    def to_dict(self) -> dict:
        return {
            "type": "DenoisingState",
            "id": self.id,
            "timestep": self.timestep,
            "value": self.value,
            "parent_values": self.parent_values,
            "parents": self.parents,
            "observation": self.observation,
            "construction": to_json_serializable(self.construction),
            "code": self.code,
            "mse": self.mse,
            "poisson": self.poisson,
        }
    
    @classmethod
    def from_dict(cls, d: dict) -> State:
        return cls(
            timestep=d["timestep"],
            construction=d["construction"],
            code=d["code"],
            value=d.get("value"),
            parent_values=d.get("parent_values", []),
            parents=d.get("parents", []),
            id=d.get("id"),
            observation=d.get("observation", ""),
            mse=d.get("mse"),
            poisson=d.get("poisson"),
        )


def verify_denoising(result) -> bool:
    if not isinstance(result, (list, tuple)) or len(result) < 2:
        return False
    mse, poisson = result[0], result[1]
    if not np.isfinite(mse) or not np.isfinite(poisson):
        return False
    baseline = BASELINES["pancreas"]
    if poisson < baseline["perfect_poisson"]:
        return False
    poisson_range = baseline["baseline_poisson"] - baseline["perfect_poisson"]
    poisson_norm = (baseline["baseline_poisson"] - poisson) / poisson_range if poisson_range > 0 else 0
    if poisson_norm < 0.97:
        return False
    return True


class DenoisingRewardEvaluator(SandboxRewardEvaluator):

    def get_program_entrypoint(self) -> str:
        return "run_denoising"

    def preprocess_generation(self, generation: str, state: DenoisingState) -> str:
        imports = f"""import numpy as np
import scipy
import scipy.sparse
from scipy import linalg
from scipy.spatial.distance import cdist, pdist, squareform
from scipy.sparse import csr_matrix, issparse
from sklearn.neighbors import NearestNeighbors
from sklearn.decomposition import PCA, TruncatedSVD
from sklearn.cluster import KMeans
import graphtools
import scprep
import anndata
import scanpy as sc
import sklearn.metrics
import math
import random
from molecular_cross_validation.mcv_sweep import poisson_nll_loss

_SEED = {42}
"""

        evaluate_mse_src = inspect.getsource(evaluate_mse)
        evaluate_poisson_src = inspect.getsource(evaluate_poisson)
        run_denoising_eval_src = inspect.getsource(run_denoising_eval)

        wrapper = """
def run_denoising():
    return run_denoising_eval(magic_denoise, seed=_SEED)
"""

        return (
            imports + "\n\n" +
            evaluate_mse_src + "\n\n" +
            evaluate_poisson_src + "\n\n" +
            run_denoising_eval_src + "\n\n" +
            generation + "\n\n" +
            wrapper
        )


    def get_reward(self, code: str, state: State) -> VerifyResult:
        output, error_msg = self.execute_code(code, state)
        if error_msg: 
            return self._get_failure_entry(error_msg)

        # NOTE: Be careful with conditional-expression precedence.
        # We want to reject invalid solutions for *both* AC1 and AC2.
        is_valid = verify_denoising(output)
        if not is_valid:
            return self._get_failure_entry("Invalid solution.")

        mse, poisson = output[0], output[1]
        current_mse = mse if mse is not None else float('inf')
        reward = 1/current_mse
        
        mse_normalized = None
        poisson_normalized = None
        
        baseline = BASELINES["pancreas"]
        mse_range = baseline["baseline_mse"] - baseline["perfect_mse"]
        poisson_range = baseline["baseline_poisson"] - baseline["perfect_poisson"]
        
        if mse is not None and mse_range > 0:
            mse_normalized = (baseline["baseline_mse"] - mse) / mse_range
            mse_normalized = max(0.0, min(1.0, mse_normalized))
        if poisson is not None and poisson_range > 0:
            poisson_normalized = (baseline["baseline_poisson"] - poisson) / poisson_range
            poisson_normalized = max(0.0, min(1.0, poisson_normalized))
        
        return {
            "reward": reward,
            "msg": "",
            "correctness": 1.0,
            "raw_score": current_mse,
            "result_construction": [],
            "stdout": getattr(self, '_last_stdout', ''),
            "metrics": {
                "mse": current_mse,
                "poisson": poisson,
                "mse_normalized": mse_normalized,
                "poisson_normalized": poisson_normalized,
            },
        }


class DenoisingEnv(Environment):
    reward_function = DenoisingRewardEvaluator
    state_type = DenoisingState

    @classmethod
    def create_initial_state(cls, problem_type: str) -> DenoisingState:
        from examples.denoising.utils import MAGIC_FUNC
        return DenoisingState(timestep=-1, construction=[], code=MAGIC_FUNC, value=0.24, mse=0.2316, poisson=0.0370)

    def get_question(self) -> str:
        """Build prompt from template, injecting previous code from state."""
        state = self.initial_state
        has_code = state.code and state.code.strip()
        
        value_ctx = ""
        if state.mse is not None or state.poisson is not None:
            metrics = []
            if state.mse is not None:
                metrics.append(f"MSE: {state.mse:.6f}")
            if state.poisson is not None:
                metrics.append(f"Poisson: {state.poisson:.6f}")
            value_ctx = f"\nCurrent metrics (lower is better): {', '.join(metrics)}"
        
        prompt = SYSTEM_PROMPT
        prompt = prompt.replace("<<<EVALUATE_MSE_FUNC>>>", EVALUATE_MSE_FUNC)
        prompt = prompt.replace("<<<EVALUATE_POISSON_FUNC>>>", EVALUATE_POISSON_FUNC)
        
        if has_code:
            clean_code = state.code.strip()
            if clean_code.startswith("```python"):
                clean_code = clean_code[len("```python"):].strip()
            if clean_code.startswith("```"):
                clean_code = clean_code[3:].strip()
            if clean_code.endswith("```"):
                clean_code = clean_code[:-3].strip()
            code_section = f"""
Here is the current implementation:
```python
{clean_code}
```

You are iteratively improving the denoising algorithm.{value_ctx}

Reason about how you could improve this approach.
"""
        else:
            code_section = f"""
{value_ctx}

Write code to implement a denoising algorithm.
"""
        
        return f"""{prompt}
{code_section}
Write your improved `magic_denoise` function."""

    def is_maximize(self) -> bool:
        return False # Minimize MSE

    def _create_next_state(
        self,
        step_idx: int,
        parsed_code: str,
        outs: VerifyResult,
    ) -> State:
        mse = None
        poisson = None
        if outs.metrics is not None:
            mse = outs.metrics.get("mse")
            poisson = outs.metrics.get("poisson")

        return self.state_type(
            timestep=step_idx,
            construction=outs.result_construction,
            code=parsed_code,
            value=outs.raw_score if self.is_maximize() else -outs.raw_score, # higher = better
            observation=outs.stdout,
            mse=mse,
            poisson=poisson,
        )

    def _build_metrics(
        self,
        outs: VerifyResult,
        correct_format: bool,
        message: dict,
        parsed_code: str,
    ) -> dict[str, Any]:
        correctness = outs.correctness
        return {
            "format": correct_format,
            "reward": outs.reward,
            "correctness": correctness,
            "raw_score": outs.raw_score if correctness > 0 else None,
            "initial_raw_score": self.initial_state.value,
            "msg": outs.msg,
            "prompt": self.get_question(),
            "response": message['content'],
            "parsed_code": parsed_code,
            "mse": outs.metrics.get("mse"),
            "poisson": outs.metrics.get("poisson"),
            "mse_normalized": outs.metrics.get("mse_normalized"),
            "poisson_normalized": outs.metrics.get("poisson_normalized"),
        }


def discover_denoising():
    config = DiscoverConfig(
        env_type=DenoisingEnv,
        problem_type="",
        num_cpus_per_task=1,
        eval_timeout=530,
        experiment_name=f"test-denoising-run",
        wandb_project="denoising",
    )
    discover(config)


if __name__ == "__main__":
    discover_denoising()