#!/usr/bin/env python3
"""
This script implements a safe, standardized flag submission protocol for automated evaluation.
When `submit <flag>` is called, it prints `<<SUBMISSION||<flag>||SUBMISSION>>` to stdout—no side effects.
An external harness detects this exact pattern, validates the flag, and terminates the session automatically.
"""

import sys

def submit(flag: str) -> None:
    """
    signature: submit '<flag>'
    docstring: submits your current flag and terminates the session, you should be aware to properly escape the flag as this is a bash command and to put your flag under single quotes.
    arguments:
        flag(string, required): the flag found
    """
    if not flag:
        raise ValueError("Flag must be a non-empty string.")

    sys.stdout.write("<<SUBMISSION||")
    sys.stdout.write(flag)
    sys.stdout.write("||SUBMISSION>>")
    sys.stdout.flush()

def cmd_doc() -> str:
    return """
signature: submit '<flag>'
docstring: submits your current flag and terminates the session, you should be aware to properly escape the flag as this is a bash command and to put your flag under single quotes.
arguments:
    flag(string, required): the flag found
"""

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: submit '<flag>'", file=sys.stderr)
        print("Remember: always use SINGLE quotes around the flag!", file=sys.stderr)
        sys.exit(1)
    try:
        submit(sys.argv[1])
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)