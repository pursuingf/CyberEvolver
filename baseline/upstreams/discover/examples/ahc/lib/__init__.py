"""ALE-Bench evaluation package."""

import importlib.metadata
import sys

if sys.version_info < (3, 9):
    raise RuntimeError("ALE-Bench evaluation requires Python 3.9 or higher.")

try:
    __version__ = importlib.metadata.version("ale_bench")
except importlib.metadata.PackageNotFoundError:
    __version__ = "0.0.0"  # Fallback version if package is not installed

from .data import list_problem_ids
from .start import restart, start
from .utils import clear_cache, get_cache_dir

__all__ = ["clear_cache", "get_cache_dir", "list_problem_ids", "restart", "start"]
