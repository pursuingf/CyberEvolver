#!/bin/bash
#SBATCH --requeue
#SBATCH --job-name=ahc058
#SBATCH --output=./slurm_logs/train_tinker_ahc_%j.out
#SBATCH --error=./slurm_logs/train_tinker_ahc_%j.err
#SBATCH --time=04:00:00
#SBATCH --nodes=5
#SBATCH -A default
#SBATCH -c 128
#SBATCH --partition default
#SBATCH --exclusive
#SBATCH --gpus-per-node 0

# Initialize conda
CONDA_HOME="" # TODO: set conda home
source "$CONDA_HOME/etc/profile.d/conda.sh"
CONDA_ENV_NAME="" # TODO: set conda env name
conda activate ${CONDA_ENV_NAME}

export HF_TOKEN="" # TODO: set HF token
export HF_HUB_DISABLE_XET=1
export TINKER_API_KEY="" # TODO: set tinker api key
export WANDB_ENTITY="" # TODO: set wandb entity
export WANDB_API_KEY="" # TODO: set wandb api key

source ~/.bashrc
set -euo pipefail

# --- Environment (adjust to your setup) ---

# Capture the current working directory (where the script is being run from)
WORK_DIR="$(pwd -P)"
REPO_ROOT="${WORK_DIR}"

# 1) Make your modules importable on every worker
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

# Ensure system PATH is available for Ray workers (needed for g++-12 in container)
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH}"

CONTAINER_IMAGE="yimjk/ale-bench:cpp20-202301"
CONTAINER_MOUNTS="" # TODO: set container mounts, most likely repo root
# FMT: /host/path:/container/path,...

# --- srun wrapper to avoid SLURM mem-var conflicts ---
# Prepare conda activation command for use inside container
# Force use of conda environment's ray by using full path and ensuring proper environment isolation
RAY_BIN="${CONDA_HOME}/envs/${CONDA_ENV_NAME}/bin/ray"
PYTHON_BIN="${CONDA_HOME}/envs/${CONDA_ENV_NAME}/bin/python"
# Activate conda and ensure env's Python and site-packages are used
# Preserve system PATH to ensure g++-12 and other system tools are available
# We'll set PYTHONPATH inside the container to prioritize env's site-packages
CONDA_ACTIVATE="source $CONDA_HOME/etc/profile.d/conda.sh && conda deactivate 2>/dev/null || true && conda activate ${CONDA_ENV_NAME} && export PATH=\"${CONDA_HOME}/envs/${CONDA_ENV_NAME}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:\$PATH\" && SITE_PKGS=\$(\"${PYTHON_BIN}\" -c \"import site; print(site.getsitepackages()[0])\" 2>/dev/null || echo \"${CONDA_HOME}/envs/${CONDA_ENV_NAME}/lib/python3.11/site-packages\") && export PYTHONPATH=\"\$SITE_PKGS:${PYTHONPATH}\""
SRUN=(env -u SLURM_MEM_PER_CPU -u SLURM_MEM_PER_GPU -u SLURM_MEM_PER_NODE srun --container-image ${CONTAINER_IMAGE} --container-mounts ${CONTAINER_MOUNTS})

