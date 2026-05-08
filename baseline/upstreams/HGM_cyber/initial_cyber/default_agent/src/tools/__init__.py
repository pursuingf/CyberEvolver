# This file is adapted from https://github.com/jennyzzt/dgm.

import importlib
import os
from pathlib import Path

# Tools that are only relevant for specific benchmark types.
# Keyed by tool filename stem → required env var (tool is skipped if env var is empty).
_TOOL_REQUIRES_ENV = {
    "check_done": "SCORING_URL",     # CVEBench only (http_poll scoring)
    "submit": "FLAG_VERIFY_URL",     # Flag-based benchmarks (CTF, autopenbench)
}


def load_all_tools(logging=print):
    tools_dir = Path(__file__).parent
    tools = []

    # Get all Python files in the tools directory (excluding __init__.py)
    tool_files = [f for f in tools_dir.glob("*.py") if f.stem != "__init__"]

    for tool_file in tool_files:
        # Skip tools that don't apply to the current benchmark
        required_env = _TOOL_REQUIRES_ENV.get(tool_file.stem)
        if required_env and not os.environ.get(required_env):
            logging(f"Skipping tool {tool_file.stem} (no {required_env})")
            continue

        # Import the module
        module_name = f"tools.{tool_file.stem}"
        try:
            module = importlib.import_module(module_name)

            # Check if module has required functions
            if hasattr(module, "tool_info") and hasattr(module, "tool_function"):
                tools.append(
                    {
                        "info": module.tool_info(),
                        "function": module.tool_function,
                        "name": tool_file.stem,
                    }
                )
            else:
                raise Exception(
                    f"Tool module {module_name} does not have required functions."
                )
        except Exception as e:
            # Log the error and raise it
            logging(f"Failed to import {module_name}: {e}")
            raise e

    return tools
