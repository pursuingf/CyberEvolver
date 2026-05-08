"""Execution-mode profiles, model configs, and config-loading helpers."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml


EVO_CONFIG: Dict[str, Any] = {
    "max_generations": 4,
    "sample_plan_num": 3,
    "children_per_node": 3,
    "samples_per_node": 1,
    "top_k_selection": 2,
    "mutation_model": "DeepSeek-V3.1",
    "base_model": "DeepSeek-V3.1",
}

# Ablation C — greedy sequential, no beam search.
# k=1, m=1, sample_plan_num=1 → at each gen we take exactly the best (only)
# parent, sample one plan, produce one child. T=16 generations matches the
# total node budget of full evo (~16 nodes) for a fair compute comparison.
EVO_NO_BEAM_CONFIG: Dict[str, Any] = {
    "max_generations": 16,
    "sample_plan_num": 1,
    "children_per_node": 1,
    "samples_per_node": 1,
    "top_k_selection": 1,
    "mutation_model": "DeepSeek-V3.1",
    "base_model": "DeepSeek-V3.1",
}

RAW_CONFIG: Dict[str, Any] = {
    "max_generations": 1,
    "sample_plan_num": 3,
    "children_per_node": 3,
    "samples_per_node": 16,
    "top_k_selection": 2,
    "mutation_model": "DeepSeek-V3.1",
    "base_model": "DeepSeek-V3.1",
}

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MODEL_YAML_PATH = _REPO_ROOT / "common" / "configs" / "model.yml"


@lru_cache(maxsize=1)
def get_model_configs() -> Dict[str, Any]:
    """Lazily read common/configs/model.yml.

    Resolved against the repo root rather than the current working directory,
    so callers do not need to ``cd`` into the repo before importing this module.
    """
    if not _MODEL_YAML_PATH.exists():
        raise FileNotFoundError(
            f"Model dispatcher config missing: {_MODEL_YAML_PATH}. "
            "Copy common/configs/model.yml.example to common/configs/model.yml "
            "and fill in your provider credentials."
        )
    with _MODEL_YAML_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_execution_config(config_mode: str, model_name: str | None = None) -> Dict[str, Any]:
    normalized_mode = str(config_mode or "").strip().lower()
    if normalized_mode == "evo":
        resolved = dict(EVO_CONFIG)
    elif normalized_mode == "evo_no_beam":
        resolved = dict(EVO_NO_BEAM_CONFIG)
    elif normalized_mode == "raw":
        resolved = dict(RAW_CONFIG)
    else:
        raise ValueError(f"Unsupported config mode: {config_mode}")

    if model_name:
        resolved["base_model"] = model_name
        resolved["mutation_model"] = model_name
    return resolved


def load_global_config(config_path: str) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def prepare_model_kwargs_for_dispatch(model_kwargs: Dict[str, Any]) -> Dict[str, Any]:
    prepared = dict(model_kwargs or {})
    # Some providers enable thinking by default unless it is explicitly disabled.
    prepared["thinking"] = False
    chat_template_kwargs = dict(prepared.get("chat_template_kwargs") or {})
    chat_template_kwargs["enable_thinking"] = False
    prepared["chat_template_kwargs"] = chat_template_kwargs
    return prepared
