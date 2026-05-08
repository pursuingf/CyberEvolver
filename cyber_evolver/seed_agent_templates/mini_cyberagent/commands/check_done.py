#!/usr/bin/env python3
"""
Check whether the current CVE Bench target has been marked as solved by the
evaluator service exposed at target:9091/done.
"""

import json
import subprocess
import sys


DONE_URL = "http://target:9091/done"


def check_done() -> int:
    """
    signature: check_done
    docstring: queries the evaluator done endpoint for the current target and reports whether the attack has already succeeded.
    arguments:
        none
    """
    result = subprocess.run(
        ["curl", "-fsS", DONE_URL],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or f"curl exited with code {result.returncode}"
        raise RuntimeError(stderr)

    raw_text = result.stdout.strip()
    payload = json.loads(raw_text)
    status = bool(payload.get("status"))
    message = str(payload.get("message", "") or "")

    print("=== DONE STATUS ===")
    print(f"status: {'true' if status else 'false'}")
    print(f"message: {message}")
    print(f"raw_json: {json.dumps(payload, separators=(',', ':'))}")
    return 0 if status else 10


def cmd_doc() -> str:
    return """
signature: check_done
docstring: queries the evaluator done endpoint for the current target and reports whether the attack has already succeeded.
arguments:
    none
"""


if __name__ == "__main__":
    try:
        sys.exit(check_done())
    except Exception as exc:  # pragma: no cover - exercised via CLI contract
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
