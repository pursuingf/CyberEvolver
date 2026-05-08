from __future__ import annotations

import base64
import io
import os
import random
import shutil
import socket
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import cairosvg
import ray
from PIL import Image
from ahocorapy.keywordtree import KeywordTree

from .constants import DEFAULT_CACHE_DIR

# Initialize Ray if not already initialized
if not ray.is_initialized():
    ray.init(ignore_reinit_error=True)


# Ray-based command execution
@ray.remote
def run_command_remote(
    command: str,
    volumes: dict[str, dict[str, str]],
    working_dir: str,
    timeout: float | None = None,
    mem_limit: int | None = None,
) -> tuple[int, str, str]:
    """Run a command in a ray remote process.

    Args:
        command: The command to run (already extracted from docker format).
        volumes: Dictionary mapping host paths to container paths.
        working_dir: Working directory for the command (container path).
        timeout: Timeout in seconds.
        mem_limit: Memory limit in bytes.

    Returns:
        Tuple of (exit_code, stdout, stderr).
    """
    import logging
    import subprocess
    from pathlib import Path
    
    # Set up logging - also use print for ray processes
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    
    def log_debug(msg):
        logger.debug(msg)
        print(f"[RAY DEBUG] {msg}", flush=True)
    
    def log_error(msg):
        logger.error(msg)
        print(f"[RAY ERROR] {msg}", flush=True)

    # Create mapping from container paths to host paths
    container_to_host = {}
    temp_dir = None
    judge_dir = None
    
    for host_path, volume_info in volumes.items():
        container_path = volume_info.get("bind", "")
        if container_path:
            container_to_host[container_path] = host_path
            
    # Collect all /workdir and /judge host paths to find common directories
    workdir_host_paths = []
    judge_host_paths = []
    
    for host_path, volume_info in volumes.items():
        container_path = volume_info.get("bind", "")
        if container_path.startswith("/workdir"):
            workdir_host_paths.append(Path(host_path))
        elif container_path.startswith("/judge"):
            judge_host_paths.append(Path(host_path))
    
    # Find common temp directory from /workdir paths
    if workdir_host_paths and temp_dir is None:
        # Find common parent of all /workdir host paths
        if len(workdir_host_paths) == 1:
            # Single path: use its parent if it's a file, or the path itself if it's a directory
            p = workdir_host_paths[0]
            temp_dir = p.parent if p.is_file() else p
        else:
            # Multiple paths: find common parent
            common_parts = []
            all_parts = [p.parts for p in workdir_host_paths]
            for parts in zip(*all_parts):
                if len(set(parts)) == 1:
                    common_parts.append(parts[0])
                else:
                    break
            if common_parts:
                temp_dir = Path(*common_parts)
            else:
                # Fallback: use parent of first path
                temp_dir = workdir_host_paths[0].parent if workdir_host_paths[0].is_file() else workdir_host_paths[0]
    
    # Find common judge directory from /judge paths
    if judge_host_paths and judge_dir is None:
        if len(judge_host_paths) == 1:
            p = judge_host_paths[0]
            # For /judge/target/release/tool, we need to extract the base /judge directory
            if "target/release" in str(p):
                if len(p.parents) >= 3:
                    judge_dir = p.parents[2]  # Go up from target/release/tester to tools
                elif len(p.parents) >= 2:
                    judge_dir = p.parents[1]  # Fallback: go up 2 levels
                else:
                    judge_dir = p.parent
            else:
                judge_dir = p.parent if p.is_file() else p
        else:
            # Find common parent, but account for target/release structure
            common_parts = []
            all_parts = [p.parts for p in judge_host_paths]
            for parts in zip(*all_parts):
                if len(set(parts)) == 1:
                    common_parts.append(parts[0])
                else:
                    break
            if common_parts:
                judge_dir = Path(*common_parts)
                # If we're inside target/release, go up to tools directory
                if "target" in common_parts and "release" in common_parts:
                    idx = common_parts.index("target")
                    judge_dir = Path(*common_parts[:idx])
            else:
                judge_dir = judge_host_paths[0].parent if judge_host_paths[0].is_file() else judge_host_paths[0]

    # Map /workdir to temp directory
    if temp_dir is not None:
        container_to_host["/workdir"] = str(temp_dir)
    
    # Map /judge to judge directory
    if judge_dir is not None:
        container_to_host["/judge"] = str(judge_dir)
    
    # Map working directory from container to host
    if working_dir.startswith("/workdir"):
        if temp_dir is not None:
            # Replace /workdir with temp_dir in the working_dir path
            relative_path = working_dir[len("/workdir"):].lstrip("/")
            work_path = temp_dir / relative_path if relative_path else temp_dir
        else:
            work_path = Path("/tmp")
    elif working_dir.startswith("/judge"):
        # For /judge paths, try to find from volumes or use /tmp
        work_path = Path("/tmp")
        for container_path, host_path in container_to_host.items():
            if container_path.startswith("/judge"):
                work_path = Path(host_path).parent if Path(host_path).is_file() else Path(host_path)
                break
    elif working_dir in container_to_host:
        work_path = Path(container_to_host[working_dir])
        if work_path.is_file():
            work_path = work_path.parent
    else:
        work_path = Path(working_dir)
    
    work_path.mkdir(parents=True, exist_ok=True)

    # Replace container paths in command with host paths (longest first to avoid partial matches)
    mapped_command = command
    
    # FIRST: Replace specific volume mappings (longest paths first) - these take precedence
    # This ensures /judge/target/release/tester gets replaced before /judge
    for container_path, host_path in sorted(container_to_host.items(), key=lambda x: len(x[0]), reverse=True):
        # Skip base paths /workdir and /judge - we'll handle those after specific mappings
        if container_path in ["/workdir", "/judge"]:
            continue
        mapped_command = mapped_command.replace(container_path, host_path)
    
    # THEN: Replace base paths /workdir and /judge for any remaining references
    if temp_dir is not None and "/workdir" in mapped_command:
        # Replace /workdir with temp_dir, handling both /workdir/... and standalone /workdir
        mapped_command = mapped_command.replace("/workdir/", f"{temp_dir}/")
        mapped_command = mapped_command.replace("/workdir ", f"{temp_dir} ")
        mapped_command = mapped_command.replace("/workdir", str(temp_dir))
    
    if judge_dir is not None and "/judge" in mapped_command:
        # Replace /judge with judge_dir (only for remaining references not already replaced)
        mapped_command = mapped_command.replace("/judge/", f"{judge_dir}/")
        mapped_command = mapped_command.replace("/judge ", f"{judge_dir} ")
        mapped_command = mapped_command.replace("/judge", str(judge_dir))

    # Prepare command with resource limits
    cmd_parts = ["/bin/sh", "-c", mapped_command]
    
    # Apply memory limit if specified using prlimit
    if mem_limit is not None:
        cmd_parts = ["prlimit", f"--as={mem_limit}", "--"] + cmd_parts
    
    # Verify that any tester/gen/vis binaries in the command actually exist
    # Add retry for NFS sync delays (important for Ray workers on different nodes)
    import time
    for container_path, host_path in container_to_host.items():
        if container_path.endswith("/tester") or container_path.endswith("/gen") or container_path.endswith("/vis"):
            host_path_obj = Path(host_path)
            # Retry check for NFS sync delays
            max_retries = 3
            found = False
            for retry in range(max_retries):
                if host_path_obj.exists() and host_path_obj.is_file():
                    found = True
                    break
                if retry < max_retries - 1:
                    time.sleep(0.1)  # Brief wait for NFS sync
            
            if not found:
                error_msg = (
                    f"Binary not found at expected path: {host_path}\n"
                    f"This binary needs to be built and placed at this location (on NFS).\n"
                    f"Expected path: {host_path}\n"
                    f"Container path: {container_path}\n"
                    f"Parent dir exists: {host_path_obj.parent.exists() if host_path_obj.parent else False}"
                )
                log_error(error_msg)
                return (1, "", error_msg)

    # Run the command
    try:
        process = subprocess.Popen(
            cmd_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(work_path),
            text=True,
        )
        stdout, stderr = process.communicate(timeout=timeout)
        exit_code = process.returncode
        if exit_code != 0 and stderr:
            # Only log errors, not successful executions
            log_error(f"Command failed with exit code {exit_code}: {stderr[:500]}")
    except subprocess.TimeoutExpired:
        log_error(f"Process timed out after {timeout} seconds")
        process.kill()
        stdout, stderr = process.communicate()
        exit_code = 124  # Timeout exit code
    except Exception as e:
        log_error(f"Exception during subprocess execution: {e}")
        import traceback
        log_error(f"Traceback: {traceback.format_exc()}")
        return (1, "", str(e))

    return (exit_code, stdout, stderr)


