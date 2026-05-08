from __future__ import annotations

import json
import os
import pickle
import tempfile
from pathlib import Path
from typing import Any

import ray

from examples.ahc.lib.code_language import CodeLanguage, JudgeVersion
from examples.ahc.lib.data import load_local_problem
from examples.ahc.lib.tool_wrappers.case_runner import run_cases
from examples.ahc.lib.utils import get_cache_dir


def get_ale_bench_error(msg: str) -> dict[str, Any]:
    return {
        "score": 0.0,
        "msg": msg,
        "correctness": 0.0,
        "performance": 0.0,
    }


def load_cached_public_inputs(problem_id: str, lite_version: bool = True) -> list[str]:
    cache_dir = get_cache_dir() / "public_inputs_150"

    # Find all cache files for this problem
    cache_files = list(cache_dir.glob(f"{problem_id}_*.json"))
    if not cache_files:
        raise FileNotFoundError(
            f"No cached public inputs found for {problem_id}. "
            f"Run cache_public_inputs.py first. Cache dir: {cache_dir}"
        )

    # Try to find a cache file matching the requested lite_version
    matching_file = None
    fallback_file = None

    for cache_file in cache_files:
        with open(cache_file, "r") as f:
            cached_data = json.load(f)

        # Verify problem_id matches
        if cached_data.get("problem_id") != problem_id:
            continue

        # Check if lite_version matches
        if cached_data.get("lite_version") == lite_version:
            matching_file = (cache_file, cached_data)
            break
        else:
            # Keep track of the first file with opposite lite_version as fallback
            if fallback_file is None:
                fallback_file = (cache_file, cached_data)

    # Use matching file if found, otherwise use fallback
    if matching_file:
        cache_file, cached_data = matching_file
        print(f"Using cached inputs with lite_version={lite_version} from {cache_file}")
    elif fallback_file:
        cache_file, cached_data = fallback_file
        actual_lite_version = cached_data.get("lite_version")
        print(
            f"Warning: Requested lite_version={lite_version} not found, "
            f"using lite_version={actual_lite_version} from {cache_file}"
        )
    else:
        raise ValueError(
            f"No valid cache file found for {problem_id}. "
            f"Found files but none matched problem_id. Cache dir: {cache_dir}"
        )

    return cached_data["inputs"]


def setup_cached_tester_dir(problem_id: str, base_dir: Path) -> Path:
    # base_dir kept for API compatibility, but the tester comes from cache.
    cache_dir = get_cache_dir() / "tester_binaries"
    tester_cache_file = cache_dir / f"{problem_id}_tester"

    if not cache_dir.exists():
        raise RuntimeError(
            f"CRITICAL: Cache directory {cache_dir} does not exist! "
            f"This may indicate the cache was deleted or the path is incorrect. "
            f"Please run cache_public_inputs.py to recreate it, or check that ALE_BENCH_CACHE is set correctly."
        )
    if not cache_dir.is_dir():
        raise RuntimeError(
            f"CRITICAL: Cache directory {cache_dir} exists but is not a directory! "
            f"This is unexpected and may indicate filesystem issues."
        )

    # Verify tester file exists
    if not tester_cache_file.exists():
        raise FileNotFoundError(
            f"No cached tester binary found for {problem_id}. "
            f"Run cache_public_inputs.py first. Expected: {tester_cache_file}. "
            f"Cache directory exists: {cache_dir.exists()}"
        )

    return cache_dir


