from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Set up logging for debugging timeouts
logger = logging.getLogger(__name__)

# Tolerance for time limit checks in local evaluation
# Accounts for Docker/container overhead, file I/O, and measurement differences
# between local evaluation and official evaluation
# This ensures programs that pass official evaluation aren't incorrectly rejected locally
TIME_LIMIT_TOLERANCE = 0.5  # seconds

from pydantic import BaseModel, ConfigDict, Field
from requests.exceptions import ConnectionError, Timeout

from .. import constants
from ..code_language import (
    CodeLanguage,
    JudgeVersion,
    get_compile_command,
    get_object_file_path,
    get_run_command,
    get_submission_file_path,
)
from ..data import ProblemType
from ..result import CaseResult, JudgeResult, Profiles
from ..utils import read_svg, run_command_remote, run_command_direct, get_cache_dir
import ray


class HostPathsCompile(BaseModel):
    """Paths on the host for the compilation step of the submission."""

    model_config = ConfigDict(frozen=True)

    code_file: Path = Field(description="The code file")
    object_file: Path = Field(description="The object file")


def setup_paths_compile(
    temp_dir: Path,
    code: str,
    code_language: CodeLanguage,
    judge_version: JudgeVersion,
) -> HostPathsCompile:
    """Setup paths for the compilation step of the submission.

    Args:
        temp_dir (Path): The temporary directory.
        code (str): The code to run.
        code_language (CodeLanguage): The code language.
        judge_version (JudgeVersion): The judge version.

    Returns:
        HostPathsCompile: The paths in the compilation step for the runner tool.
    """
    code_file = temp_dir / get_submission_file_path(code_language, judge_version)
    code_file.parent.mkdir(parents=True, exist_ok=True)
    code_file.write_text(code)
    object_file = temp_dir / get_object_file_path(code_language, judge_version)
    object_file.parent.mkdir(parents=True, exist_ok=True)
    object_file.touch()
    return HostPathsCompile(code_file=code_file, object_file=object_file)


def get_compile_volumes(host_paths: HostPathsCompile, temp_dir: Path) -> dict[str, dict[str, str]]:
    """Get the volumes for the compilation command with the setup.

    Args:
        host_paths (HostPathsCompile): The paths for the runner tool.
        temp_dir (Path): The temporary directory.

    Returns:
        dict[str, dict[str, str]]: The volumes for the compile command with the setup.
    """
    return {
        str(host_paths.code_file): {
            "bind": f"{constants.WORK_DIR}/{host_paths.code_file.relative_to(temp_dir)}",
            "mode": "ro",
        },
        str(host_paths.object_file): {
            "bind": f"/tmp/{host_paths.object_file.relative_to(temp_dir)}",
            "mode": "rw",
        },
    }


def build_compile_command(
    code_language: CodeLanguage,
    judge_version: JudgeVersion,
    object_file_relative_path: Path,
) -> str:
    """Build the compile command for the given code language and judge version.

    Args:
        code_language (CodeLanguage): The code language.
        judge_version (JudgeVersion): The judge version.
        object_file_relative_path (Path): The relative path of the object file.

    Returns:
        str: The compile command.
    """
    compile_command = get_compile_command(code_language, judge_version)
    compile_command += (
        f"; cp {constants.WORK_DIR}/{object_file_relative_path} /tmp/{object_file_relative_path}"
    )
    compile_command += f"; chmod 744 /tmp/{object_file_relative_path}"
    return compile_command


class HostPathsBatchRun(BaseModel):
    """Paths on the host for the running step of the submission for batch problems."""

    model_config = ConfigDict(frozen=True)

    code_file: Path = Field(description="The code file")
    object_file: Path = Field(description="The object file")
    input_file: Path = Field(description="The input file")
    output_file: Path = Field(description="The output file")
    profiles_file: Path = Field(description="The profiles file")


def get_cached_input_file_path(problem_id: str, case_idx: int) -> Path | None:
    """Get the path to a cached input file if it exists.
    
    Args:
        problem_id: The problem ID.
        case_idx: The case index (0-based).
        
    Returns:
        Path to cached input file if it exists, None otherwise.
    """
    try:
        cache_dir = get_cache_dir() / "public_inputs_150" / f"{problem_id}_inputs"
        cached_input_file = cache_dir / f"{problem_id}_{case_idx:06d}_input.txt"
        if cached_input_file.exists() and cached_input_file.stat().st_size > 0:
            return cached_input_file
    except Exception:
        pass  # If cache lookup fails, fall back to writing file
    return None


def setup_paths_batch_run(
    host_paths_compile: HostPathsCompile,
    temp_dir: Path,
    input_str: str,
    prefix: str = "",
    problem_id: str | None = None,
    case_idx: int | None = None,
) -> HostPathsBatchRun:
    """Setup paths for the running step of the submission for batch problems.

    Args:
        host_paths_compile (HostPathsCompile): The paths in the compilation step for the runner tool.
        temp_dir (Path): The temporary directory.
        input_str (str): The input string for the problem.
        prefix (str): The prefix for the input/output/profiles files. Defaults to "".
        problem_id (str | None): The problem ID (used to find cached input files). Defaults to None.
        case_idx (int | None): The case index (used to find cached input files). Defaults to None.

    Returns:
        HostPathsBatchRun: The paths for the runner tool.
    """
    # Ensure temp_dir exists (important for Ray workers on different nodes)
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Use cached input file - must exist
    if problem_id is None or case_idx is None:
        raise RuntimeError(f"Cached input file requires problem_id and case_idx. Got problem_id={problem_id}, case_idx={case_idx}")
    
    cached_input_file = get_cached_input_file_path(problem_id, case_idx)
    if cached_input_file is None:
        cache_dir = get_cache_dir() / "public_inputs_150" / f"{problem_id}_inputs"
        expected_path = cache_dir / f"{problem_id}_{case_idx:06d}_input.txt"
        raise FileNotFoundError(
            f"Cached input file not found for problem_id={problem_id}, case_idx={case_idx}. "
            f"Expected path: {expected_path}. "
            f"Please run cache_public_inputs.py to cache the input files."
        )
    
    # Create local temp directory for input/output files to avoid NFS I/O latency
    # Use /tmp which is typically local to each node
    local_temp_dir = Path(tempfile.mkdtemp(prefix=f"ahc_local_{problem_id}_{case_idx}_", dir="/tmp"))
    local_temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy cached input file to local temp with proper synchronization
    input_file_name = constants.INPUT_FILE.split("/")[-1]
    input_file = local_temp_dir / input_file_name
    shutil.copy2(cached_input_file, input_file)
    # Force sync to ensure file is fully written
    with open(input_file, 'rb') as f:
        os.fsync(f.fileno())
    # Verify file was copied correctly
    if not input_file.exists() or input_file.stat().st_size == 0:
        raise RuntimeError(f"Failed to copy cached input file from {cached_input_file} to {input_file}")
    
    # Create output file in local temp
    output_file_name = constants.OUTPUT_FILE.split("/")[-1]
    output_file = local_temp_dir / output_file_name
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.touch()
    # Force sync to ensure file is created
    with open(output_file, 'a') as f:
        os.fsync(f.fileno())
    
    # Create profiles file in local temp
    profiles_file_name = constants.PROFILES_FILE.split("/")[-1]
    profiles_file = local_temp_dir / profiles_file_name
    profiles_file.parent.mkdir(parents=True, exist_ok=True)
    profiles_file.touch()
    # Force sync to ensure file is created
    with open(profiles_file, 'a') as f:
        os.fsync(f.fileno())
    
    return HostPathsBatchRun(
        code_file=host_paths_compile.code_file,
        object_file=host_paths_compile.object_file,
        input_file=input_file,
        output_file=output_file,
        profiles_file=profiles_file,
    )


def get_batch_run_volumes(host_paths: HostPathsBatchRun, temp_dir: Path) -> dict[str, dict[str, str]]:
    """Get the volumes for the run command with the setup.

    Args:
        host_paths (HostPathsRun): The paths for the runner tool.
        temp_dir (Path): The temporary directory.

    Returns:
        dict[str, dict[str, str]]: The volumes for the run command with the setup.
    """
    return {
        str(host_paths.code_file): {
            "bind": f"{constants.WORK_DIR}/{host_paths.code_file.relative_to(temp_dir)}",
            "mode": "ro",
        },
        str(host_paths.object_file): {
            "bind": f"{constants.WORK_DIR}/{host_paths.object_file.relative_to(temp_dir)}",
            "mode": "ro",
        },
        str(host_paths.input_file): {"bind": constants.INPUT_FILE, "mode": "ro"},
        str(host_paths.output_file): {"bind": constants.OUTPUT_FILE, "mode": "rw"},
        str(host_paths.profiles_file): {"bind": constants.PROFILES_FILE, "mode": "rw"},
    }