# Direct command execution (non-remote version for use within ray workers)
def run_command_direct(
    command: str,
    volumes: dict[str, dict[str, str]],
    working_dir: str,
    timeout: float | None = None,
    mem_limit: int | None = None,
) -> tuple[int, str, str]:
    """Run a command directly (non-remote version for use within ray workers).

    Args:
        command: The command to run (already extracted from docker format).
        volumes: Dictionary mapping host paths to container paths.
        working_dir: Working directory for the command (container path).
        timeout: Timeout in seconds.
        mem_limit: Memory limit in bytes.

    Returns:
        Tuple of (exit_code, stdout, stderr).
    """
    import logging
    import subprocess
    from pathlib import Path
    
    # Set up logging
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    
    def log_debug(msg):
        logger.debug(msg)
        print(f"[DEBUG] {msg}", flush=True)
    
    def log_error(msg):
        logger.error(msg)
        print(f"[ERROR] {msg}", flush=True)

    # Create mapping from container paths to host paths
    container_to_host = {}
    temp_dir = None
    judge_dir = None
    
    for host_path, volume_info in volumes.items():
        container_path = volume_info.get("bind", "")
        if container_path:
            container_to_host[container_path] = host_path
            
    # Collect all /workdir and /judge host paths to find common directories
    workdir_host_paths = []
    judge_host_paths = []
    
    for host_path, volume_info in volumes.items():
        container_path = volume_info.get("bind", "")
        if container_path.startswith("/workdir"):
            workdir_host_paths.append(Path(host_path))
        elif container_path.startswith("/judge"):
            judge_host_paths.append(Path(host_path))
    
    # Find common temp directory from /workdir paths
    if workdir_host_paths and temp_dir is None:
        # Find common parent of all /workdir host paths
        if len(workdir_host_paths) == 1:
            # Single path: use its parent if it's a file, or the path itself if it's a directory
            p = workdir_host_paths[0]
            temp_dir = p.parent if p.is_file() else p
        else:
            # Multiple paths: find common parent
            common_parts = []
            all_parts = [p.parts for p in workdir_host_paths]
            for parts in zip(*all_parts):
                if len(set(parts)) == 1:
                    common_parts.append(parts[0])
                else:
                    break
            if common_parts:
                temp_dir = Path(*common_parts)
            else:
                # Fallback: use parent of first path
                temp_dir = workdir_host_paths[0].parent if workdir_host_paths[0].is_file() else workdir_host_paths[0]
    
    # Find common judge directory from /judge paths
    if judge_host_paths and judge_dir is None:
        if len(judge_host_paths) == 1:
            p = judge_host_paths[0]
            # For /judge/target/release/tool, we need to extract the base /judge directory
            if "target/release" in str(p):
                if len(p.parents) >= 3:
                    judge_dir = p.parents[2]  # Go up from target/release/tester to tools
                elif len(p.parents) >= 2:
                    judge_dir = p.parents[1]  # Fallback: go up 2 levels
                else:
                    judge_dir = p.parent
            else:
                judge_dir = p.parent if p.is_file() else p
        else:
            # Find common parent, but account for target/release structure
            common_parts = []
            all_parts = [p.parts for p in judge_host_paths]
            for parts in zip(*all_parts):
                if len(set(parts)) == 1:
                    common_parts.append(parts[0])
                else:
                    break
            if common_parts:
                judge_dir = Path(*common_parts)
                # If we're inside target/release, go up to tools directory
                if "target" in common_parts and "release" in common_parts:
                    idx = common_parts.index("target")
                    judge_dir = Path(*common_parts[:idx])
            else:
                judge_dir = judge_host_paths[0].parent if judge_host_paths[0].is_file() else judge_host_paths[0]

    # Map /workdir to temp directory
    if temp_dir is not None:
        container_to_host["/workdir"] = str(temp_dir)
    
    # Map /judge to judge directory
    if judge_dir is not None:
        container_to_host["/judge"] = str(judge_dir)
    
    # Map working directory from container to host
    if working_dir.startswith("/workdir"):
        if temp_dir is not None:
            # Replace /workdir with temp_dir in the working_dir path
            relative_path = working_dir[len("/workdir"):].lstrip("/")
            work_path = temp_dir / relative_path if relative_path else temp_dir
        else:
            work_path = Path("/tmp")
    elif working_dir.startswith("/judge"):
        # For /judge paths, try to find from volumes or use /tmp
        work_path = Path("/tmp")
        for container_path, host_path in container_to_host.items():
            if container_path.startswith("/judge"):
                work_path = Path(host_path).parent if Path(host_path).is_file() else Path(host_path)
                break
    elif working_dir in container_to_host:
        work_path = Path(container_to_host[working_dir])
        if work_path.is_file():
            work_path = work_path.parent
    else:
        work_path = Path(working_dir)
    
    work_path.mkdir(parents=True, exist_ok=True)

    # Replace container paths in command with host paths (longest first to avoid partial matches)
    mapped_command = command
    
    # FIRST: Replace specific volume mappings (longest paths first) - these take precedence
    # This ensures /judge/target/release/tester gets replaced before /judge
    for container_path, host_path in sorted(container_to_host.items(), key=lambda x: len(x[0]), reverse=True):
        # Skip base paths /workdir and /judge - we'll handle those after specific mappings
        if container_path in ["/workdir", "/judge"]:
            continue
        mapped_command = mapped_command.replace(container_path, host_path)
    
    # THEN: Replace base paths /workdir and /judge for any remaining references
    if temp_dir is not None and "/workdir" in mapped_command:
        # Replace /workdir with temp_dir, handling both /workdir/... and standalone /workdir
        mapped_command = mapped_command.replace("/workdir/", f"{temp_dir}/")
        mapped_command = mapped_command.replace("/workdir ", f"{temp_dir} ")
        mapped_command = mapped_command.replace("/workdir", str(temp_dir))
    
    if judge_dir is not None and "/judge" in mapped_command:
        # Replace /judge with judge_dir (only for remaining references not already replaced)
        mapped_command = mapped_command.replace("/judge/", f"{judge_dir}/")
        mapped_command = mapped_command.replace("/judge ", f"{judge_dir} ")
        mapped_command = mapped_command.replace("/judge", str(judge_dir))

    # Prepare command with resource limits
    cmd_parts = ["/bin/sh", "-c", mapped_command]
    
    # Apply memory limit if specified using prlimit
    if mem_limit is not None:
        cmd_parts = ["prlimit", f"--as={mem_limit}", "--"] + cmd_parts
    
    # Verify that any tester/gen/vis binaries in the command actually exist
    # Add retry for NFS sync delays (important for Ray workers on different nodes)
    import time
    for container_path, host_path in container_to_host.items():
        if container_path.endswith("/tester") or container_path.endswith("/gen") or container_path.endswith("/vis"):
            host_path_obj = Path(host_path)
            # Retry check for NFS sync delays
            max_retries = 3
            found = False
            for retry in range(max_retries):
                if host_path_obj.exists() and host_path_obj.is_file():
                    found = True
                    break
                if retry < max_retries - 1:
                    time.sleep(0.1)  # Brief wait for NFS sync
            
            if not found:
                error_msg = (
                    f"Binary not found at expected path: {host_path}\n"
                    f"This binary needs to be built and placed at this location (on NFS).\n"
                    f"Expected path: {host_path}\n"
                    f"Container path: {container_path}\n"
                    f"Parent dir exists: {host_path_obj.parent.exists() if host_path_obj.parent else False}"
                )
                log_error(error_msg)
                return (1, "", error_msg)

    # Run the command
    try:
        process = subprocess.Popen(
            cmd_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(work_path),
            text=True,
        )
        stdout, stderr = process.communicate(timeout=timeout)
        exit_code = process.returncode
        if exit_code != 0 and stderr:
            # Only log errors, not successful executions
            log_error(f"Command failed with exit code {exit_code}: {stderr[:500]}")
    except subprocess.TimeoutExpired:
        log_error(f"Process timed out after {timeout} seconds")
        process.kill()
        stdout, stderr = process.communicate()
        exit_code = 124  # Timeout exit code
    except Exception as e:
        log_error(f"Exception during subprocess execution: {e}")
        import traceback
        log_error(f"Traceback: {traceback.format_exc()}")
        return (1, "", str(e))

    return (exit_code, stdout, stderr)


