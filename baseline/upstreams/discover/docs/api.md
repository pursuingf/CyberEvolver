# API Reference

Public API of `ttt_discover`. Import from the package root:

```python
from ttt_discover import (
    Environment,
    DiscoverConfig,
    discover,
    State,
    BaseRewardEvaluator,
    SandboxRewardEvaluator,
)
```

---

## Discovery

### `discover(config: DiscoverConfig) -> None`

Runs discovery with test-time RL training: builds dataset and RL config from `config`, sets up logging and (optionally) Ray, then runs training. This is the main entry point for launching a discovery run.

- **config** — A `DiscoverConfig` instance (see below).
- **Blocks** until training completes (uses `asyncio.run` internally).

---

### `DiscoverConfig`

Configuration for discovery runs. Defined with `chz.chz` (frozen/dataclass-like). All attributes have defaults.

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `model_name` | `str` | `"openai/gpt-oss-120b"` | Model name for training and tokenizer. |
| `lora_rank` | `int` | `32` | LoRA rank. |
| `renderer_name` | `str \| None` | `"gpt_oss_high_reasoning"` | Renderer for prompts. |
| `save_every` | `int` | `5` | Save checkpoint every N epochs. |
| `group_size` | `int` | `8` | Envs per group. |
| `groups_per_batch` | `int` | `64` | Groups per batch. |
| `learning_rate` | `float` | `4e-5` | Learning rate. |
| `num_epochs` | `int` | `50` | Training epochs. |
| `temperature` | `float` | `1.0` | Sampling temperature. |
| `kl_penalty_coef` | `float` | `0.1` | KL penalty coefficient. |
| `phase1_max_tokens` | `int` | `26000` | Token budget for prompt + thinking (two-phase sampling). |
| `experiment_name` | `str \| None` | `None` | Experiment name (used in log path). |
| `wandb_project` | `str \| None` | `"tinker-cookbook"` | Weights & Biases project. |
| `env_type` | `str` | `Environment` | Environment class (e.g. your subclass of `Environment`). |
| `problem_type` | `str` | `"26"` | Problem type identifier. |
| `num_cpus_per_task` | `int` | `0` | CPUs per task; if `> 0`, Ray is initialized for job dispatch. |
| `eval_timeout` | `int` | `1000` | Evaluation timeout (e.g. seconds). |

---

## Environments and state

### `Environment`

Base class for problem environments. Subclass this to define a new task; the discovery pipeline uses it to build rollouts, prompt the model, and verify code.

- **Class attribute:** `state_type` — the `State` subclass used for this env (e.g. `State` or a custom subclass).
- **Class attribute:** `reward_function` — class (e.g. a `BaseRewardEvaluator` subclass or factory) used to create the evaluator. It is called with `problem_type`, `log_dir`, `eval_timeout`, `num_cpus_per_task`; the returned instance must provide `get_reward(generation, state)`.

**Class methods**

- **`create_initial_state(problem_type: str) -> State`**  
  Factory for the initial state used in rollouts. Override in subclasses to provide task-specific initial state.

**Constructor**

- **`__init__(self, renderer, initial_state: State, sampler, config)`**  
  `renderer` and `config` come from the dataset builder; `initial_state` and `sampler` are provided per rollout.

**Abstract methods to implement**

- **`get_question() -> str`**  
  Build the prompt string (e.g. from a template, injecting previous code from `state`).

**Other methods (override as needed)**

- **`is_maximize() -> bool`** — Default `True` (higher reward = better). IMPORTANT: If setting is_maximize to False, the sampler will internally negate the state values, meaning you should probably create an initial state with a very negative initial value to avoid the sampler getting stuck in the intial state.
- **`check_format(parsed_code: str) -> bool`** — Validate format of parsed code.
- **`check_answer(parsed_code: str, step: int) -> VerifyResult`** — Async answer checking (uses timeout and optional sandbox).

Internally the env uses a reward evaluator (e.g. `BaseRewardEvaluator` / `SandboxRewardEvaluator`) to turn code + state into rewards and constructions.

---

### `State`

Base class for a single rollout state: one “construction” (e.g. reuse), the code that produced it, and metadata used for value estimation and prompting.

**Attributes**

