"""Repo-root conftest.

Pytest discovers this file and inserts its directory into sys.path before
collection. This means tests can do `from common.utils import ...` without
having to add the repo root to PYTHONPATH manually.

Even though pyproject.toml pins `pythonpath = ["."]`, an explicit conftest
keeps things working when tests are run via plain `pytest <file>` from
arbitrary directories.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
