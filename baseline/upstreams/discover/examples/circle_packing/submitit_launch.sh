#!/bin/bash
CONDA_HOME="" # TODO: set conda home
source "$CONDA_HOME/etc/profile.d/conda.sh"
conda activate discover # TODO: set conda env

export HF_TOKEN="" # TODO: set HF token
export HF_HUB_DISABLE_XET=1
export TINKER_API_KEY="" # TODO: set tinker api key
export WANDB_ENTITY="" # TODO: set wandb entity
export WANDB_API_KEY="" # TODO: set wandb api key
####################################################

# Define your slurm config below
python train_submitit.py --cmd "python -m examples.circle_packing.env" \
    --nodes 2 \
    --gpus-per-node 0 \
    --cpus-per-task 128 \
    --mem 0 \
    --timeout_min 240 \
    --partition default \
    --account default \
    --job-name cp-test \