def build_batch_run_command(code_language: CodeLanguage, judge_version: JudgeVersion, time_limit: float) -> str:
    """Build the run command for the given code language and judge version.

    Args:
        code_language (CodeLanguage): The code language.
        judge_version (JudgeVersion): The judge version.
        problem_type (ProblemType): The problem type.
        time_limit (float): The time limit in seconds.

    Returns:
        str: The run command.
    """
    run_command = get_run_command(code_language, judge_version)
    run_command += f" < {constants.INPUT_FILE} > {constants.OUTPUT_FILE}"
    run_command = (
        "/usr/bin/time "
        f'-f "{constants.TIME_OUTPUT_FORMAT}" '
        f"-o {constants.PROFILES_FILE} {run_command}"
    )  # NOTE: We use the GNU Time to measure the resource usage
    # NOTE: the profiles by GNU Time update every 1 sec (from observations while debugging)
    time_limit_ceil = math.ceil(time_limit + 0.1)
    # Increase timeout margin significantly to account for sync overhead
    # sync happens after program execution and can take time, especially on NFS
    # We need enough margin so timeout doesn't kill before profiles file is written
    TIMEOUT_MARGIN = 1.5  # seconds - accounts for sync + GNU time file writing
    run_command = (
        f"timeout {time_limit_ceil + TIMEOUT_MARGIN} "
        f"prlimit --cpu={time_limit_ceil + 0.1} {run_command}"
    )  # NOTE: margin Wall Time: 1.5 sec (for sync + file I/O), CPU Time: 0.1+α sec
    run_command += "; sync"  # NOTE: Ensure all output is written before the container exits
    return run_command


class HostPathsBatchJudge(BaseModel):
    """Paths on the host for the judging step of the submission for batch problems."""

    model_config = ConfigDict(frozen=True)

    input_file: Path = Field(description="The input file")
    output_file: Path = Field(description="The output file")
    profiles_file: Path = Field(description="The profiles file")


def setup_paths_batch_judge(host_paths_batch_run: HostPathsBatchRun) -> HostPathsBatchJudge:
    """Setup paths for the judging step of the submission for batch problems.

    Args:
        host_paths_batch_run (HostPathsBatchRun): The paths for the runner tool.

    Returns:
        HostPathsBatchJudge: The paths for the judging step of the submission.
    """
    return HostPathsBatchJudge(
        input_file=host_paths_batch_run.input_file,
        output_file=host_paths_batch_run.output_file,
        profiles_file=host_paths_batch_run.profiles_file,
    )


def get_batch_judge_volumes(host_paths: HostPathsBatchJudge, tool_dir: Path, problem_id: str, generation_id: str | None = None) -> dict[str, dict[str, str]]:
    """Get the volumes for the judging command with the setup.

    Args:
        host_paths (HostPathsBatchJudge): The paths for the runner tool.
        tool_dir (Path): The cache directory containing tester binaries.
        problem_id (str): Problem ID to construct tester path.
        generation_id (str | None): Optional generation ID to include in logs for tracking.

    Returns:
        dict[str, dict[str, str]]: The volumes for the judging command with the setup.
    """
    # tool_dir is the cache directory, so tester is directly at tool_dir / "{problem_id}_tester"
    tester_path = (tool_dir / f"{problem_id}_tester").resolve()
    # Verify tester exists - with retry for NFS sync delays
    import time
    max_retries = 3
    for retry in range(max_retries):
        if tester_path.exists() and tester_path.is_file():
            break
        if retry < max_retries - 1:
            time.sleep(0.1)  # Brief wait for NFS sync
        else:
            # Log additional debug info
            logger.error(
                f"Tester binary not found at {tester_path} after {max_retries} attempts. "
                f"Tool directory: {tool_dir} (exists: {tool_dir.exists()}). "
                f"Parent dir exists: {tester_path.parent.exists() if tester_path.parent else False}. "
                f"Please ensure the tester binary is built and available on NFS."
            )
            raise FileNotFoundError(
                f"Tester binary not found at {tester_path}. "
                f"Tool directory: {tool_dir}. "
                f"Please ensure the tester binary is built and available on NFS."
            )
    
    # CRITICAL: Verify input and output files exist before mounting them
    # These files are created on NFS and may have sync delays
    input_file_path = Path(host_paths.input_file).resolve()
    output_file_path = Path(host_paths.output_file).resolve()
    
    # Check input file (should always exist as it's from cache)
    input_exists = False
    for retry in range(max_retries):
        if input_file_path.exists():
            input_exists = True
            break
        if retry < max_retries - 1:
            time.sleep(0.1)  # Brief wait for NFS sync
    
    # Check output file (may not exist if program terminated early)
    output_exists = False
    for retry in range(max_retries):
        if output_file_path.exists():
            output_exists = True
            break
        if retry < max_retries - 1:
            time.sleep(0.1)  # Brief wait for NFS sync
    
    # Provide clear, distinct error messages for each missing file
    if not input_exists:
        gen_prefix = f"[GEN {generation_id}] " if generation_id else ""
        logger.error(
            f"{gen_prefix}INPUT FILE NOT FOUND after {max_retries} attempts. "
            f"Input file: {input_file_path} (exists: {input_file_path.exists()}). "
            f"Parent dir exists: {input_file_path.parent.exists() if input_file_path.parent else False}. "
            f"This is a cached input file that should always exist."
        )
        raise FileNotFoundError(
            f"INPUT FILE NOT FOUND: {input_file_path}. "
            f"This is a cached input file that should always exist. "
            f"Please ensure the cache is properly set up and accessible from Ray workers."
        )
    
    if not output_exists:
        gen_prefix = f"[GEN {generation_id}] " if generation_id else ""
        logger.error(
            f"{gen_prefix}OUTPUT FILE NOT FOUND after {max_retries} attempts. "
            f"Output file: {output_file_path} (exists: {output_file_path.exists()}). "
            f"Parent dir exists: {output_file_path.parent.exists() if output_file_path.parent else False}. "
            f"This likely indicates the program terminated early or failed to write output."
        )
        raise FileNotFoundError(
            f"OUTPUT FILE NOT FOUND: {output_file_path}. "
            f"This likely indicates the program terminated early, timed out, or failed to write output. "
            f"The input file exists at: {input_file_path}"
        )
    
    return {
        str(host_paths.input_file): {"bind": constants.INPUT_FILE, "mode": "ro"},
        str(host_paths.output_file): {"bind": constants.OUTPUT_FILE, "mode": "ro"},
        str(tester_path): {
            "bind": constants.TESTER_BIN,
            "mode": "ro",
        },
    }


def build_batch_judge_command() -> str:
    """Build the judging command.

    Returns:
        str: The judging command.
    """
    judge_command = (
        f"{constants.TESTER_BIN} {constants.INPUT_FILE} {constants.OUTPUT_FILE}"
    )
    return judge_command


class HostPathsReactiveJudge(BaseModel):
    """Paths on the host for the judging step of the submission for reactive problems."""

    model_config = ConfigDict(frozen=True)

    code_file: Path = Field(description="The code file")
    object_file: Path = Field(description="The object file")
    input_file: Path = Field(description="The input file")
    output_file: Path = Field(description="The output file")
    profiles_file: Path = Field(description="The profiles file")


