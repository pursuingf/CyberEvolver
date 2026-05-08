"""ReasoningBank CTF agent — simple ReAct loop with bash + submit_flag tools.

Architecture:
  - In-process agent (Mode B): runs inside the batch worker process.
  - Executes commands in a pre-created ctfenv container via docker_env.
  - Uses OpenAI function-calling for tool invocation.
  - Integrates ReasoningBank memory for experience storage & retrieval.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from baseline.agents.upstream_runner import (
    check_solved,
    make_result,
    resolve_target_info,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Execute a bash command inside the CTF environment container. "
                "Use this to explore files, run exploits, analyze binaries, "
                "interact with network services, etc. "
                "The command runs as root in the container with a default "
                "timeout of 120 seconds."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 120, max 300).",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_flag",
            "description": (
                "Submit a flag string to check if it is correct. "
                "Only call this when you are confident you have found the flag. "
                "The flag format is usually flag{...} or similar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flag": {
                        "type": "string",
                        "description": "The flag string to submit.",
                    },
                },
                "required": ["flag"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert CTF (Capture The Flag) player. Your goal is to solve the given \
CTF challenge and find the flag.

You have access to a Linux container environment with common CTF tools installed \
(python3, pwntools, gdb, radare2, binwalk, John the Ripper, sqlmap, nmap, etc.).

## Available Tools
- **bash**: Execute any bash command in the container.
- **submit_flag**: Submit a flag when you find it.

## Strategy
1. Start by reading the challenge description and exploring provided files.
2. Identify the challenge category and plan your approach.
3. Execute your plan step by step, observing outputs carefully.
4. When you find the flag, submit it immediately.
5. If an approach fails, try alternative methods.

## Important Notes
- Challenge files are typically in /ctf/ directory. Use `find` to locate them if unsure.
- For network challenges, use the provided host and port.
- Write scripts to automate complex exploits rather than doing everything manually.
- Read error messages carefully — they often contain hints.
- Do NOT guess flags. Only submit when you have strong evidence.
"""

_MEMORY_SECTION = """\

## Relevant Past Experience
The following are lessons learned from similar challenges you solved before. \
Use them to guide your approach, but adapt to the specifics of this challenge.

{memories}
"""

# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def _exec_bash(
    docker_env: Any,
    command: str,
    timeout: int = 120,
    _log: Optional[logging.Logger] = None,
) -> str:
    """Run a bash command in the container and return output."""
    timeout = min(max(timeout, 5), 300)
    result = docker_env.execute(command, timeout=timeout)
    output = result.get("output", "") or ""
    rc = result.get("returncode", -1)
    if rc == 124:
        output += "\n[SYSTEM] Command timed out."
    # Truncate very long outputs
    if len(output) > 50000:
        output = output[:25000] + "\n\n[SYSTEM] Output truncated (too long)...\n\n" + output[-25000:]
    return output


def _exec_submit_flag(
    flag_submitted: str,
    expected_flag: str,
) -> tuple[bool, str]:
    """Check if submitted flag matches expected flag."""
    if not expected_flag:
        # No expected flag — accept any flag-shaped string
        return True, f"Flag submitted: {flag_submitted} (no ground truth to verify)"
    if flag_submitted.strip() == expected_flag.strip():
        return True, "Correct flag!"
    # Partial match check
    if expected_flag.strip() in flag_submitted.strip():
        return True, "Correct flag! (contained in submission)"
    return False, f"Incorrect flag. Try again."


# ---------------------------------------------------------------------------
# ReasoningBank integration
# ---------------------------------------------------------------------------


def _retrieve_memories(
    challenge_description: str,
    bank: Any,
    k: int = 3,
    log_dir: Optional[str] = None,
    _log: Optional[logging.Logger] = None,
) -> tuple[str, List[Dict[str, Any]]]:
    """Retrieve relevant memories from ReasoningBank.

    Returns (formatted_text_for_prompt, raw_memories_list).
    The raw list is logged to memory_retrieved.json for auditability.
    """
    _logger = _log or logger
    try:
        memories = bank.retrieve_memories(challenge_description, k=k)
        if not memories:
            return "", []

        parts = []
        for i, mem in enumerate(memories, 1):
            distilled = mem.get("distilled_items", "")
            if isinstance(distilled, str):
                try:
                    distilled = json.loads(distilled)
                except (json.JSONDecodeError, TypeError):
                    pass
            if isinstance(distilled, list):
                for item in distilled:
                    title = item.get("title", "Lesson")
                    content = item.get("content", item.get("description", ""))
                    parts.append(f"### {i}. {title}\n{content}")
            elif isinstance(distilled, str) and distilled:
                parts.append(f"### {i}. Past Experience\n{distilled}")

        formatted = "\n\n".join(parts)

        # Log retrieved memories
        if log_dir:
            _write_json(Path(log_dir) / "memory_retrieved.json", {
                "query": challenge_description[:500],
                "k": k,
                "num_results": len(memories),
                "memories": memories,
                "formatted_text": formatted,
            })

        return formatted, memories
    except Exception as exc:
        _logger.warning("ReasoningBank retrieve failed: %s", exc)
        return "", []


