# Reproducing Results

## Setup

To run a job, create and activate a Conda environment, then install the required dependencies for the task you want to run.

- **gpu_mode** Conda environments must use **Python 3.13.11**
- All other tasks are recommended to use **Python 3.11**

Each task has its own `requirements.txt` located under `requirements/`

Install dependencies with the application-specific requirements:
- Math: `requirements/requirements-math.txt`
- GPU kernels: `requirements/requirements-gpumode.txt`
- AtCoder: `requirements/requirements-ale.txt`  
- Denoising: `requirements/denoising/requirements-denoising.txt` (see [README](requirements/denoising/README.md))

## Running Tasks

After installing dependencies, locate the corresponding application under `examples/`

Each application provides a script in `env.py` that launches the job. Run it with:

```
python -m examples.<task_dir>.env
```


## Getting Final Performance

We use the `raw_score` metric for logging our task performance. The final performance is typically the max (or min) of the `raw_score` metric, logged as `env/all/raw_score/max` (or `env/all/raw_score/min` for applications that minimize a value), **across all steps**. For example, in a training run for 50 steps of circle packing, if the max raw score at step 12 is 2.63 and no earlier or later step exceeds it, the final performance is still 2.63.

Some applications may require extra processing for our final results, such as denoising.

The following examples are maximization tasks: `second ac inequalities`, `circle packing`, and `AHC`. For performance, you should track the `env/all/raw_score/max`.

The following examples are minimization tasks: `first ac inequalities`, `erdos minimum overlap`, `denoising`, and `gpu mode`. For performance, you should track the `env/all/raw_score/min`.

## Multi-node Execution

Multi-node execution is supported via Slurm.

## Hardware Requirements and Performance Notes

All reported results were run using HPC-grade CPUs.

Mathematics and AHC tasks will perform significantly worse if they are not run on HPC-grade CPUs or if they are limited to a small number of cores. For these tasks, it is strongly recommended to use a large number of CPU cores and multiple hosts.

## AHC Container Requirements

For AHC tasks, jobs must be launched inside the ALE-Bench provided C++ container:

`yimjk/ale-bench:cpp20-202301`

Docker Hub:
https://hub.docker.com/layers/yimjk/ale-bench/cpp20-202301/images/sha256-946af1b209a84160594e43262b5157aec933938c99e2585b53042cac9bc3f43c

We support the Pyxis Slurm plugin to launch this container across multiple nodes for AHC, but it is not strictly required.