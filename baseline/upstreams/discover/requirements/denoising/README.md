# OpenProblems Denoising Benchmark

## Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Setup

**Important**: Use Python 3.11 (not 3.12+).

```bash
cd /path/to/ttt-continuous

uv venv .venv --python 3.11
source .venv/bin/activate

# 1. Install requirements
uv pip install -r requirements/denoising/requirements-denoising.txt

# 2. Git dependencies
uv pip install git+https://github.com/czbiohub/simscity.git
uv pip install --no-deps git+https://github.com/czbiohub/molecular-cross-validation.git

# 3. Clone and install openproblems (--no-deps to avoid version conflicts)
git clone https://github.com/openproblems-bio/openproblems.git
cd openproblems && git checkout v1.0.0 && cd ..

# 4. Apply patch (MUST be done before installing)
cd openproblems && git apply ../requirements/denoising/openproblems_api_fix.patch && cd ..

# 5. Install openproblems
uv pip install --no-deps -e ./openproblems
```

## Why --no-deps for openproblems?

`openproblems` v1.0.0 pins old dependencies (numpy 1.23.5, pandas 1.3.5, etc.) that conflict with modern packages like transformers and torch. Installing with `--no-deps` avoids these conflicts.

## What the patch fixes

The `openproblems_api_fix.patch` fixes three issues:

1. **CZI cellxgene API change** - Tabula Muris loader uses outdated API endpoints
2. **NumPy 2.x compatibility** - Replaces deprecated `np.int` with `int`
3. **Configurable cache directory** - Adds `OPENPROBLEMS_CACHE_DIR` env var support

## Caching datasets

Set `OPENPROBLEMS_CACHE_DIR` to persist downloaded datasets:

```bash
export OPENPROBLEMS_CACHE_DIR="/path/to/ttt-continuous/.openproblems_cache"
```

This avoids re-downloading data on each run and is required for distributed training where `/tmp` isn't shared across nodes.
