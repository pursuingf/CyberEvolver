from agents import RunContextWrapper, function_tool

from pathlib import Path
import subprocess

from tagent.agent_dependencies import AgentDeps

@function_tool
def pip_install(ctx: RunContextWrapper[AgentDeps], package: str) -> str:
    """
    Installs a Python package using pip.

    Args:
        package (str): The name of the package to install.
    """

    if not ctx.context.workspace:
        raise ValueError('Workspace not set')
    result = subprocess.run(
        ['pip', 'install', package],
        capture_output=True,
        encoding='utf-8',
        cwd=ctx.context.workspace,
    )

    if result.returncode != 0:
        return "Error installing package: {result.stderr}"
    return result.stdout

@function_tool       
def python_script(ctx: RunContextWrapper[AgentDeps], code: str, args: list[str]) -> str:
    """
    Runs a Python script with the given code and arguments.

    Args:
        code (str): The Python code to run.
        args (list[str]): The arguments to pass to the script.
    """
    if not ctx.context.workspace:
        raise ValueError('Workspace not set')
    temp_file = Path(ctx.context.workspace) / 'temp_script.py'
    temp_file.write_text(code)
    result = subprocess.run(
        ['python', 'temp_script.py', *args],
        capture_output=True,
        text=True,
        encoding='utf-8',
        cwd=ctx.context.workspace,
    )
    if result.returncode != 0:
        return f"Error running script: {result.stderr}"
    return result.stdout

@function_tool
def run_bash(ctx: RunContextWrapper[AgentDeps], command: str) -> str:
    """
    Runs a bash command and returns its output.

    Args:
        command (str): The bash command to run.
    """
    
    if not ctx.context.workspace:
        raise ValueError("Workspace not set")
    
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            encoding="utf-8",
            cwd=ctx.context.workspace,
        )
    except Exception as e:
        return f"Exception occurred while running bash command: {e}"

    if result.returncode != 0:
        return f"Error running bash command: {result.stderr}"
    
    return result.stdout