# Docker (deprecated - kept for compatibility but uses ray instead)
@contextmanager
def docker_client() -> Generator[dict, None, None]:
    """Context manager for Docker client (now uses ray instead).

    Yields:
        dict: A mock docker client interface for compatibility.
    """
    # Return a mock object that provides the same interface
    class MockDockerClient:
        class containers:
            @staticmethod
            def run(*args, **kwargs):
                return MockContainer(*args, **kwargs)

    class MockContainer:
        def __init__(self, *args, **kwargs):
            self.image = kwargs.get("image", "")
            self.command = kwargs.get("command", "")
            self.volumes = kwargs.get("volumes", {})
            self.working_dir = kwargs.get("working_dir", "/tmp")
            self.timeout = kwargs.get("timeout", None)
            self.mem_limit = kwargs.get("mem_limit", None)
            self._exit_code = None
            self._stdout = ""
            self._stderr = ""

        def wait(self, timeout=None):
            # Extract command from the docker command format
            if self.command.startswith("/bin/sh -c '"):
                cmd = self.command[len("/bin/sh -c '"):-1]  # Remove wrapper
            else:
                cmd = self.command

            # Map volumes to actual paths (use host paths directly)
            # For compatibility, we'll use the host paths from volumes
            work_dir = self.working_dir
            if work_dir.startswith("/workdir") or work_dir.startswith("/judge"):
                # Use a temp directory or the actual path
                work_dir = "/tmp"

            # Run via ray
            result = ray.get(
                run_command_remote.remote(
                    command=cmd,
                    volumes=self.volumes,
                    working_dir=work_dir,
                    timeout=timeout or self.timeout,
                    mem_limit=self.mem_limit,
                )
            )
            self._exit_code, self._stdout, self._stderr = result

        def logs(self, stdout=True, stderr=True):
            output = ""
            if stdout:
                output += self._stdout
            if stderr:
                output += self._stderr
            return output.encode("utf-8")

        @property
        def attrs(self):
            return {"State": {"ExitCode": self._exit_code}}

        def remove(self, force=False):
            pass

        @property
        def id(self):
            return "mock-container-id"

    yield MockDockerClient()