def run_cases_remote(
    code_path: str,
    problem_data_path: str,
    tool_dir_path: str,
    problem_id: str,
    lite_version: bool,
    results_path: str,
):
    # Read code from file
    with open(code_path, "r") as f:
        code = f.read()

    # Load cached public inputs directly on the ray worker
    inputs = load_cached_public_inputs(problem_id=problem_id, lite_version=lite_version)

    # Read problem data from file
    with open(problem_data_path, "rb") as f:
        problem_data = pickle.load(f)

    # Extract problem data
    time_limit = problem_data["time_limit"]
    memory_limit = problem_data["memory_limit"]
    problem_type = problem_data["problem_type"]
    code_lang = problem_data["code_language"]
    judge_version = problem_data["judge_version"]

    # Convert enums
    code_lang_enum = CodeLanguage(code_lang) if isinstance(code_lang, str) else code_lang
    judge_version_enum = (
        JudgeVersion(judge_version) if isinstance(judge_version, str) else judge_version
    )

    # Run cases
    # Ensure tool_dir_path is absolute
    tool_dir_abs = Path(tool_dir_path).resolve()
    # Get base_dir from results_path parent (should be on NFS)
    base_dir = Path(results_path).parent
    case_results = run_cases(
        inputs=inputs,
        code=code,
        code_language=code_lang_enum,
        judge_version=judge_version_enum,
        time_limit=time_limit,
        memory_limit=memory_limit,
        problem_id=problem_id,
        problem_type=problem_type,
        tool_dir=tool_dir_abs,
        return_details=False,
        skip_local_visualization=True,
        num_workers=150,
        base_dir=base_dir,  # Use NFS directory for temp files
    )

    # Write results to file
    with open(results_path, "wb") as f:
        pickle.dump(case_results, f)

    return results_path


# Initialize ray remote function (module-level)
_exec_fn = None


def _get_exec_fn(num_cpus: int = 2):
    """Get or create the ray remote execution function."""
    global _exec_fn
    if _exec_fn is None:
        _exec_fn = ray.remote(num_cpus=num_cpus, max_calls=1)(run_cases_remote)
    return _exec_fn