def setup_paths_reactive_judge(
    host_paths_compile: HostPathsCompile,
    temp_dir: Path,
    input_str: str,
    prefix: str = "",
    problem_id: str | None = None,
    case_idx: int | None = None,
) -> HostPathsReactiveJudge:
    """Setup paths for the judging step of the submission for reactive problems.

    Args:
        host_paths_compile (HostPathsCompile): The paths in the compilation step for the runner tool.
        temp_dir (Path): The temporary directory.
        input_str (str): The input string for the problem.
        prefix (str): The prefix for the input/output/profiles files. Defaults to "".
        problem_id (str | None): The problem ID (used to find cached input files). Defaults to None.
        case_idx (int | None): The case index (used to find cached input files). Defaults to None.

    Returns:
        HostPathsReactiveJudge: The paths for the runner tool.
    """
    # Ensure temp_dir exists (important for Ray workers on different nodes)
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Use cached input file - must exist
    if problem_id is None or case_idx is None:
        raise RuntimeError(f"Cached input file requires problem_id and case_idx. Got problem_id={problem_id}, case_idx={case_idx}")
    
    cached_input_file = get_cached_input_file_path(problem_id, case_idx)
    if cached_input_file is None:
        cache_dir = get_cache_dir() / "public_inputs_150" / f"{problem_id}_inputs"
        expected_path = cache_dir / f"{problem_id}_{case_idx:06d}_input.txt"
        raise FileNotFoundError(
            f"Cached input file not found for problem_id={problem_id}, case_idx={case_idx}. "
            f"Expected path: {expected_path}. "
            f"Please run cache_public_inputs.py to cache the input files."
        )
    
    # Create local temp directory for input/output files to avoid NFS I/O latency
    # Use /tmp which is typically local to each node
    local_temp_dir = Path(tempfile.mkdtemp(prefix=f"ahc_local_{problem_id}_{case_idx}_", dir="/tmp"))
    local_temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy cached input file to local temp with proper synchronization
    input_file_name = constants.INPUT_FILE.split("/")[-1]
    input_file = local_temp_dir / input_file_name
    shutil.copy2(cached_input_file, input_file)
    # Force sync to ensure file is fully written
    with open(input_file, 'rb') as f:
        os.fsync(f.fileno())
    # Verify file was copied correctly
    if not input_file.exists() or input_file.stat().st_size == 0:
        raise RuntimeError(f"Failed to copy cached input file from {cached_input_file} to {input_file}")
    
    # Create output file in local temp
    output_file_name = constants.OUTPUT_FILE.split("/")[-1]
    output_file = local_temp_dir / output_file_name
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.touch()
    # Force sync to ensure file is created
    with open(output_file, 'a') as f:
        os.fsync(f.fileno())
    
    # Create profiles file in local temp
    profiles_file_name = constants.PROFILES_FILE.split("/")[-1]
    profiles_file = local_temp_dir / profiles_file_name
    profiles_file.parent.mkdir(parents=True, exist_ok=True)
    profiles_file.touch()
    # Force sync to ensure file is created
    with open(profiles_file, 'a') as f:
        os.fsync(f.fileno())
    
    return HostPathsReactiveJudge(
        code_file=host_paths_compile.code_file,
        object_file=host_paths_compile.object_file,
        input_file=input_file,
        output_file=output_file,
        profiles_file=profiles_file,
    )


def get_reactive_judge_volumes(
    host_paths: HostPathsReactiveJudge, temp_dir: Path, tool_dir: Path, problem_id: str
) -> dict[str, dict[str, str]]:
    """Get the volumes for the run command with the setup.

    Args:
        host_paths (HostPathsReactiveJudge): The paths for the runner tool.
        temp_dir (Path): The temporary directory.
        tool_dir (Path): The cache directory containing tester binaries.
        problem_id (str): Problem ID to construct tester path.

    Returns:
        dict[str, dict[str, str]]: The volumes for the run command with the setup.
    """
    # tool_dir is the cache directory, so tester is directly at tool_dir / "{problem_id}_tester"
    tester_path = (tool_dir / f"{problem_id}_tester").resolve()
    # Verify tester exists - with retry for NFS sync delays
    import time
    max_retries = 3
    for retry in range(max_retries):
        if tester_path.exists() and tester_path.is_file():
            break
        if retry < max_retries - 1:
            time.sleep(0.1)  # Brief wait for NFS sync
        else:
            # Log additional debug info
            logger.error(
                f"Tester binary not found at {tester_path} after {max_retries} attempts. "
                f"Tool directory: {tool_dir} (exists: {tool_dir.exists()}). "
                f"Parent dir exists: {tester_path.parent.exists() if tester_path.parent else False}. "
                f"Please ensure the tester binary is built and available on NFS."
            )
            raise FileNotFoundError(
                f"Tester binary not found at {tester_path}. "
                f"Tool directory: {tool_dir}. "
                f"Please ensure the tester binary is built and available on NFS."
            )
    return {
        str(host_paths.code_file): {
            "bind": f"{constants.WORK_DIR}/{host_paths.code_file.relative_to(temp_dir)}",
            "mode": "ro",
        },
        str(host_paths.object_file): {
            "bind": f"{constants.WORK_DIR}/{host_paths.object_file.relative_to(temp_dir)}",
            "mode": "ro",
        },
        str(host_paths.input_file): {"bind": constants.INPUT_FILE, "mode": "ro"},
        str(host_paths.output_file): {"bind": constants.OUTPUT_FILE, "mode": "rw"},
        str(host_paths.profiles_file): {"bind": constants.PROFILES_FILE, "mode": "rw"},
        str(tester_path): {
            "bind": constants.TESTER_BIN,
            "mode": "ro",
        },
    }


def build_reactive_judge_command(code_language: CodeLanguage, judge_version: JudgeVersion, time_limit: float) -> str:
    """Build the run command for the given code language and judge version.

    Args:
        code_language (CodeLanguage): The code language.
        judge_version (JudgeVersion): The judge version.
        time_limit (float): The time limit in seconds.

    Returns:
        str: The run command.
    """
    run_command = get_run_command(code_language, judge_version)
    run_command += f" < {constants.INPUT_FILE} > {constants.OUTPUT_FILE}"
    run_command = (
        f"{constants.TESTER_BIN} /usr/bin/time "
        f'-f "{constants.TIME_OUTPUT_FORMAT}" '
        f"-o {constants.PROFILES_FILE} {run_command}"
    )  # NOTE: We use the GNU Time to measure the resource usage
    # NOTE: the profiles by GNU Time update every 1 sec (from observations while debugging)
    time_limit_ceil = math.ceil(time_limit + 0.1)
    # Increase timeout margin significantly to account for sync overhead
    # sync happens after program execution and can take time, especially on NFS
    # We need enough margin so timeout doesn't kill before profiles file is written
    TIMEOUT_MARGIN = 1.5  # seconds - accounts for sync + GNU time file writing
    run_command = (
        f"timeout {time_limit_ceil + TIMEOUT_MARGIN} "
        f"prlimit --cpu={time_limit_ceil + 0.1} {run_command}"
    )  # NOTE: margin Wall Time: 1.5 sec (for sync + file I/O), CPU Time: 0.1+α sec
    run_command += "; sync"  # NOTE: Ensure all output is written before the container exits
    return run_command


class HostPathsVis(BaseModel):
    """Paths on the host for the visualization step of the judge."""

    model_config = ConfigDict(frozen=True)

    input_file: Path = Field(description="The input file")
    output_file: Path = Field(description="The output file")
    local_visualization_file: Path = Field(description="The local visualization file")


def setup_paths_vis(
    host_paths_judge: HostPathsBatchJudge | HostPathsReactiveJudge, temp_dir: Path, problem_id: str, prefix: str = ""
) -> HostPathsVis:
    """Setup paths for the visualization step of the judge.

    Args:
        host_paths_run (HostPathsBatchRun | HostPathsReactiveRun): The paths for the judge.
        temp_dir (Path): The temporary directory.
        problem_id (str): The problem ID.
        prefix (str): The prefix for the local visualization file. Defaults to "".

    Returns:
        HostPathsVis: The paths for the visualization step of the judge.
    """
    local_visualization_container = (
        constants.LOCAL_VIS_SVG
        if problem_id in constants.VIS_SVG_GENERATION
        else constants.LOCAL_VIS_HTML
    )
    local_visualization_ext = local_visualization_container.rsplit(".", 1)[1]
    local_visualization_file = temp_dir / f"{prefix}local_visualization.{local_visualization_ext}"
    local_visualization_file.touch()
    return HostPathsVis(
        input_file=host_paths_judge.input_file,
        output_file=host_paths_judge.output_file,
        local_visualization_file=local_visualization_file,
    )


def get_vis_volumes(host_paths: HostPathsVis, tool_dir: Path) -> dict[str, dict[str, str]]:
    """Get the volumes for the visualization command with the setup.

    Args:
        host_paths (HostPathsVis): The paths for the runner tool.
        tool_dir (Path): The directory of the tools.

    Returns:
        dict[str, dict[str, str]]: The volumes for the visualization command with the setup.

    Raises:
        ValueError: If the local visualization file does not have a valid extension.
    """
    if host_paths.local_visualization_file.suffix == ".svg":
        local_visualization_container = constants.LOCAL_VIS_SVG
    elif host_paths.local_visualization_file.suffix == ".html":
        local_visualization_container = constants.LOCAL_VIS_HTML
    else:
        raise ValueError("The local visualization file must have either .svg or .html extension.")
    vis_volumes = {
        str((tool_dir / "tools" / "target" / "release" / "vis").resolve()): {
            "bind": constants.VIS_BIN,
            "mode": "ro",
        },
        str(host_paths.input_file): {"bind": constants.INPUT_FILE, "mode": "ro"},
        str(host_paths.output_file): {"bind": constants.OUTPUT_FILE, "mode": "ro"},
        str(host_paths.local_visualization_file): {"bind": local_visualization_container, "mode": "rw"},
    }
    return vis_volumes


