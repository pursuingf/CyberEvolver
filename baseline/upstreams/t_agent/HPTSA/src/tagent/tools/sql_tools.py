from agents import RunContextWrapper, function_tool

from typing import Optional
import subprocess

from tagent.agent_dependencies import AgentDeps

@function_tool
def sqlmap_tool(run_context: RunContextWrapper[AgentDeps], url: str, dbms: Optional[str], technique: Optional[str], dump: Optional[bool], threads: Optional[int], level: Optional[int], risk: Optional[int]) -> str:
    """
    Runs sqlmap with the given parameters.

    Args:
        url (str): The target URL for the sqlmap command.
        dbms (Optional[str]): Specifies the target database management system (e.g., MySQL, PostgreSQL, MSSQL). If not provided, sqlmap will attempt to detect it automatically.
        technique (Optional[str]): Defines the specific SQL injection techniques to use (e.g., 'B', 'U', 'T', or a combination). If not set, sqlmap will use all available techniques.
        dump (Optional[bool]): Indicates whether to dump database data. Set to True to extract data if a vulnerability is found. Defaults to False.
        threads (Optional[int]): Number of threads to use for faster execution. Higher values can speed up the process but may cause instability or detection by the target system.
        level (Optional[int]): Sets the level of testing. Higher levels increase the number of tests performed. Valid values are 1 (default) to 5.
        risk (Optional[int]): Specifies the risk of tests to be performed. Higher risk levels may include potentially harmful tests. Valid values are 1 (default) to 3.
    """
    if not run_context.context.workspace:
        raise ValueError('Workspace not set')
    
    base_command = ['timeout', '120', 'sqlmap', '-u', url, '--batch', '-v 0', '--forms', '--crawl=2']

    if dbms:
        base_command += ['--dbms', dbms]
    if technique:
        base_command += ['--technique', technique]
    if dump:
        base_command += ['--dump']
    if threads:
        base_command += ['--threads', str(threads)]
    if level:
        base_command += ['--level', str(level)]
    if risk:
        base_command += ['--risk', str(risk)]

    result = subprocess.run(
        base_command,
        capture_output=True,
        encoding='utf-8',
        cwd=run_context.context.workspace,
    )
    if result.returncode != 0:
        return f"Error running sqlmap: {result.stderr}"
    return result.stdout