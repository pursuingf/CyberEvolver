from agents import RunContextWrapper, function_tool

from typing import Optional
import subprocess

from tagent.agent_dependencies import AgentDeps


@function_tool
def zap_baseline_scan(
    ctx: RunContextWrapper[AgentDeps],
    target_url: str,
    report_file: Optional[str],
    config_file: Optional[str],
    timeout_seconds: Optional[int],
    extra_args: Optional[str],
) -> str:
    """
    Run an OWASP ZAP baseline scan against a target URL.

    This uses the official ZAP baseline scan script (zap-baseline.py) to
    passively scan the target for common vulnerabilities. It assumes that
    ZAP and the zap-baseline.py entrypoint are installed and available
    on the PATH, as per the ZAP project documentation.

    Args:
        target_url (str): The target URL to scan (including scheme, e.g. https://example.com).
        report_file (Optional[str]): Optional path (relative to the workspace) to write an HTML report to.
        config_file (Optional[str]): Optional ZAP configuration file to pass to zap-baseline.py (relative to the workspace).
        timeout_seconds (Optional[int]): Optional timeout (in seconds) for the scan. If not provided, a default of 600 seconds is used.
        extra_args (Optional[str]): Extra arguments to pass directly to zap-baseline.py, as a raw string (for advanced usage).
    """
    if not ctx.context.workspace:
        raise ValueError("Workspace not set")

    timeout = timeout_seconds if timeout_seconds is not None else 600

    # Base command for the ZAP baseline scan. This performs a spider + passive
    # scan and exits without actively attacking the target.
    command: list[str] = [
        "timeout",
        str(timeout),
        "zap-baseline.py",
        "-t",
        target_url,
        "-I",  # Ignore ZAP's safe mode / interactive prompts
    ]

    # If a report file is requested, write an HTML report there.
    if report_file:
        command += ["-r", report_file]

    # If the caller provides a ZAP config file, pass it through.
    if config_file:
        command += ["-c", config_file]

    # Allow advanced users (or the agent) to pass additional flags through.
    if extra_args:
        command += extra_args.split()

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            cwd=ctx.context.workspace,
        )
    except Exception as e:
        return f"Exception while running ZAP baseline scan: {e}"

    if result.returncode != 0:
        # ZAP sometimes uses non-zero exit codes to indicate findings; include both
        # stdout and stderr so the agent can interpret the results.
        return (
            "ZAP baseline scan finished with a non-zero exit code.\n\n"
            f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        )

    return result.stdout or "ZAP baseline scan completed with no output."