def build_vis_command() -> str:
    """Build the visualization command.

    Returns:
        str: The visualization command.
    """
    vis_command = f"{constants.VIS_BIN} {constants.INPUT_FILE} {constants.OUTPUT_FILE}"
    return vis_command


def run_compile_container(
    code_language: CodeLanguage,
    judge_version: JudgeVersion,
    host_paths_compile: HostPathsCompile,
    compile_volumes: dict[str, dict[str, str]],
    compile_command: str,
) -> CaseResult | None:
    """Run the compile command in a ray process.

    Args:
        code_language (CodeLanguage): The code language.
        judge_version (JudgeVersion): The judge version.
        host_paths_compile (ostPathsCompile): The paths for the runner tool in the compilation step.
        compile_volumes (dict[str, dict[str, str]]): The volumes for the compile command with the setup.
        compile_command (str): The compile command.

    Returns:
        CaseResult | None: The case result if the compilation fails, otherwise None.
    """
    try:
        exit_code, stdout, stderr = run_command_direct(
            command=compile_command,
            volumes=compile_volumes,
            working_dir=constants.WORK_DIR,
            timeout=constants.COMPILE_TIMEOUT,
            mem_limit=constants.MAX_MEMORY_LIMIT,
        )
        stderr = stderr.strip()
    except Exception as e:
        if code_language != CodeLanguage.PYTHON:
            return CaseResult(
                input_str=None,
                output_str=None,
                error_str=None,
                judge_result=JudgeResult.COMPILATION_ERROR,
                message=f"Compilation timed out or failed: {str(e)}",
                absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
                execution_time=0.0,
                memory_usage=0,
            )
        exit_code = 1
        stderr = str(e)
    object_size = host_paths_compile.object_file.stat().st_size
    if any(
        [
            exit_code != 0,
            # NOTE: As for Python, it is fine if .pyc file is not created during the compilation step.
            code_language != CodeLanguage.PYTHON and object_size == 0,
            # NOTE: We regard SyntaxError as a compilation error for Python
            code_language == CodeLanguage.PYTHON and "SyntaxError" in stderr,
        ]
    ):
        return CaseResult(
            input_str=None,
            output_str=None,
            error_str=None,
            judge_result=JudgeResult.COMPILATION_ERROR,
            message=f"Failed to compile the code.\nStandard error:\n{stderr}",
            absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
            execution_time=0.0,
            memory_usage=0,
        )
    return None  # Compilation succeeded, return None to indicate success


def run_batch_run_container(
    code_language: CodeLanguage,
    judge_version: JudgeVersion,
    time_limit: float,
    run_volumes: dict[str, dict[str, str]],
    run_command: str,
    input_str: str | None,
) -> CaseResult | tuple[float, str]:
    """Run the run command in a ray process for batch problems.

    Args:
        code_language (CodeLanguage): The code language.
        judge_version (JudgeVersion): The judge version.
        time_limit (float): The time limit in seconds.
        run_volumes (dict[str, dict[str, str]]): The volumes for the run command with the setup.
        run_command (str): The run command.
        input_str (str | None): The input string of the problem included in the case result.

    Returns:
        CaseResult | tuple[float, str]:
            The case result if the run fails, otherwise the execution time in seconds and the standard error.
    """
    start_at = time.perf_counter()
    exit_code, stdout, stderr = run_command_direct(
        command=run_command,
        volumes=run_volumes,
        working_dir=constants.WORK_DIR,
        timeout=None,  # NOTE: timeout is handled by the timeout command in run_command
        mem_limit=constants.MAX_MEMORY_LIMIT,
    )
    end_at = time.perf_counter()
    execution_time_host = end_at - start_at  # NOTE: we use this wall time for `RE` (including the overhead)
    stderr = stderr.strip()
    if exit_code != 0:
        if execution_time_host > time_limit + TIME_LIMIT_TOLERANCE:  # Killed by `timeout` command (with tolerance)
            return CaseResult(
                input_str=input_str,
                output_str=None,
                error_str=stderr if input_str is not None else None,
                judge_result=JudgeResult.TIME_LIMIT_EXCEEDED,
                message=f"Program timed out. {execution_time_host} > {time_limit + TIME_LIMIT_TOLERANCE}",
                absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
                execution_time=min(execution_time_host, time_limit + 0.1),  # NOTE: slight longer than time limit
                memory_usage=0,
            )
        else:
            return CaseResult(
                input_str=input_str,
                output_str=None,
                error_str=stderr if input_str is not None else None,
                judge_result=JudgeResult.RUNTIME_ERROR,
                message="Runtime error.",
                absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
                execution_time=execution_time_host,
                memory_usage=0,
            )
    return execution_time_host, stderr  # Run succeeded, return the execution time and stderr


def run_batch_judge_container(
    judge_volumes: dict[str, dict[str, str]],
    judge_command: str,
    execution_time_host: float,
    input_str: str | None,
    output_str: str | None,
    error_str: str | None,
) -> CaseResult | int:
    """Run the run command in a ray process for batch problems.

    Args:
        judge_volumes (dict[str, dict[str, str]]): The volumes for the judge command with the setup.
        judge_command (str): The judge command.
        execution_time_host (float): The execution time on the host in seconds.
        input_str (str | None): The input string of the problem included in the case result.
        output_str (str | None): The output string of the problem included in the case result.
        error_str (str | None): The error string of the problem included in the case result.

    Returns:
        CaseResult | int: The case result if the judge fails, otherwise the score.
    """
    judge_start = time.perf_counter()
    exit_code, stdout, stderr = run_command_direct(
        command=judge_command,
        volumes=judge_volumes,
        working_dir=constants.WORK_DIR,
        timeout=None,  # NOTE: timeout is handled by the timeout command in judge_command
        mem_limit=constants.MAX_MEMORY_LIMIT,
    )
    judge_duration = time.perf_counter() - judge_start
    # Only log if judge is slow (potential timeout issue)
    if judge_duration > 10.0:
        logger.warning(f"[JUDGE] Slow judge: {judge_duration:.2f}s, exit_code={exit_code}")
    stderr = stderr.strip()
    if exit_code != 0:
        return CaseResult(
            input_str=input_str,
            output_str=output_str,
            error_str=error_str,
            judge_result=JudgeResult.WRONG_ANSWER,
            message=f"Wrong answer.\nStandard error:\n{stderr}",
            absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
            execution_time=execution_time_host,
            memory_usage=0,
        )
    if "wrong answer: " in stderr:
        error_message = stderr.split("wrong answer: ")[1]
        return CaseResult(
            input_str=input_str,
            output_str=output_str,
            error_str=error_str,
            judge_result=JudgeResult.WRONG_ANSWER,
            message=f"Wrong answer.\n{error_message}",
            absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
            execution_time=execution_time_host,
            memory_usage=0,
        )
    stderr_last_line = stderr.splitlines()[-1]
    score_match = re.match(r"Score = (\d+)", stderr_last_line)
    if score_match is None:
        return CaseResult(
            input_str=input_str,
            output_str=output_str,
            error_str=error_str,
            judge_result=JudgeResult.WRONG_ANSWER,
            message=f"Wrong answer.\nStandard error:\n{stderr}",
            absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
            execution_time=execution_time_host,
            memory_usage=0,
        )
    score = int(score_match.group(1))
    return score  # Return the score as an integer