# Cache
def get_cache_dir() -> Path:
    """Get the cache directory for ALE-Bench.

    Returns:
        Path: The cache directory.
    """
    cache_dir_str = os.environ.get("ALE_BENCH_CACHE", None)
    if cache_dir_str is None:
        # Use ahc/cache relative to the package location
        # Get the directory where this file (utils.py) is located
        package_dir = Path(__file__).parent
        return package_dir / "cache"
    else:
        return Path(cache_dir_str).expanduser().resolve()


def clear_cache() -> None:
    """Clear the cache directory for ALE-Bench."""
    cache_dir = get_cache_dir()
    if cache_dir.is_dir():
        print(f"Clearing cache directory: {cache_dir}")
        shutil.rmtree(cache_dir)


# Data
def get_local_data_dir() -> Path | None:
    """Get the local data directory for ALE-Bench.

    Returns:
        Path | None: The local data directory. Returns None if not set.
    """
    data_dir_str = os.environ.get("ALE_BENCH_DATA", None)
    if data_dir_str is None:
        return None
    data_dir = Path(data_dir_str).expanduser().resolve()
    if not data_dir.is_dir():
        print(f"Data directory does not exist: {data_dir}")
        return None
    return data_dir


def dir_tree(
    dir_path: Path,
    prefix: str = "",
) -> Generator[str, None, None]:
    """Generate a tree structure of the directory.

    Args:
        dir_path (Path): The path to the directory.
        prefix (str, optional): The prefix for the tree structure. Defaults to "".

    Yields:
        str: The tree structure of the directory.
    """
    if not dir_path.is_dir():
        raise ValueError(f"{dir_path} is not a directory.")
    tee = "├── "
    last = "└── "
    branch = "│   "
    space = "    "
    contents = list(dir_path.iterdir())
    pointers = [tee] * (len(contents) - 1) + [last]
    for pointer, path in zip(pointers, contents):
        yield prefix + pointer + path.name
        if path.is_dir():
            extension = branch if pointer == tee else space
            yield from dir_tree(path, prefix + extension)


