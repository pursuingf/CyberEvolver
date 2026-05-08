# OpenProblems Denoising Benchmark

## Setup

```bash
uv venv .venv
source .venv/bin/activate

uv pip install magic-impute
uv pip install git+https://github.com/czbiohub/molecular-cross-validation.git

git clone https://github.com/openproblems-bio/openproblems.git
cd openproblems && git checkout v1.0.0 && cd ..
uv pip install -e ./openproblems

# Apply API fix
cd openproblems && git apply ../openproblems_api_fix.patch && cd ..
```

## Known Issues

### 1. CZI cellxgene API changed
Tabula Muris loader fails. The API now uses:
- `dataset["dataset_id"]` instead of `dataset["id"]`
- Assets embedded in dataset: `dataset["assets"]`
- `asset["url"]` instead of `asset["presigned_url"]`

**Fix**: `openproblems_api_fix.patch`

### 2. NumPy 2.x compatibility
PyTorch pulls NumPy 2.x which breaks old syntax:
```python
# Old (breaks):
np.asarray(Y, dtype=np.float64, copy=False)

# New (works):
np.asarray(Y, dtype=np.float64)
```