def run_reactive_judge_container(
    code_language: CodeLanguage,
    judge_version: JudgeVersion,
    time_limit: float,
    judge_volumes: dict[str, dict[str, str]],
    judge_command: str,
    input_str: str | None,
    output_file_path: Path | None,
) -> CaseResult | tuple[float, int, str]:
    """Run the run command in a ray process for batch problems.

    Args:
        code_language (CodeLanguage): The code language.
        judge_version (JudgeVersion): The judge version.
        time_limit (float): The time limit in seconds.
        judge_volumes (dict[str, dict[str, str]]): The volumes for the judge command with the setup.
        judge_command (str): The judge command.
        input_str (str | None): The input string of the problem included in the case result.
        output_file_path (Path | None): The path to the output file. If None, contents of the output file is not used.

    Returns:
        CaseResult | tuple[float, int]: The case result if the run fails,
            otherwise the execution time in seconds, the score and the standard error.
    """
    start_at = time.perf_counter()
    exit_code, stdout, stderr = run_command_direct(
        command=judge_command,
        volumes=judge_volumes,
        working_dir=constants.WORK_DIR,
        timeout=None,  # NOTE: timeout is handled by the timeout command in judge_command
        mem_limit=constants.MAX_MEMORY_LIMIT,
    )
    end_at = time.perf_counter()
    execution_time_host = end_at - start_at  # NOTE: we use this wall time for `RE` (including the overhead)
    stderr = stderr.strip()
    if exit_code != 0 or stderr == "":
        if execution_time_host > time_limit + TIME_LIMIT_TOLERANCE:  # Killed by `timeout` command (with tolerance)
            return CaseResult(
                input_str=input_str,
                output_str=None,
                error_str=stderr if input_str is not None else None,
                judge_result=JudgeResult.TIME_LIMIT_EXCEEDED,
                message=f"Program timed out. {execution_time_host} > {time_limit + TIME_LIMIT_TOLERANCE}",
                absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
                execution_time=min(execution_time_host, time_limit + 0.1),  # NOTE: slight longer than time limit
                memory_usage=0,
            )
        else:
            return CaseResult(
                input_str=input_str,
                output_str=None,
                error_str=stderr if input_str is not None else None,
                judge_result=JudgeResult.RUNTIME_ERROR,
                message="Runtime error.",
                absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
                execution_time=execution_time_host,
                memory_usage=0,
            )
    stderr_last_line = stderr.splitlines()[-1]
    score_match = re.match(r"Score = (\d+)", stderr_last_line)
    if score_match is None:
        return CaseResult(
            input_str=input_str,
            output_str=output_file_path.read_text() if output_file_path else None,
            error_str=stderr if input_str is not None else None,
            judge_result=JudgeResult.WRONG_ANSWER,
            message="Wrong answer.",  # NOTE: exclude stderr because we don't want to be exploited by the user
            absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
            execution_time=execution_time_host,
            memory_usage=0,
        )
    score = int(score_match.group(1))
    return (execution_time_host, score, stderr)  # Run succeeded, return the execution time


def run_vis_container(vis_command: str, vis_volumes: dict[str, dict[str, str]]) -> None:
    """Run the visualization command in a ray process.

    Args:
        vis_command (str): The visualization command.
        vis_volumes (dict[str, dict[str, str]]): The volumes for the visualization command with the setup.
    """
    try:
        exit_code, stdout, stderr = run_command_direct(
            command=vis_command,
            volumes=vis_volumes,
            working_dir=constants.WORK_DIR,
            timeout=constants.VISUALIZE_TIMEOUT,
            mem_limit=constants.MAX_MEMORY_LIMIT,
        )
    except Exception:
        raise RuntimeError("Timeout while running the visualization command. Something went wrong.")
    if exit_code != 0:
        raise RuntimeError("Failed to run the visualization command. Something went wrong.")


def parse_profiles(
    time_limit: float,
    memory_limit: int,
    profiles_content: str,
    execution_time_host: float,
    input_str: str | None,
    output_str: str | None,
    error_str: str | None,
) -> CaseResult | tuple[float, int]:
    """
    Parse the profiles content and check for time limit, memory limit, and exit status.

    Args:
        time_limit (float): The time limit in seconds.
        memory_limit (int): The memory limit in bytes.
        profiles_content (str): The content of the profiles file.
        execution_time_host (float): The execution time on the host in seconds.
        input_str (str | None): The input string of the problem included in the case result.
        output_str (str | None): The output string of the problem included in the case result.
        error_str (str | None): The error string of the problem included in the case result.

    Returns:
        CaseResult | tuple[float, int]: The case result if there is an error, otherwise (execution_time, memory_usage).
    """
    assert execution_time_host >= 0.0, "execution_time_host must be non-negative"
    # Check if the profiles content is empty or if it indicates a timeout
    is_tle = False
    if profiles_content == "":
        if execution_time_host > time_limit + TIME_LIMIT_TOLERANCE:  # NOTE: ex. `python -c "import time; time.sleep(10)"` (with tolerance)
            return CaseResult(
                input_str=input_str,
                output_str=output_str,
                error_str=error_str,
                judge_result=JudgeResult.TIME_LIMIT_EXCEEDED,
                message=f"Time limit exceeded: No profiles file was generated. {execution_time_host} > {time_limit + TIME_LIMIT_TOLERANCE}",
                absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
                execution_time=min(execution_time_host, time_limit + 0.1),  # NOTE: slight longer than time limit
                memory_usage=0,
            )
        else:  # NOTE: Error in running the code
            return CaseResult(
                input_str=input_str,
                output_str=output_str,
                error_str=error_str,
                judge_result=JudgeResult.RUNTIME_ERROR,
                message="Runtime error.",
                absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
                execution_time=execution_time_host,
                memory_usage=0,
            )
    elif profiles_content.startswith("Command terminated by signal 9"):
        # NOTE: Sigkill is sent by `prlimit` and included to the profiles file
        profiles_content = profiles_content.split("\n", 1)[1]  # Remove the first line
        is_tle = True
    elif profiles_content.startswith("Command exited with non-zero status"):
        # NOTE: This indicates that the run command failed
        profiles_content = profiles_content.split("\n", 1)[1]  # Remove the first line
    # Parse the profiles content
    profiles_content = profiles_content.strip()
    try:
        profiles_dict = json.loads(profiles_content)
    except json.JSONDecodeError:
        return CaseResult(
            input_str=input_str,
            output_str=output_str,
            error_str=error_str,
            judge_result=JudgeResult.WRONG_ANSWER,
            message="Wrong answer.",
            absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
            execution_time=min(execution_time_host, time_limit + 0.1),  # NOTE: slight longer than time limit
            memory_usage=0,
        )
    try:
        profiles = Profiles(**profiles_dict)
    except ValueError:
        return CaseResult(
            input_str=input_str,
            output_str=output_str,
            error_str=error_str,
            judge_result=JudgeResult.INTERNAL_ERROR,
            message="Internal Error: Invalid profiles format.",
            absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
            execution_time=min(execution_time_host, time_limit + 0.1),  # NOTE: slight longer than time limit
            memory_usage=0,
        )  # NOTE: This should not happen, but just in case
    # Check the profiles for exit status, execution time, and memory usage
    exit_status = profiles.exit_status
    # Use CPU time as primary metric (more accurate, excludes I/O wait overhead)
    # But also check elapsed time to catch programs that intentionally sleep/wait
    cpu_time = profiles.user_cpu_seconds + profiles.system_cpu_seconds
    elapsed_time = profiles.elapsed_time_seconds
    # Use the maximum, but apply tolerance only to elapsed_time (I/O overhead)
    # CPU time should be checked strictly, elapsed_time gets tolerance for I/O wait
    execution_time = max(cpu_time, elapsed_time)
    memory_usage = profiles.max_resident_set_size_kbytes * 1024
    # Check the resource usage
    if exit_status != 0:
        return CaseResult(
            input_str=input_str,
            output_str=output_str,
            error_str=error_str,
            judge_result=JudgeResult.RUNTIME_ERROR,
            message="Runtime error.",
            absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
            execution_time=min(execution_time, time_limit + 0.1),  # NOTE: slight longer than time limit
            memory_usage=memory_usage,
        )
    # Check CPU time strictly, elapsed time with tolerance (for I/O overhead)
    elif cpu_time > time_limit or (elapsed_time > time_limit + TIME_LIMIT_TOLERANCE) or is_tle:
        # Determine which metric triggered the TLE for better debugging
        if is_tle:
            tle_reason = f"CPU limit exceeded (prlimit killed process)"
        elif cpu_time > time_limit:
            tle_reason = f"CPU time exceeded: {cpu_time:.3f}s > {time_limit}s"
        else:
            tle_reason = f"Elapsed time exceeded: {elapsed_time:.3f}s > {time_limit + TIME_LIMIT_TOLERANCE:.3f}s (CPU: {cpu_time:.3f}s)"
        return CaseResult(
            input_str=input_str,
            output_str=output_str,
            error_str=error_str,
            judge_result=JudgeResult.TIME_LIMIT_EXCEEDED,
            message=f"Time limit exceeded: {tle_reason}",
            absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
            execution_time=min(execution_time, time_limit + 0.1),  # NOTE: slight longer than time limit
            memory_usage=memory_usage,
        )
    elif memory_usage > memory_limit:
        return CaseResult(
            input_str=input_str,
            output_str=output_str,
            error_str=error_str,
            judge_result=JudgeResult.MEMORY_LIMIT_EXCEEDED,
            message="Memory limit exceeded.",
            absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
            execution_time=execution_time,
            memory_usage=memory_usage,
        )
    return execution_time, memory_usage  # Return the execution time and memory usage if all checks pass