def run_ale_bench_task(
    generation: str,
    problem_id: str | None = None,
    lite_version: bool = True,
    log_dir: str | None = None,
    num_cpus_per_task: int = 2,
) -> dict[str, Any]:
    # Get problem_id from parameter or environment variable
    if problem_id is None:
        problem_id = os.environ.get("ALE_BENCH_PROBLEM_ID")
        if problem_id is None:
            return get_ale_bench_error(
                "problem_id must be provided or set ALE_BENCH_PROBLEM_ID environment variable"
            )

    # Validate code
    if not generation or not generation.strip():
        return get_ale_bench_error("Invalid code: empty or missing")

    # Basic validation: check for main function in C++ code
    code_lower = generation.lower()
    if "int main" not in code_lower and "void main" not in code_lower:
        return get_ale_bench_error("Invalid code: missing main function")

    tool_dir = None  # This is the cache directory, NOT a temp directory - do NOT delete it
    code_path = None
    problem_data_path = None
    results_path = None

    try:
        # Set up NFS directory for file-based data transfer
        # log_dir must be provided and must be on NFS for Ray workers to access files
        if log_dir is None:
            # Try to get from environment variable as fallback
            log_dir = os.environ.get("ALE_BENCH_LOG_DIR")
            if log_dir is None:
                raise ValueError(
                    "log_dir must be provided or set ALE_BENCH_LOG_DIR environment variable. "
                    "This must be a path on NFS that is accessible from all Ray workers."
                )
        tmp_dir = Path(log_dir) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # This prevents accidental deletion of cache if log_dir is misconfigured
        cache_dir = get_cache_dir()
        tmp_dir_resolved = tmp_dir.resolve()
        cache_dir_resolved = cache_dir.resolve()
        if cache_dir_resolved in tmp_dir_resolved.parents or tmp_dir_resolved == cache_dir_resolved:
            raise RuntimeError(
                f"CRITICAL: tmp_dir ({tmp_dir_resolved}) is inside or equal to cache directory ({cache_dir_resolved})! "
                f"This would cause the cache to be deleted. Please use a different log_dir that is not in the cache directory."
            )

        # Load problem data from local problems directory
        problem, seeds, standings, rank_performance_map, data_root = load_local_problem(
            problem_id, lite_version
        )

        # Set up cached tester binary (on NFS so Ray workers can access it)
        tool_dir = setup_cached_tester_dir(problem_id, tmp_dir)
        expected_cache_dir = (get_cache_dir() / "tester_binaries").resolve()
        tool_dir_resolved = Path(tool_dir).resolve()
        if tool_dir_resolved != expected_cache_dir:
            raise RuntimeError(
                f"CRITICAL: tool_dir ({tool_dir_resolved}) is not the expected cache directory ({expected_cache_dir}). "
                f"This would cause the cache to be deleted! Aborting to prevent data loss."
            )

        # Convert code_language string to enum (default: cpp20)
        code_lang = CodeLanguage.CPP20
        judge_version = JudgeVersion.V202301  # Default judge version

        # Write code to NFS file
        with tempfile.NamedTemporaryFile(
            suffix=".cpp",
            delete=False,
            mode="w",
            dir=str(tmp_dir),
        ) as f:
            code_path = f.name
            f.write(generation)

        # Write problem data to NFS file (pickled)
        problem_data = {
            "time_limit": problem.constraints.time_limit,
            "memory_limit": problem.constraints.memory_limit,
            "problem_id": problem_id,
            "problem_type": problem.metadata.problem_type,
            "code_language": code_lang.value,  # CodeLanguage is a string enum
            "judge_version": judge_version.value,  # JudgeVersion is a string enum
        }
        with tempfile.NamedTemporaryFile(
            suffix=".pkl",
            delete=False,
            mode="wb",
            dir=str(tmp_dir),
        ) as f:
            problem_data_path = f.name
            pickle.dump(problem_data, f)

        # Compute expected results path
        results_path = str(Path(code_path).with_suffix(".results.pkl"))

        # Launch remote execution
        exec_fn = _get_exec_fn(num_cpus_per_task)
        result_path_future = exec_fn.options(scheduling_strategy="SPREAD").remote(
            code_path,
            problem_data_path,
            str(tool_dir),
            problem_id,
            lite_version,
            results_path,
        )

        # Wait for results (no timeout to allow for scheduling delays)
        returned_results_path = ray.get(result_path_future)

        if not returned_results_path or not os.path.exists(returned_results_path):
            raise RuntimeError(f"Results file does not exist: {returned_results_path}")

        # Load results from NFS file
        with open(returned_results_path, "rb") as f:
            case_results = pickle.load(f)

        # Return raw results; aggregation/scoring is handled by env.py.
        return {
            "problem_id": problem_id,
            "lite_version": lite_version,
            "case_results": case_results,
            "num_public_cases": len(case_results),
        }

    except FileNotFoundError as e:
        return get_ale_bench_error(f"Cache error: {str(e)}")
    except ray.exceptions.GetTimeoutError:
        return get_ale_bench_error("Evaluation timed out")
    except Exception as e:
        error_msg = str(e)
        # Check if it's a compilation error
        is_compile_error = any(
            keyword in error_msg.lower()
            for keyword in [
                "undefined reference to `main'",
                "undefined reference to `main\"",
                "no such file or directory",
                "compilation error",
                "compile",
            ]
        )
        if is_compile_error:
            return get_ale_bench_error(f"Compilation error: {error_msg}")
        else:
            return get_ale_bench_error(f"Evaluation error: {error_msg}")
    finally:
        # Cleanup temp files
        cache_dir = get_cache_dir()

        for file_path in [code_path, problem_data_path, results_path]:
            if file_path is not None:
                try:
                    file_path_obj = Path(file_path)
                    if file_path_obj.exists():
                        # Safety check: don't delete anything in cache directories
                        file_path_resolved = file_path_obj.resolve()
                        cache_dir_resolved = cache_dir.resolve()
                        # If file is inside cache_dir or any cache subdirectory, skip deletion
                        if cache_dir_resolved in file_path_resolved.parents:
                            continue  # Skip deletion of cache files
                        # Also check specific cache subdirectories
                        for cache_subdir in ["tester_binaries", "public_inputs_150"]:
                            cache_subdir_path = (cache_dir / cache_subdir).resolve()
                            if (
                                cache_subdir_path.exists()
                                and cache_subdir_path in file_path_resolved.parents
                            ):
                                continue  # Skip deletion of cache files
                        os.unlink(file_path)
                except (FileNotFoundError, OSError):
                    pass