# --- Nodes & head IP ---
mapfile -t nodes_array < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
head_node=${nodes_array[0]}
num_nodes=$((${#nodes_array[@]}))
echo "Number of workers: ${num_nodes}"
ip=$(getent ahostsv4 "$head_node" | awk 'NR==1{print $1}')
: "${ip:?FATAL: could not resolve IP for $head_node}"

port=6379
dashboard_port=8265
client_port=10001
# Must be outside worker range 20000-29999 to avoid conflict with dashboard_agent_grpc
dashboard_agent_grpc_port=52360
# Must be outside worker range 20000-29999 to avoid conflict (metrics_export was using 23358)
metrics_export_port=52361
ip_head="${ip}:${port}"
echo "Head node: $head_node  IP: $ip  GCS: $ip_head"


# RAY_ADDRESS will be set inside the container when running the training script
# Don't export it here because ray status doesn't accept ray:// format

# --- Start Ray head first (in parallel with workers) ---
echo "STARTING HEAD at $head_node (will start Ray head, wait for workers, then run training)"
"${SRUN[@]}" --export=ALL --nodes=1 --ntasks=1 -w "$head_node" \
  bash -c "
      ${CONDA_ACTIVATE}
      
      # Change to the working directory
      cd \"${WORK_DIR}\"
      echo \"Working directory: \$(pwd)\"
      
      # Start Ray head in background
      echo 'Starting Ray head...'
      # Set explicit port ranges to avoid conflicts: worker 20000-29999; dashboard_agent_grpc
      # and metrics_export must be outside that range (they would otherwise be random and can land in 20000-29999).
      \"${RAY_BIN}\" start --head \
        --node-ip-address=\"$ip\" \
        --port=\"$port\" \
        --dashboard-host=127.0.0.1 \
        --dashboard-port=\"$dashboard_port\" \
        --ray-client-server-port=\"$client_port\" \
        --dashboard-agent-grpc-port=\"$dashboard_agent_grpc_port\" \
        --metrics-export-port=\"$metrics_export_port\" \
        --min-worker-port=20000 \
        --max-worker-port=29999 &
      RAY_HEAD_PID=\$!
      
    # Wait for Ray head to be ready (reduced from 5s to 2s)
    sleep 2
    
    # Wait for workers to connect
    echo 'Waiting for workers to connect...'
    EXPECTED_WORKERS=$((num_nodes - 1))
    if [ \$EXPECTED_WORKERS -gt 0 ]; then
      MAX_WAIT=120  # Reduced from 5 minutes to 2 minutes
      WAIT_TIME=0
      CHECK_INTERVAL=2  # Check every 2 seconds initially (faster)
      while [ \$WAIT_TIME -lt \$MAX_WAIT ]; do
        # Use Python to check Ray cluster status directly (more reliable than parsing ray status)
        unset RAY_ADDRESS
        CONNECTED_NODES=\$(\"${PYTHON_BIN}\" -c \"
import ray
try:
    ray.init(address='auto', ignore_reinit_error=True)
    nodes = ray.nodes()
    # Total nodes minus 1 (head node) = worker nodes
    total_nodes = len([n for n in nodes if n.get('Alive', False)])
    worker_count = max(0, total_nodes - 1)
    print(worker_count)
    ray.shutdown()
except Exception as e:
    print('0')
\" 2>/dev/null || echo '0')
        
        # Convert to integer for comparison
        CONNECTED_NODES_INT=\${CONNECTED_NODES:-0}
        if [ \"\$CONNECTED_NODES_INT\" -eq \"\$EXPECTED_WORKERS\" ] 2>/dev/null; then
          echo \"All \$EXPECTED_WORKERS workers connected! (detected: \$CONNECTED_NODES_INT)\"
          break
        elif [ \"\$CONNECTED_NODES_INT\" -gt \"\$EXPECTED_WORKERS\" ] 2>/dev/null; then
          echo \"WARNING: More workers connected than expected (expected: \$EXPECTED_WORKERS, detected: \$CONNECTED_NODES_INT). Proceeding anyway.\"
          break
        fi
        # Only print status every 10 seconds to reduce noise
        if [ \$((WAIT_TIME % 2)) -eq 0 ]; then
          echo \"Waiting for workers... (expected: \$EXPECTED_WORKERS, detected: \${CONNECTED_NODES_INT}, waited: \${WAIT_TIME}s)\"
        fi
        sleep \$CHECK_INTERVAL
        WAIT_TIME=\$((WAIT_TIME + CHECK_INTERVAL))
        # Back off to 5s intervals after 30 seconds
        if [ \$WAIT_TIME -ge 30 ] && [ \$CHECK_INTERVAL -lt 5 ]; then
          CHECK_INTERVAL=5
        fi
      done
      
      if [ \$WAIT_TIME -ge \$MAX_WAIT ]; then
        echo \"ERROR: Timeout waiting for workers. Expected \$EXPECTED_WORKERS workers but only detected \$CONNECTED_NODES_INT.\"
        echo \"Ray status:\"
        unset RAY_ADDRESS
        \"${RAY_BIN}\" status || true
        echo \"FATAL: Not all workers connected. Exiting.\"
        exit 1
      fi
    else
      echo 'No additional workers expected (single node setup)'
    fi
    
    # Run the training script in the same container/environment
    echo 'Starting training script...'
    export RAY_ADDRESS=\"ray://${ip}:${client_port}\"
    python -m examples.ahc.env
    
    TRAIN_EXIT_CODE=\$?
    
    # Cleanup: stop Ray head
    echo 'Stopping Ray head...'
    \"${RAY_BIN}\" stop || true
    wait \$RAY_HEAD_PID 2>/dev/null || true
    
      exit \$TRAIN_EXIT_CODE
  " &
HEAD_SRUN_PID=$!

# --- Start Ray workers on non-head nodes (in parallel with head) ---
worker_pids=()
if (( num_nodes > 1 )); then
  for ((i=1; i<num_nodes; i++)); do
    node_i=${nodes_array[$i]}
    echo "STARTING WORKER $i at $node_i"
    "${SRUN[@]}" --export=ALL --nodes=1 --ntasks=1 -w "$node_i" \
      bash -c "${CONDA_ACTIVATE} && cd \"${WORK_DIR}\" && \"${RAY_BIN}\" start --address \"$ip_head\" --dashboard-agent-grpc-port=${dashboard_agent_grpc_port} --metrics-export-port=${metrics_export_port} --min-worker-port=20000 --max-worker-port=29999 --block" &
    worker_pids+=($!)
    # Reduced sleep between worker starts (from 10s to 2s)
    sleep 2
  done
fi

# Wait for head node process to finish
wait $HEAD_SRUN_PID

# Wait for all worker processes to finish
for pid in "${worker_pids[@]}"; do
  wait $pid 2>/dev/null || true
done