def print_dir_tree(dir_path: Path) -> None:
    """Print the tree structure of the directory.

    Args:
        dir_path (Path): The path to the directory.
    """
    for line in dir_tree(dir_path):
        print(line)


# Problem
def text_image_contents_to_openai(contents: list[str | Image.Image]) -> list[dict[str, str | dict[str, str]]]:
    """Convert the contents to OpenAI format.

    Args:
        contents (list[str | Image.Image]): The contents to convert.

    Returns:
        list[dict[str, str]]: The converted contents.
    """
    openai_contents: list[dict[str, str | dict[str, str]]] = []
    for content in contents:
        if isinstance(content, str):
            openai_contents.append({"type": "text", "text": content})
        elif isinstance(content, Image.Image):
            openai_contents.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{pil_to_base64jpeg(content)}"},
                }
            )
        else:
            raise ValueError("The content is not a str or a PIL.Image.Image.")
    return openai_contents


def parse_statement(
    statement: str,
    images: dict[str, Image.Image | list[Image.Image]],
    ignore_video: bool = False,
    extract_video_frame: Literal["first", "last", "all"] = "all",
    return_openai: bool = False,
) -> list[str | Image.Image] | list[dict[str, str | dict[str, str]]]:
    """Parse the problem statement and images and return a list of contents.
    Images are interleaved with the text in the statement.

    Args:
        statement (str): The problem statement.
        images (dict[str, Image.Image | list[Image.Image]]): The images with their names.
            The keys are the image names int the statement and the values are the images or a list of images.
        ignore_video (bool, optional): If True, ignore video frames. Defaults to False.
        extract_video_frame (Literal["first", "last", "all"], optional): The video frame to extract.
            Defaults to "all". If ignore_video is True, this argument is ignored.
            If "first", extract the first frame. If "last", extract the last frame. If "all", extract all frames.
        return_openai (bool, optional): If True, convert the contents to OpenAI format. Defaults to False.

    Returns:
        list[str | Image.Image] | list[dict[str, str | dict[str, str]]]:
            A list of contents, where each content is either a text or an image.
    """
    # Search for image names in the statement by using Aho-Corasick algorithm
    kwtree = KeywordTree(case_insensitive=False)
    for image_name in images:
        if isinstance(images[image_name], list) and ignore_video:
            continue  # Ignore video
        kwtree.add(image_name)
    kwtree.finalize()
    matches = kwtree.search_all(statement)

    # If no image names are found, return the statement as is
    contents: list[str | Image.Image] = []
    if matches is None:  # No image names found in the statement
        contents.append(statement)
        if return_openai:
            return text_image_contents_to_openai(contents)
        return contents

    # Interleave the images with the text in the statement
    matches = sorted(matches, key=lambda x: x[1])  # Sort by the start index
    current_idx = 0
    for matched_image, idx in matches:
        contents.append(statement[current_idx:idx])
        image = images[matched_image]
        if isinstance(image, list):
            if extract_video_frame == "first":
                contents.append(image[0])
            elif extract_video_frame == "last":
                contents.append(image[-1])
            elif extract_video_frame == "all":
                for frame in image:
                    contents.append(frame)
            else:
                raise ValueError(f"`extract_video_frame` must be 'first', 'last', or 'all'. Got: {extract_video_frame}")
        else:
            contents.append(image)
        current_idx = idx + len(matched_image)
    contents.append(statement[current_idx:])

    # Convert the contents to OpenAI format if requested
    if return_openai:
        return text_image_contents_to_openai(contents)
    return contents