def run_single_case_remote(
    temp_dir_str: str,
    tool_dir_str: str,
    code_file_str: str,
    object_file_str: str,
    problem_id: str,
    time_limit: float,
    memory_limit: int,
    problem_type_str: str,
    case_idx: int,
    input_str: str,
    code_language_str: str,
    judge_version_str: str,
    return_details: bool,
    skip_local_visualization: bool,
    generation_id: str | None = None,
) -> CaseResult:
    """Ray remote function to run a single test case.
    
    Args:
        temp_dir_str: Path to temp directory on NFS
        tool_dir_str: Path to tool directory on NFS
        code_file_str: Path to code file on NFS
        object_file_str: Path to object file on NFS
        problem_id: Problem ID
        time_limit: Time limit in seconds
        memory_limit: Memory limit in bytes
        problem_type_str: Problem type as string (BATCH or REACTIVE)
        case_idx: Case index
        input_str: Input string for the case
        code_language_str: Code language as string
        judge_version_str: Judge version as string
        return_details: Whether to return detailed results
        skip_local_visualization: Whether to skip visualization
        generation_id: Optional generation ID to include in logs for tracking
        
    Returns:
        CaseResult for the case
    """
    # Import only what's needed - functions in same module are accessible directly
    from pathlib import Path
    from ..code_language import CodeLanguage, JudgeVersion
    from ..data import ProblemType as ProblemTypeEnum
    # Functions in the same module (HostPathsCompile, build_*_command, case_iter_func)
    # are accessible directly since they're in the module namespace
    
    # Resolve paths to absolute and ensure temp_dir exists (important for Ray workers on different nodes)
    temp_dir = Path(temp_dir_str).resolve()
    temp_dir.mkdir(parents=True, exist_ok=True)  # Ensure temp_dir exists
    tool_dir = Path(tool_dir_str).resolve()
    code_language = CodeLanguage(code_language_str)
    judge_version = JudgeVersion(judge_version_str)
    problem_type = ProblemTypeEnum(problem_type_str)  # Convert string to enum
    
    # Reconstruct host_paths_compile from paths
    host_paths_compile = HostPathsCompile(
        code_file=Path(code_file_str),
        object_file=Path(object_file_str),
    )
    
    # Build commands
    batch_run_command = build_batch_run_command(code_language, judge_version, time_limit)
    batch_judge_command = build_batch_judge_command()
    reactive_judge_command = build_reactive_judge_command(code_language, judge_version, time_limit)
    vis_command = build_vis_command()
    
    # Run the case
    return case_iter_func(
        problem_id=problem_id,
        time_limit=time_limit,
        memory_limit=memory_limit,
        problem_type=problem_type,
        case_idx=case_idx,
        input_str=input_str,
        code_language=code_language,
        judge_version=judge_version,
        temp_dir=temp_dir,
        tool_dir=tool_dir,
        return_details=return_details,
        skip_local_visualization=skip_local_visualization,
        host_paths_compile=host_paths_compile,
        batch_run_command=batch_run_command,
        batch_judge_command=batch_judge_command,
        reactive_judge_command=reactive_judge_command,
        vis_command=vis_command,
        generation_id=generation_id,
    )


