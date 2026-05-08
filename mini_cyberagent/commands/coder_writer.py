#!/usr/bin/env python3
import sys
import os
import re
import json
import argparse
import requests
import glob

# ==========================================
# CONFIGURATION
# ==========================================
API_KEY = os.getenv("CODER_API_KEY", "")
API_URL = os.getenv("CODER_API_URL", "")
MODEL_NAME = os.getenv("CODER_MODEL_NAME", "DeepSeek-V3.1")

# ==========================================
# SYSTEM PROMPT
# ==========================================
CODER_SYSTEM_PROMPT = """
You are an elite Cybersecurity Researcher and Python Expert specialized in CTF challenges.
Your goal is to write a ROBUST, EXECUTABLE Python script based on the user's instructions and provided reference files.

### CRITICAL CONTEXT NOTE:
The calling agent (CTF Agent) and you (Coder) DO NOT SHARE CONTEXT.
Therefore, the `instruction` field MUST BE SELF-CONTAINED and include:
- Full problem description
- Target environment (IP/port/architecture/binary name, if applicable)
- Vulnerability details (e.g., type, offset, gadgets, format string location)
- Expected behavior of the script
- Any assumptions or constraints

DO NOT assume prior knowledge. If key info is missing, make minimal safe assumptions and document them.

### PROCESS:
1. **Analyze**: You MAY output reasoning, dependency analysis, or attack vector logic first.
2. **Code**: You MUST wrap the FINAL Python script in a Markdown code block: ```python ... ```.
   → The tool extracts ONLY this block. Ensure it is complete and executable.

### CODE STANDARDS:
- **Robustness**: Handle network (socket/requests), I/O, and subprocess errors gracefully.
- **Generalization**: Use `argparse` for IP/port/file/path arguments. Avoid hardcoding unless explicitly requested.
- **Style**: PEP8 compliant.
- **Completeness**: The code must be a full script: imports + logic + `if __name__ == "__main__":` block.

### OUTPUT RULE:
Only the content inside the LAST ```python block will be saved to file.
All reasoning outside the code block is for your internal use and will be discarded.
"""

def _get_stdin_content():
    """Read from stdin if available (e.g., via heredoc)."""
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return None

def _read_related_files(file_paths):
    """Read content of related/reference files for context enrichment."""
    buffer = []
    for pattern in file_paths or []:
        expanded = glob.glob(os.path.expanduser(pattern))
        if not expanded:
            sys.stderr.write(f"[Warning] No matching files for pattern: {pattern}\n")
            continue
        for fpath in expanded:
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    buffer.append(f"--- BEGIN RELATED FILE: {fpath} ---\n{content}\n--- END RELATED FILE ---\n")
            except Exception as e:
                sys.stderr.write(f"[Warning] Failed to read {fpath}: {e}\n")
    return "\n".join(buffer)

def _extract_code_block(text: str) -> str:
    """
    Extracts the LAST Python code block (```python or ```) from LLM output.
    Fallback: If no block found, search for first logical start (import/def/class/#!).
    """
    matches = re.findall(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    if matches:
        return matches[-1].strip()
    # Fallback heuristic
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("import ", "from ", "#!", "def ", "class ", "async def ")):
            return "\n".join(lines[i:])
    return text.strip()

def _call_coder_api(instruction: str, related_content: str, target_path: str) -> str:
    if not API_KEY:
        raise ValueError("Environment variable CODER_API_KEY is not set.")
    if not API_URL:
        raise ValueError("Environment variable CODER_API_URL is not set.")

    # Build user prompt
    parts = [f"Task: Generate a Python script to be saved at: '{target_path}'."]
    if related_content:
        parts.append("\n### RELATED/REFERENCE FILES:\n" + related_content)
    parts.append(f"\n### INSTRUCTION (SELF-CONTAINED CONTEXT):\n{instruction}")
    user_prompt = "\n".join(parts)

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": CODER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 10000
    }

    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        data = response.json()
        if 'choices' in data and data['choices']:
            return data['choices'][0]['message']['content']
        raise ValueError(f"Unexpected API response: {data}")
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"API request failed: {e}")
    except Exception as e:
        raise RuntimeError(f"Unexpected error: {e}")