def _store_experience(
    trajectory: str,
    query: str,
    bank: Any,
    solved: bool = False,
    log_dir: Optional[str] = None,
    _log: Optional[logging.Logger] = None,
) -> None:
    """Store a completed trajectory into ReasoningBank.

    Logs the stored trajectory and any distilled items to memory_stored.json.
    """
    _logger = _log or logger
    try:
        result = bank.add_experience(trajectory, query)
        _logger.info("Stored experience in ReasoningBank (solved=%s)", solved)

        # Log what was stored
        if log_dir:
            log_data: Dict[str, Any] = {
                "query": query[:500],
                "trajectory_length": len(trajectory),
                "solved": solved,
            }
            # Try to extract distilled items from the result
            if isinstance(result, dict):
                log_data["distilled"] = result
            elif result is not None:
                log_data["result"] = str(result)[:2000]
            _write_json(Path(log_dir) / "memory_stored.json", log_data)

    except Exception as exc:
        _logger.warning("ReasoningBank store failed: %s", exc)
        if log_dir:
            _write_json(Path(log_dir) / "memory_stored.json", {
                "error": str(exc),
                "query": query[:500],
                "trajectory_length": len(trajectory),
                "solved": solved,
            })


def _write_json(path: Path, data: Any) -> None:
    """Write JSON to file, creating parent dirs."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------


def _build_initial_prompt(chal_data: dict, target: dict) -> str:
    """Build the user message describing the challenge."""
    category = chal_data.get("category", "misc")
    chal_id = chal_data.get("id", "unknown")
    description = chal_data.get("description", "")
    files = chal_data.get("files", [])

    # Replace target placeholders in description
    for old, new in [
        ("{box}", target["container_host"]),
        ("{port}", str(target["inner_port"])),
        ("{{box}}", target["container_host"]),
        ("{{port}}", str(target["inner_port"])),
    ]:
        description = description.replace(old, new)

    lines = [
        f"# CTF Challenge: {chal_id}",
        f"**Category**: {category}",
        f"**Description**: {description}",
    ]

    if target.get("inner_port"):
        host = target["container_host"]
        port = target["inner_port"]
        if target.get("server_type") == "web":
            lines.append(f"**Target**: http://{host}:{port}")
        else:
            lines.append(f"**Target**: nc {host} {port}")

    if files:
        lines.append(f"**Files**: {', '.join(files)}")
        lines.append("Files are located in the `/ctf/` directory. Use `find /ctf -name '<filename>'` to locate them.")

    lines.append("\nSolve this challenge and find the flag.")
    return "\n".join(lines)


def _parse_tool_call(tc: Dict[str, Any]) -> tuple[str, Dict[str, Any], str]:
    """Extract (name, args, call_id) from a tool call dict."""
    func = tc.get("function", {}) or {}
    name = func.get("name", "")
    try:
        args = json.loads(func.get("arguments", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        args = {}
    call_id = tc.get("id", "")
    return name, args, call_id


def run_challenge(
    chal_data: dict,
    docker_env: Any,
    llm_stub: Any,
    logger_instance: Optional[logging.Logger] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Run a CTF challenge using ReAct agent with optional ReasoningBank memory."""
    _log = logger_instance or logger
    start_time = time.time()

    step_limit = int(kwargs.get("step_limit", 30))
    command_timeout = int(kwargs.get("command_timeout", 120))
    log_dir = kwargs.get("log_dir")
    reasoning_bank = kwargs.get("reasoning_bank")

    target = resolve_target_info(chal_data)
    expected_flag = chal_data.get("flag", "")
    chal_id = chal_data.get("id", "unknown")

    tokens = {"input": 0, "output": 0, "total": 0}
    solved = False
    found_flag: Optional[str] = None
    error: Optional[str] = None
    step = 0

    # Build system prompt
    system_prompt = _SYSTEM_PROMPT

    # Retrieve memories if ReasoningBank is available
    if reasoning_bank is not None:
        challenge_desc = chal_data.get("description", "") + " " + chal_data.get("category", "")
        memories_text, raw_memories = _retrieve_memories(
            challenge_desc, reasoning_bank, k=3, log_dir=log_dir, _log=_log,
        )
        if memories_text:
            system_prompt += _MEMORY_SECTION.format(memories=memories_text)
            _log.info("Injected %d chars of ReasoningBank memories (%d items)",
                      len(memories_text), len(raw_memories))

    # Build initial messages
    user_prompt = _build_initial_prompt(chal_data, target)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # Trajectory log for ReasoningBank storage
    trajectory_parts: List[str] = [f"Challenge: {chal_id}\n{user_prompt}\n"]

    try:
        for step in range(1, step_limit + 1):
            # Call LLM
            try:
                resp = llm_stub.invoke(messages, tools=TOOLS, tool_choice="auto")
            except Exception as exc:
                _log.error("LLM invoke failed at step %d: %s", step, exc)
                error = f"LLM error: {exc}"
                break

            # Track tokens
            usage = resp.usage_metadata if hasattr(resp, "usage_metadata") else {}
            if isinstance(usage, dict):
                tokens["input"] += int(usage.get("input_tokens", 0) or 0)
                tokens["output"] += int(usage.get("output_tokens", 0) or 0)
                tokens["total"] += int(usage.get("total_tokens", 0) or 0)

            content = resp.content or ""
            tool_calls = resp.tool_calls or []

            if not tool_calls:
                # No tool call — LLM is just thinking or done
                messages.append({"role": "assistant", "content": content})
                trajectory_parts.append(f"[Think] {content}\n")

                # Check if the model found a flag in its text
                flag_solved, flag_found = check_solved(content, expected_flag)
                if flag_solved and flag_found:
                    solved = True
                    found_flag = flag_found
                    _log.info("Flag found in text at step %d: %s", step, found_flag)
                    break
                continue

            # Process tool calls
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })
            if content:
                trajectory_parts.append(f"[Think] {content}\n")

            for tc in tool_calls:
                name, args, call_id = _parse_tool_call(tc)
                _log.info("Step %d: tool=%s args=%s", step, name, str(args)[:200])

                if name == "bash":
                    cmd = args.get("command", "")
                    timeout = args.get("timeout", command_timeout)
                    result_text = _exec_bash(docker_env, cmd, timeout=timeout, _log=_log)
                    trajectory_parts.append(f"[Action] bash: {cmd}\n[Observation] {result_text[:2000]}\n")

                    # Log step
                    if log_dir:
                        _append_step_log(log_dir, {
                            "step": step, "tool": "bash",
                            "command": cmd, "output_len": len(result_text),
                        })

                    # Check for flag in output
                    flag_solved, flag_found = check_solved(result_text, expected_flag)
                    if flag_solved and flag_found:
                        _log.info("Flag detected in bash output at step %d", step)
                        # Don't auto-solve — let the agent submit it

                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": result_text,
                    })

                elif name == "submit_flag":
                    submitted = args.get("flag", "")
                    correct, msg = _exec_submit_flag(submitted, expected_flag)
                    trajectory_parts.append(f"[Action] submit_flag: {submitted}\n[Observation] {msg}\n")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": msg,
                    })

                    if correct:
                        solved = True
                        found_flag = submitted
                        _log.info("Flag submitted correctly at step %d: %s", step, found_flag)

                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": f"Unknown tool: {name}",
                    })

            if solved:
                break

    except Exception as exc:
        error = str(exc)
        _log.error("Agent error: %s", exc, exc_info=True)

    elapsed = time.time() - start_time

    # Build final trajectory text
    trajectory_text = "\n".join(trajectory_parts)
    if solved:
        trajectory_text += f"\n[Result] SOLVED — flag: {found_flag}\n"
    else:
        trajectory_text += f"\n[Result] FAILED — steps={step}\n"

    # Store experience in ReasoningBank
    if reasoning_bank is not None:
        _store_experience(
            trajectory_text, user_prompt, reasoning_bank,
            solved=solved, log_dir=log_dir, _log=_log,
        )

    # Write trajectory log (always, regardless of ReasoningBank)
    if log_dir:
        try:
            traj_path = Path(log_dir) / "trajectory.txt"
            traj_path.parent.mkdir(parents=True, exist_ok=True)
            traj_path.write_text(trajectory_text, encoding="utf-8")
        except OSError:
            pass

    if tokens["total"] == 0 and (tokens["input"] or tokens["output"]):
        tokens["total"] = tokens["input"] + tokens["output"]

    return make_result(
        solved=solved,
        steps_completed=step,
        elapsed_seconds=elapsed,
        tokens_total=tokens["total"],
        tokens_input=tokens["input"],
        tokens_output=tokens["output"],
        flag=found_flag,
        error=error,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _append_step_log(log_dir: Optional[str], record: Dict[str, Any]) -> None:
    if not log_dir:
        return
    try:
        path = Path(log_dir) / "steps.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
