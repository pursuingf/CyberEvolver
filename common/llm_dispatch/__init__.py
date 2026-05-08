"""Cross-process LLM request dispatcher.

Public API is re-exported from the ``dispatcher`` submodule. Tests that need
to monkey-patch module-level globals (httpx, time, etc.) should target
``common.llm_dispatch.dispatcher`` directly.
"""

from .dispatcher import *  # noqa: F401,F403
from . import dispatcher  # noqa: F401  (kept so tests can patch dispatcher.X)