def coder_writer(target_path: str, context_instruction: str, related_files: list = None):
    """
    signature: coder_writer <path> [context_instruction] [--related-files <file> ...]
    description: Invokes an expert AI programmer to generate, rewrite, or fix Python code.
    arguments:
        path (required): Output file path (e.g., `./exp.py`, `./utils/pwnlib_compat.py`).
        context_instruction (required): A complete, self-sufficient natural-language specification. 
        --related-files / -r (optional): 
            Paths to files that help the coder understand the task (e.g., `vuln.c`, `disasm.txt`, `config.yaml`).
            Supports globs: `-r ./src/*.py`. Files are read and appended to the prompt.
            These are *hints*, not substitutes for missing context in `context_instruction`.
    usage:
        - The CTF Agent and Coder operate in isolation. Do NOT expect the coder to "remember" prior interactions. Therefore, `context_instruction` MUST contain ALL necessary information
        - If the task depends on dynamic state (e.g., leaked addresses), include them in `context_instruction`.
        - The coder outputs reasoning + code; only the final ```python block is saved.
        
        examples:

        1. [Exploit Dev] Write a format-string exploit for a remote service:
           coder_writer ./fmt_exp.py -r ./binary -r ./leak_output.txt <<'EOF'
           Target: nc 10.10.10.5 9999 (x86_64, Ubuntu 20.04, ASLR on)
           Binary: ./binary (PIE disabled, partial RELRO, no canary)
           Vulnerability: In main(), printf(user_input) → format string bug.
           Goal: Leak libc address via %15$p, then overwrite GOT of puts with system.
           Constraints: Must work with Python 3.10+, no external dependencies beyond pwnlib/socket.
           EOF

        2. [Tooling] Generate a reliable TCP health checker:
           coder_writer ./check.py "Write a script that tests if port 80 is open on given host. Use socket, 2s timeout. Return exit code 0/1."

        3. [Patch & Test] Fix and validate a vulnerable function:
           coder_writer ./fixed_login.py -r ./app.py <<'EOF'
           Refactor `login(db, username, password)` in app.py to prevent SQL injection.
           Use parameterized queries (sqlite3).
           Add a `--test` flag that runs 3 test cases (valid, invalid, SQLi payload).
           EOF
    """
    target_path = os.path.expanduser(target_path)

    # Read related files (optional)
    related_content = ""
    if related_files:
        sys.stdout.write(f"[Agent] Loading {len(related_files)} related files for context...\n")
        related_content = _read_related_files(related_files)

    # Call LLM
    #sys.stdout.write(f"[Agent] Requesting Coder LLM for '{target_path}'...\n")
    try:
        full_response = _call_coder_api(context_instruction, related_content, target_path)
    except Exception as e:
        sys.stderr.write(f"[ERROR] Coder API call failed: {e}\n")
        sys.exit(1)

    # Extract code
    code = _extract_code_block(full_response)
    if not code.strip():
        sys.stderr.write("[ERROR] No valid Python code block found in LLM response.\n")
        sys.stderr.write(f"Response preview: {full_response[:300]}...\n")
        sys.exit(2)

    # Write to file
    out_dir = os.path.dirname(target_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    try:
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(code)
    except OSError as e:
        sys.stderr.write(f"[ERROR] Failed to write '{target_path}': {e}\n")
        sys.exit(1)

    # Report
    sys.stdout.write(f"[✓] Successfully wrote '{target_path}' ({len(code)} chars).\n")
    sys.stdout.write("<<GENERATED CODE>>\n")
    for i, line in enumerate(code.splitlines(), 1):
        sys.stdout.write(f"{i:3d} | {line}\n")
    sys.stdout.write("<<END>>\n")

# =============== CLI ===============
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AI Coder Agent: Generates self-contained, robust Python scripts for security tasks.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("path", help="Output file path (e.g., ./exploit.py).")
    parser.add_argument(
        "context_instruction", 
        nargs="?", 
        help="Self-contained instruction with full context. If omitted, reads from stdin."
    )
    parser.add_argument(
        "-r", "--related-files",
        action="append",
        help="Optional: Paths to related/reference files (source, config, etc.).\n"
             "Supports globs (e.g., -r ./src/*.c). Used to enrich coder's context."
    )

    args = parser.parse_args()

    # Resolve instruction: argument > stdin > error
    instr = args.context_instruction
    if not instr:
        instr = _get_stdin_content()
    if not instr:
        parser.print_help(sys.stderr)
        sys.stderr.write("\n[ERROR] Missing self-contained context_instruction (arg or stdin).\n")
        sys.exit(1)

    try:
        coder_writer(args.path, instr.strip(), args.related_files)
    except Exception as e:
        sys.stderr.write(f"[FATAL] {type(e).__name__}: {e}\n")
        sys.exit(2)