# Session
def find_free_port(min_port: int = 9000, max_port: int = 65535) -> int:
    """Find a free port in the specified range.

    Args:
        min_port (int, optional): Minimum port number. Defaults to 9000.
        max_port (int, optional): Maximum port number. Defaults to 65535.

    Returns:
        int: A free port number.

    Raises:
        RuntimeError: If no free port is found in the specified range.
    """
    ports = list(range(min_port, max_port + 1))
    random.shuffle(ports)
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free ports found in range {min_port}-{max_port}.")


# Image
def base64_to_pil(base64_str: str) -> Image.Image:
    """Convert a base64 string to a PIL image.

    Args:
        base64_str (str): The base64 string of the image.

    Returns:
        Image.Image: The PIL image.
    """
    image_data = base64.b64decode(base64_str)
    image = Image.open(io.BytesIO(image_data))
    return image.convert(image.mode)  # NOTE: to create a new Image instance (not subclasses like PngImageFile)


def pil_to_base64(image: Image.Image, image_format: Literal["JPEG", "PNG"] = "PNG") -> str:
    """Convert a PIL image to a base64 string.

    Args:
        image (Image.Image): The PIL image.
        image_format (Literal["JPEG", "PNG"]): The format to save the image in. Defaults to "PNG".

    Returns:
        str: The base64 string of the image.
    """
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def pil_to_base64jpeg(image: Image.Image) -> str:
    """Convert a PIL image to a base64 string of a JPEG image.

    Args:
        image (Image.Image): The PIL image.

    Returns:
        str: The base64 string of the JPEG image.
    """
    return pil_to_base64(image.convert("RGB"), image_format="JPEG")


def read_svg(svg_text: str, size: int | tuple[int, int] = 1000) -> Image.Image:
    """Read an SVG text and return a PIL image.

    Args:
        svg_text (str): The SVG text.
        size (int | tuple[int, int], optional): The size of the output image. Defaults to 1000.
            If it is an integer, the output image will be a square. If it is a tuple, (width, height) will be used.

    Returns:
        Image.Image: The PIL image of the SVG.

    Raises:
        ValueError: If the SVG text is empty.
    """
    if len(svg_text) == 0:
        raise ValueError("SVG text is empty.")
    if isinstance(size, int):
        size = (size, size)
    width, height = size
    buffer = io.BytesIO()
    cairosvg.svg2png(
        bytestring=svg_text.encode("utf-8"),
        output_width=width,
        output_height=height,
        background_color="white",
        write_to=buffer,
    )
    return Image.open(buffer).convert("RGB")