def case_iter_func(
    problem_id: str,
    time_limit: float,
    memory_limit: int,
    problem_type: ProblemType,
    case_idx: int,
    input_str: str,
    code_language: CodeLanguage,
    judge_version: JudgeVersion,
    temp_dir: Path,
    tool_dir: Path,
    return_details: bool,
    skip_local_visualization: bool,
    host_paths_compile: HostPathsCompile,
    batch_run_command: str,
    batch_judge_command: str,
    reactive_judge_command: str,
    vis_command: str,
    generation_id: str | None = None,
) -> CaseResult:
    result_input_str = input_str if return_details else None
    host_paths_judge: HostPathsBatchJudge | HostPathsReactiveJudge
    execution_time_host = -1.0
    
    # Create log prefix with generation ID if provided
    gen_prefix = f"[GEN {generation_id}] " if generation_id else ""
    case_prefix = f"{gen_prefix}[CASE {case_idx}]"

    if problem_type == ProblemType.BATCH:
        case_start = time.perf_counter()
        
        # Run the submission code and generate the output file
        host_paths_run = setup_paths_batch_run(
            host_paths_compile, temp_dir, input_str, f"{problem_id}_{case_idx:06d}_",
            problem_id=problem_id, case_idx=case_idx
        )
        run_volumes = get_batch_run_volumes(host_paths_run, temp_dir)
        run_result = run_batch_run_container(
            code_language, judge_version, time_limit, run_volumes, batch_run_command, result_input_str
        )
        
        if isinstance(run_result, CaseResult):
            return run_result
        assert isinstance(run_result, tuple), "Run result must be a tuple"
        execution_time_host, stderr = run_result
        
        # Wait for output file to be fully written with proper synchronization
        # Retry with exponential backoff to ensure file is complete
        max_wait_retries = 10
        wait_delay = 0.01  # Start with 10ms
        output_file_ready = False
        for retry in range(max_wait_retries):
            if host_paths_run.output_file.exists():
                try:
                    # Try to open file in append mode to ensure it's not being written
                    with open(host_paths_run.output_file, 'r+b') as f:
                        f.seek(0, 2)  # Seek to end
                        current_size = f.tell()
                        # Wait a bit and check if size is stable (file is done writing)
                        time.sleep(wait_delay)
                        f.seek(0, 2)
                        new_size = f.tell()
                        if current_size == new_size:
                            # Force sync to ensure all data is flushed
                            os.fsync(f.fileno())
                            output_file_ready = True
                            break
                except (OSError, IOError) as e:
                    if retry < max_wait_retries - 1:
                        time.sleep(wait_delay)
                        wait_delay *= 2  # Exponential backoff
                        continue
            if retry < max_wait_retries - 1:
                time.sleep(wait_delay)
                wait_delay *= 2  # Exponential backoff
        
        # Safely read output file - might not exist if process was killed or timed out
        try:
            result_output_str = host_paths_run.output_file.read_text() if (return_details and output_file_ready) else None
        except Exception as e:
            logger.warning(f"{case_prefix} Failed to read output file: {e}")
            result_output_str = None
        result_error_str = stderr if return_details else None
        
        # Wait for profiles file to be fully written with proper synchronization
        profiles_content = ""
        max_wait_retries = 10
        wait_delay = 0.01
        profiles_file_ready = False
        for retry in range(max_wait_retries):
            if host_paths_run.profiles_file.exists():
                try:
                    with open(host_paths_run.profiles_file, 'r+b') as f:
                        f.seek(0, 2)
                        current_size = f.tell()
                        time.sleep(wait_delay)
                        f.seek(0, 2)
                        new_size = f.tell()
                        if current_size == new_size:
                            os.fsync(f.fileno())
                            profiles_file_ready = True
                            break
                except (OSError, IOError) as e:
                    if retry < max_wait_retries - 1:
                        time.sleep(wait_delay)
                        wait_delay *= 2
                        continue
            if retry < max_wait_retries - 1:
                time.sleep(wait_delay)
                wait_delay *= 2
        
        # Parse the profiles file - handle case where file might not exist (e.g., timeout)
        try:
            if profiles_file_ready:
                profiles_content = host_paths_run.profiles_file.read_text()
            else:
                profiles_content = ""
        except Exception as e:
            # If we can't read the profiles file, treat as empty (will be handled by parse_profiles)
            logger.warning(f"{case_prefix} Failed to read profiles file: {e}")
            profiles_content = ""
        
        profiles_result = parse_profiles(
            time_limit,
            memory_limit,
            profiles_content,
            execution_time_host,
            result_input_str,
            result_output_str,
            result_error_str,
        )
        if isinstance(profiles_result, CaseResult):
            return profiles_result  # NOTE: Parsing profiles failed, return the result
        assert isinstance(profiles_result, tuple), "Profiles result must be a tuple"
        execution_time, memory_usage = profiles_result
        
        # Ensure output file exists before judging (it may not exist if program crashed/killed before writing)
        # The file was created with touch() but might not be visible on NFS or might have been deleted
        if not host_paths_run.output_file.exists():
            logger.warning(
                f"{case_prefix} Output file does not exist after run, creating empty file. "
                f"This indicates the program terminated before writing any output. "
                f"Path: {host_paths_run.output_file}"
            )
            host_paths_run.output_file.parent.mkdir(parents=True, exist_ok=True)
            host_paths_run.output_file.touch()
            # Force sync to NFS
            with open(host_paths_run.output_file, 'w') as f:
                f.flush()
                os.fsync(f.fileno())
        
        # Calculate score by the input and output files
        host_paths_judge = setup_paths_batch_judge(host_paths_run)
        judge_volumes = get_batch_judge_volumes(host_paths_judge, tool_dir, problem_id, generation_id=generation_id)
        batch_judge_result = run_batch_judge_container(
            judge_volumes,
            batch_judge_command,
            execution_time_host,
            result_input_str,
            result_output_str,
            result_error_str,
        )
        
        if isinstance(batch_judge_result, CaseResult):
            return batch_judge_result
        assert isinstance(batch_judge_result, int), "Judge result must be an integer"
        absolute_score = batch_judge_result
    elif problem_type == ProblemType.REACTIVE:
        host_paths_judge = setup_paths_reactive_judge(
            host_paths_compile,
            temp_dir,
            input_str,
            f"{problem_id}_{case_idx:06d}_",
            problem_id=problem_id,
            case_idx=case_idx,
        )
        judge_volumes = get_reactive_judge_volumes(host_paths_judge, temp_dir, tool_dir, problem_id)
        reactive_judge_result = run_reactive_judge_container(
            code_language,
            judge_version,
            time_limit,
            judge_volumes,
            reactive_judge_command,
            result_input_str,
            host_paths_judge.output_file if return_details else None,
        )
        wo_profile_result = None
        if isinstance(reactive_judge_result, CaseResult):
            wo_profile_result = reactive_judge_result
            execution_time_host = reactive_judge_result.execution_time  # already processed
            result_output_str = reactive_judge_result.output_str  # already processed
            result_error_str = reactive_judge_result.error_str  # already processed
        else:
            assert isinstance(reactive_judge_result, tuple), "Judge result must be a tuple"
            execution_time_host, absolute_score, stderr = reactive_judge_result
            
            # Wait for output file to be fully written with proper synchronization
            max_wait_retries = 10
            wait_delay = 0.01
            output_file_ready = False
            for retry in range(max_wait_retries):
                if host_paths_judge.output_file.exists():
                    try:
                        with open(host_paths_judge.output_file, 'r+b') as f:
                            f.seek(0, 2)
                            current_size = f.tell()
                            time.sleep(wait_delay)
                            f.seek(0, 2)
                            new_size = f.tell()
                            if current_size == new_size:
                                os.fsync(f.fileno())
                                output_file_ready = True
                                break
                    except (OSError, IOError) as e:
                        if retry < max_wait_retries - 1:
                            time.sleep(wait_delay)
                            wait_delay *= 2
                            continue
                if retry < max_wait_retries - 1:
                    time.sleep(wait_delay)
                    wait_delay *= 2
            
            # Safely read output file - might not exist if process was killed or timed out
            try:
                result_output_str = host_paths_judge.output_file.read_text() if (return_details and output_file_ready) else None
            except Exception as e:
                logger.warning(f"{case_prefix} Failed to read output file: {e}")
                result_output_str = None
            result_error_str = stderr if return_details else None
        
        # Wait for profiles file to be fully written with proper synchronization
        profiles_content = ""
        max_wait_retries = 10
        wait_delay = 0.01
        profiles_file_ready = False
        for retry in range(max_wait_retries):
            if host_paths_judge.profiles_file.exists():
                try:
                    with open(host_paths_judge.profiles_file, 'r+b') as f:
                        f.seek(0, 2)
                        current_size = f.tell()
                        time.sleep(wait_delay)
                        f.seek(0, 2)
                        new_size = f.tell()
                        if current_size == new_size:
                            os.fsync(f.fileno())
                            profiles_file_ready = True
                            break
                except (OSError, IOError) as e:
                    if retry < max_wait_retries - 1:
                        time.sleep(wait_delay)
                        wait_delay *= 2
                        continue
            if retry < max_wait_retries - 1:
                time.sleep(wait_delay)
                wait_delay *= 2
        
        # Parse the profiles file - handle case where file might not exist (e.g., timeout)
        try:
            if profiles_file_ready:
                profiles_content = host_paths_judge.profiles_file.read_text()
            else:
                profiles_content = ""
        except Exception as e:
            # If we can't read the profiles file, treat as empty (will be handled by parse_profiles)
            logger.warning(f"{case_prefix} Failed to read profiles file: {e}")
            profiles_content = ""
        profiles_result = parse_profiles(
            time_limit,
            memory_limit,
            profiles_content,
            execution_time_host,
            result_input_str,
            result_output_str,
            result_error_str,
        )
        if isinstance(profiles_result, CaseResult):
            return profiles_result  # NOTE: Parsing profiles failed, return the result
        assert isinstance(profiles_result, tuple), "Profiles result must be a tuple"
        execution_time, memory_usage = profiles_result
        if wo_profile_result is not None:
            return CaseResult(
                input_str=wo_profile_result.input_str,
                output_str=wo_profile_result.output_str,
                error_str=wo_profile_result.error_str,
                judge_result=wo_profile_result.judge_result,
                message=wo_profile_result.message,
                absolute_score=wo_profile_result.absolute_score,
                execution_time=execution_time,
                memory_usage=memory_usage,
            )
    else:
        raise ValueError(f"Invalid problem type: {problem_type}")

    # Output the final state and state history if requested
    local_visualization = None
    if not skip_local_visualization and problem_id not in constants.NO_LOCAL_VIS:
        # Run the local visualization command in a ray process
        host_paths_vis = setup_paths_vis(host_paths_judge, temp_dir, problem_id, f"{problem_id}_{case_idx:06d}_")
        vis_volumes = get_vis_volumes(host_paths_vis, tool_dir)
        run_vis_container(vis_command, vis_volumes)
        # Read the local visualization SVG or HTML
        svg_text = host_paths_vis.local_visualization_file.read_text()
        svg_text = svg_text.replace("\n", "").removeprefix("<html><body>").removesuffix("</body></html>")
        if svg_text == "":
            raise RuntimeError("The local visualization file is empty. Something went wrong.")
        local_visualization = read_svg(svg_text)
    # Add the result
    return CaseResult(
        input_str=result_input_str,
        output_str=result_output_str,
        error_str=result_error_str,
        judge_result=JudgeResult.ACCEPTED,
        message="",
        absolute_score=absolute_score,
        local_visualization=local_visualization,
        execution_time=execution_time,
        memory_usage=memory_usage,
    )


