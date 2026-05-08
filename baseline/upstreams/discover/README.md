<p align="center">
  <h1 align="center">🔬 TTT-Discover</h1>
  <h3 align="center">Learning to Discover at Test Time</h3>
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2601.16175"><img src="https://img.shields.io/badge/arXiv-2601.16175-b31b1b.svg" alt="arXiv"></a>
  <a href="https://test-time-training.github.io/discover/"><img src="https://img.shields.io/badge/Project-Page-blue" alt="Project Page"></a>
  <a href="#"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License"></a>
</p>

<p align="center">
  <b>Mert Yuksekgonul*</b>, <b>Daniel Koceja*</b>, <b>Xinhao Li*</b>, <b>Federico Bianchi*</b><br>
  Jed McCaleb, Xiaolong Wang, Jan Kautz, Yejin Choi, James Zou†, Carlos Guestrin†, Yu Sun*
</p>

<p align="center">
  <em>Stanford · NVIDIA · Astera Institute · UC San Diego · Together AI</em>
</p>

---

**TTT-Discover** performs reinforcement learning at test time, allowing the LLM to continue training with experience specific to the problem at hand. We achieve **new state-of-the-art** across mathematics, GPU kernels, algorithms, and biology.

<p align="center">
  <img src="docs/assets/figure1.svg" width="800">
</p>

## Installation

```bash
pip install ttt-discover
```

Or from source:
```bash
pip install -e .
```

Set environment variables:

```bash
export HF_TOKEN="..."
export TINKER_API_KEY="..."      
export WANDB_API_KEY="..."       
export WANDB_ENTITY="..."        
```

## Making your own Environment

To use TTT-Discover for your own application, you should create a new environment. Here are the general steps to make your own environment.

1) Create a new environment that inherits ttt_discover.Environment.

2) Define a reward evaluator that inherits ttt_discover.BaseRewardEvaluator. Optionally, you can use ttt_discover.SandboxRewardEvaluator to run generated code in sandboxes.

4) (Optional) Add initial state definition to your environment.

5) Define a config and run with ttt_discover.discover!

Here is a sample skeleton for a new environment.
```python
# Import requred ttt_discover objects
from ttt_discover import Environment, BaseRewardEvaluator, State, DiscoverConfig, discover


# Define your reward function
class YourReward(BaseRewardEvaluator):

    def get_reward(self, code: str, state: State) -> float:
        # ...add logic here for computing reward

        return {
            "reward": reward,
            "correctness": 1.0,
            "raw_score": raw_score,
            "msg": f"Success; raw_score={raw_score}",
            "result_construction": [], # Could reuse
            "stdout": "", # No stdout
        }


class YourEnv(Environment):
    reward_function = YourReward
    state_type = State # You may define your own state if you wish

    def get_question(self) -> str:
        state_ctx = self.initial_state.to_prompt(100, metric_name="performance")

        return f"""You are an expert mathematician specializing in combinatorial problems and computational geometry. Your task is to ... {state_ctx}."""


config = DiscoverConfig(
    env_type=YourEnv,
    experiment_name="test-run",
    wandb_project="",
)

# Run discovery
discover(config)
```

Check [examples/circle_packing](examples/circle_packing) for a fully implemented example.


## Key Results

<div align="center">

|                  | **Mathematics**<br>Erdős Overlap ↓ | **Kernel A100**<br>TriMul ↓ | **Kernel H100**<br>TriMul ↓ | **Algorithms**<br>AtCoder ↑ | **Biology**<br>Denoising ↑ |
|------------------|:----------------------------------:|:---------------------------:|:---------------------------:|:---------------------------:|:--------------------------:|
| Best Human       | 0.380927                           | 4531 μs                     | 1371 μs                     | 566,997                     | 0.64                       |
| Prev. Best AI    | 0.380924                           | —                           | —                           | 558,026                     | —                          |
| **TTT-Discover** | **0.380876**                       | **2198 μs**                 | **1161 μs**                 | **567,062**                 | **0.71**                   |

</div>

## Domains

<details>
<summary><b>Mathematics</b> — Classic open problems in combinatorics and analysis</summary>

<p align="center">
  <img src="docs/assets/erdos.png" width="800">
</p>

<div align="center">

| Task | Erdős Min. Overlap ↓ | Autocorr. (AC1) ↓ | Autocorr. (AC2) ↑ |
|------|:--------------------:|:-----------------:|:-----------------:|
| Best Human | 0.380927 | 1.50973 | 0.9015 |
| Prev. Best AI | 0.380924 | 1.50314 | 0.9610 |
| **TTT-Discover** | **0.380876** | **1.50287** | 0.9591 |

</div>

</details>

<details>
<summary><b>Kernel Engineering</b> — GPUMode TriMul competition for triangular matrix multiplication</summary>

<div align="center">

| Task | A100 ↓ | H100 ↓ | B200 ↓ | MI300x ↓ |
|------|:------:|:------:|:------:|:--------:|
| Best Human | 4531 μs | 1371 μs | 1005 μs | 2462 μs |
| **TTT-Discover** | **2198 μs** | **1161 μs** | **905 μs** | **1596 μs** |

</div>

</details>

<details>
<summary><b>Algorithm Engineering</b> — AtCoder Heuristic Contests on real-world optimization [<a href="https://atcoder.jp/contests/ahc039/submissions/72633477">AHC39</a>] [<a href="https://atcoder.jp/contests/ahc058/submissions/72633508">AHC58</a>]</summary>

<div align="center">

| Task | AHC39 (Geometry) ↑ | AHC58 (Scheduling) ↑ |
|------|:------------------:|:--------------------:|
| Best Human | 566,997 | 847,674,723 |
| Prev. Best AI | 558,026 | 848,373,282 |
| **TTT-Discover** | **567,062** | **848,414,228** |

</div>

</details>

<details>
<summary><b>Biology</b> — Single-cell RNA-seq denoising on OpenProblems benchmark</summary>

<div align="center">

| Task | PBMC ↑ | Tabula ↑ |
|------|:------:|:--------:|
| Best Human | 0.64 | 0.64 |
| **TTT-Discover** | **0.71** | **0.73** |

</div>

</details>
</br>

The environments to reproduce results from our paper are under examples/. To run these, please see [reproducing.md](docs/reproducing.md)

## Submitit
We provide submitit script to launch ttt-discover as a slurm job across multiple nodes with ray. See [submitit_launch.sh](examples/circle_packing/submitit_launch.sh) for an example.

## Security Notice

It is **recommended** to run all jobs on an isolated network or VPN if using ray. Ray has minimal built-in security protections and should not be exposed on a public or shared network.

## Acknowledgments

This work builds on several outstanding projects and communities:

- **[GPU Mode](https://github.com/gpu-mode)** — Community for GPU kernel optimization and the TriMul competition
- **[ALE-Bench](https://github.com/PLACEHOLDER)** — AtCoder-based benchmark for LLM evaluation
- **[Tinker](https://github.com/PLACEHOLDER)** — LLM training recipes and RL framework

## Citation

```bibtex
@article{ttt-discover2026,
  title   = {Learning to Discover at Test Time},
  author  = {Yuksekgonul, Mert and Koceja, Daniel and Li, Xinhao 
             and Bianchi, Federico and McCaleb, Jed and Wang, Xiaolong 
             and Kautz, Jan and Choi, Yejin and Zou, James 
             and Guestrin, Carlos and Sun, Yu},
  journal = {arXiv preprint arXiv:2601.16175},
  year    = {2026}
}
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