| Attribute | Type | Description |
|-----------|------|-------------|
| `id` | `str` | Unique identifier (UUID if not provided). |
| `timestep` | `int` | Training step when this state was first visited. |
| `value` | `float` | Expected value from this state (higher = better in default setup). |
| `code` | `str` | Code that produced this construction. |
| `construction` | `list[Any]` | JSON-serializable construction (e.g. solution object). |
| `parent_values` | `list[float]` | Ancestor values (most recent first) for terminal value estimation. |
| `parents` | `list[dict]` | Parent refs `[{"id": ..., "timestep": ...}, ...]` (most recent first). |
| `observation` | `str` | Stdout/logs from the code that created this state. |

**Constructor**

- **`State(timestep, construction, code, value=None, parent_values=None, parents=None, id=None, observation="")`**

**Methods**

- **`to_dict() -> dict`** — Serialize to a dictionary (e.g. for logging or replay).
- **`from_dict(d: dict) -> State`** — Class method: construct from a dictionary.
- **`to_prompt(target, metric_name="value", maximize=True, language="") -> str`** — Build context string for the prompt (previous code, value before/after, target, gap, and optional stdout).

---

## Reward evaluators

### `BaseRewardEvaluator`

Minimal abstract interface for reward evaluation. Concrete evaluators may return more than a float elsewhere; this interface only requires a single method.

**Abstract method**

- **`get_reward(self, code: str, state: Any) -> float`**  
  Compute reward for the given code and state. Must be implemented by subclasses.

---

### `SandboxRewardEvaluator`

Evaluator that runs model-generated code in a separate process via Ray: writes code to a temp file, runs it with CPU affinity and timeout, and returns results (or failure info). Supports code extraction from markdown, stdout capture, and optional verifier preprocessing.

**Inherits from:** `BaseRewardEvaluator`.

**Constructor**

- **`__init__(self, problem_type: int, log_dir: str, num_cpus_per_task: int = 1, fail_score: float = 0.0, eval_timeout: int = 530, worst_perf_log: float = 0.0, env_type: str = "")`**
  - **problem_type** — Problem identifier.
  - **log_dir** — Directory for logs and temp files (e.g. `tmp` under it).
  - **num_cpus_per_task** — CPUs allocated per Ray task (must be ≥ 1).
  - **fail_score** — Reward to return on failure.
  - **eval_timeout** — Timeout in seconds for running the generated program.
  - **worst_perf_log** — Value used for “worst” performance in logs.
  - **env_type** — Optional env type name for logging/identification.

**Abstract method to implement**

- **`get_program_entrypoint() -> str`**  
  Name of the function in the generated script that should be executed (e.g. `"main"` or `"run"`).

- **`get_reward(self, code: str, state: Any)`**  
  Full reward interface used by the env; typically implemented to call `execute_code` and map results to a reward dict with `reward`, `msg`, `correctness`, `raw_score`, `stdout`.

**Methods**

- **`preprocess_generation(self, generation, state) -> str`**  
  Optionally wrap or modify the model generation (e.g. add verifier and construction from `state`). Override or use `verifier_src`; default returns `generation` unchanged if no verifier.

- **`execute_code(self, solution_str: str, state) -> tuple[Any, str \| None]`**  
  Extract code from `solution_str`, run `preprocess_generation`, execute in sandbox. Returns `(result, None)` on success or `(None, error_message)` on failure.

**Attributes**

- **`verifier_src`** — Optional callable whose source is injected in `preprocess_generation` (e.g. verifier function).
- **`_last_stdout`** — Set after execution; holds stdout of the last run (useful for failure entries and debugging).

---

## Summary

| Symbol | Kind | Purpose |
|--------|------|---------|
| `discover` | function | Run discovery with RL from a `DiscoverConfig`. |
| `DiscoverConfig` | class | Config for `discover` (model, training, env, problem, CPUs, timeouts). |
| `Environment` | class | Base env for problems; implement `get_question`, optionally override state/reward behavior. |
| `State` | class | One rollout state (construction, code, value, parents, observation); serialization and `to_prompt`. |
| `BaseRewardEvaluator` | abstract class | Interface: `get_reward(code, state) -> float`. |
| `SandboxRewardEvaluator` | class | Runs code in a sandbox via Ray; implement `get_program_entrypoint` and optionally `get_reward` / preprocessing. |