def run_cases(
    inputs: list[str],
    code: str,
    code_language: CodeLanguage,
    judge_version: JudgeVersion,
    time_limit: float,
    memory_limit: int,
    problem_id: str,
    problem_type: ProblemType,
    tool_dir: Path,
    return_details: bool,
    skip_local_visualization: bool,
    num_workers: int,
    base_dir: Path | None = None,
) -> list[CaseResult]:
    """Run the cases for the given inputs and code.

    Args:
        inputs (list[str]): The list of inputs.
        code (str): The code to run.
        code_language (CodeLanguage): The code language.
        judge_version (JudgeVersion): The judge version.
        time_limit (float): The time limit in seconds.
        memory_limit (int): The memory limit in bytes.
        problem_id (str): The problem ID.
        problem_type (ProblemType): The problem type.
        tool_dir (Path): The directory of the tools.
        return_details (bool): Whether to return detailed results (input_str, output_str, error_str).
        skip_local_visualization (bool): Whether to skip local visualization.
        num_workers (int): The number of workers for running cases.
        base_dir (Path | None): Base directory on NFS for temp files. If None, uses system temp.

    Returns:
        list[CaseResult]: The list of case results.
    """
    # Generate a unique generation ID for this run to track logs
    generation_id = str(uuid.uuid4())[:8]  # Use first 8 chars for readability
    
    # Create temporary directory on NFS if base_dir is provided
    if base_dir is not None:
        base_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=f"ahc_cases_{problem_id}_", dir=str(base_dir)))
        temp_dir_cleanup = True
    else:
        temp_dir = Path(tempfile.mkdtemp())
        temp_dir_cleanup = True

    try:
        # Prepare for the run
        host_paths_compile = setup_paths_compile(temp_dir, code, code_language, judge_version)
        compile_volumes = get_compile_volumes(host_paths_compile, temp_dir)
        compile_command = build_compile_command(
            code_language, judge_version, host_paths_compile.object_file.relative_to(temp_dir)
        )
        batch_run_command = build_batch_run_command(code_language, judge_version, time_limit)
        batch_judge_command = build_batch_judge_command()
        reactive_judge_command = build_reactive_judge_command(code_language, judge_version, time_limit)
        vis_command = build_vis_command()

        # Compile the code in a ray process
        compile_result = run_compile_container(
            code_language,
            judge_version,
            host_paths_compile,
            compile_volumes,
            compile_command,
        )
        if compile_result is not None:
            return [compile_result for _ in inputs]  # NOTE: Compilation failed, return the result

        # Run the code and calculate the score in ray processes
        all_cases_start = time.perf_counter()
        case_results: list[CaseResult] = []
        if len(inputs) == 1 or num_workers == 1:
            for case_idx, input_str in enumerate(inputs):
                case_result = case_iter_func(
                    problem_id,
                    time_limit,
                    memory_limit,
                    problem_type,
                    case_idx,
                    input_str,
                    code_language,
                    judge_version,
                    temp_dir,
                    tool_dir,
                    return_details,
                    skip_local_visualization,
                    host_paths_compile,
                    batch_run_command,
                    batch_judge_command,
                    reactive_judge_command,
                    vis_command,
                    generation_id=generation_id,
                )
                # Check for early stopping on failure
                if case_result.judge_result != JudgeResult.ACCEPTED:
                    gen_prefix = f"[GEN {generation_id}] " if generation_id else ""
                    logger.warning(
                        f"{gen_prefix}[RUN_CASES] Early stop: Case {case_idx} failed with {case_result.judge_result.value}. "
                        f"Message: {case_result.message}"
                    )
                    # Fill remaining cases with placeholder results
                    for remaining_idx in range(case_idx + 1, len(inputs)):
                        case_results.append(CaseResult(
                            input_str=inputs[remaining_idx] if return_details else None,
                            output_str=None,
                            error_str=None,
                            judge_result=JudgeResult.INTERNAL_ERROR,
                            message="Internal Error: Case not executed (early stop).",
                            absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
                            execution_time=0.0,
                            memory_usage=0,
                        ))
                    case_results.append(case_result)
                    break
                # Add the result
                case_results.append(case_result)
        else:
            # Multiple cases - use Ray workers launched via ThreadPoolExecutor
            # Create Ray remote function for single case execution
            _case_exec_fn = ray.remote(num_cpus=2, max_calls=0)(run_single_case_remote)
            
            case_results = [
                CaseResult(
                    input_str=input_str if return_details else None,
                    output_str=None,
                    error_str=None,
                    judge_result=JudgeResult.INTERNAL_ERROR,
                    message="Internal Error: Case not executed (early stop).",
                    absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
                    execution_time=0.0,
                    memory_usage=0,
                )
                for input_str in inputs
            ]
            
            # Use ThreadPoolExecutor to launch Ray tasks
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                future_to_case_idx = {}
                for case_idx, input_str in enumerate(inputs):
                    def submit_ray_task(idx=case_idx, inp=input_str):
                        return _case_exec_fn.options(scheduling_strategy="SPREAD").remote(
                            str(temp_dir),
                            str(tool_dir),
                            str(host_paths_compile.code_file),
                            str(host_paths_compile.object_file),
                            problem_id,
                            time_limit,
                            memory_limit,
                            problem_type.value,  # Convert enum to string for Ray
                            idx,
                            inp,
                            code_language.value,
                            judge_version.value,
                            return_details,
                            skip_local_visualization,
                            generation_id=generation_id,
                        )
                    future = executor.submit(submit_ray_task)
                    future_to_case_idx[future] = case_idx
                    # Small delay to stagger worker creation and avoid overwhelming Ray
                    if case_idx > 0 and case_idx % 5 == 0:
                        time.sleep(0.1)  # Brief pause every 5 submissions
                
                # Process results as they complete with early stopping
                failed_case_idx = None
                for future in as_completed(future_to_case_idx):
                    case_idx = future_to_case_idx[future]
                    try:
                        ray_future = future.result()  # Get the Ray future
                        case_result = ray.get(ray_future)  # Get the actual result
                        case_results[case_idx] = case_result
                        
                        # Check if this case failed - if so, stop early
                        if case_result.judge_result != JudgeResult.ACCEPTED:
                            failed_case_idx = case_idx
                            gen_prefix = f"[GEN {generation_id}] " if generation_id else ""
                            logger.warning(
                                f"{gen_prefix}[RUN_CASES] Early stop: Case {case_idx} failed with {case_result.judge_result.value}. "
                                f"Message: {case_result.message}"
                            )
                            # Cancel remaining ThreadPoolExecutor futures (which will prevent new Ray tasks)
                            for remaining_future, remaining_idx in future_to_case_idx.items():
                                if remaining_future != future and not remaining_future.done():
                                    remaining_future.cancel()
                                    case_results[remaining_idx] = CaseResult(
                                        input_str=inputs[remaining_idx] if return_details else None,
                                        output_str=None,
                                        error_str=None,
                                        judge_result=JudgeResult.INTERNAL_ERROR,
                                        message="Internal Error: Case not executed (early stop).",
                                        absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
                                        execution_time=0.0,
                                        memory_usage=0,
                                    )
                            break
                    except Exception as e:
                        gen_prefix = f"[GEN {generation_id}] " if generation_id else ""
                        logger.warning(f"{gen_prefix}[RUN_CASES] Error in case {case_idx}: {e}")
                        case_results[case_idx] = CaseResult(
                            input_str=inputs[case_idx] if return_details else None,
                            output_str=None,
                            error_str=None,
                            judge_result=JudgeResult.INTERNAL_ERROR,
                            message=f"Internal Error: {e}",
                            absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
                            execution_time=0.0,
                            memory_usage=0,
                        )
                        failed_case_idx = case_idx
                        # Cancel remaining ThreadPoolExecutor futures
                        for remaining_future, remaining_idx in future_to_case_idx.items():
                            if remaining_future != future and not remaining_future.done():
                                remaining_future.cancel()
                                case_results[remaining_idx] = CaseResult(
                                    input_str=inputs[remaining_idx] if return_details else None,
                                    output_str=None,
                                    error_str=None,
                                    judge_result=JudgeResult.INTERNAL_ERROR,
                                    message="Internal Error: Case not executed (early stop).",
                                    absolute_score=constants.REJECTED_ABSOLUTE_SCORE,
                                    execution_time=0.0,
                                    memory_usage=0,
                                )
                        break
        
        # Only log if total time is unusually long
        total_time = time.perf_counter() - all_cases_start
        if total_time > 60.0:
            gen_prefix = f"[GEN {generation_id}] " if generation_id else ""
            logger.warning(f"{gen_prefix}[RUN_CASES] Slow evaluation: {total_time:.1f}s for {len(inputs)} cases (avg {total_time/len(inputs):.2f}s/case)")

    finally:
        # Cleanup temp directory (but NEVER delete cache directories)
        if temp_dir_cleanup and temp_dir.exists():
            # Safety check: ensure we're not accidentally deleting cache directories
            from ..utils import get_cache_dir
            cache_dir = get_cache_dir()
            # Check if temp_dir is within or equal to any cache directory
            try:
                temp_dir_resolved = temp_dir.resolve()
                cache_dir_resolved = cache_dir.resolve()
                # If temp_dir is inside cache_dir or is cache_dir itself, don't delete it (safety check)
                # Check: is temp_dir inside cache_dir? (cache_dir is a parent of temp_dir)
                if cache_dir_resolved in temp_dir_resolved.parents:
                    logger.warning(f"[RUN_CASES] Skipping cleanup of {temp_dir} - appears to be inside cache directory {cache_dir}")
                    return case_results
                # Check: is temp_dir equal to cache_dir?
                if temp_dir_resolved == cache_dir_resolved:
                    logger.warning(f"[RUN_CASES] Skipping cleanup of {temp_dir} - appears to be the cache directory itself")
                    return case_results
                # Check: is temp_dir inside any cache subdirectory (tester_binaries, public_inputs_150, etc.)
                for cache_subdir in ["tester_binaries", "public_inputs_150"]:
                    cache_subdir_path = (cache_dir / cache_subdir).resolve()
                    if cache_subdir_path.exists() and cache_subdir_path in temp_dir_resolved.parents:
                        logger.warning(f"[RUN_CASES] Skipping cleanup of {temp_dir} - appears to be inside cache subdirectory {cache_subdir_path}")
                        return case_results
            except Exception:
                pass  # If path resolution fails, continue with cleanup (temp_dir should be safe)
            
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass  # Best effort cleanup

    return case_results
