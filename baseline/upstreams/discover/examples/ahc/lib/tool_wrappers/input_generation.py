import io
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .. import constants
from ..error import AleBenchError

import modal


class HostPathsGen(BaseModel):
    """Paths on the host for the generator tool."""

    model_config = ConfigDict(frozen=True)

    seeds_file: Path = Field(description="The seeds file")
    input_dir: Path = Field(description="The directory for the generated input cases")


def setup_paths_gen(temp_dir: Path, seeds: list[int]) -> HostPathsGen:
    """Setup the paths for the generator tool."""
    seeds_file = temp_dir / constants.SEEDS_FILE.split("/")[-1]
    seeds_file.write_text("\n".join([str(seed) for seed in seeds]) + "\n")
    input_dir = temp_dir / constants.IN_DIR.split("/")[-1]
    input_dir.mkdir()
    return HostPathsGen(seeds_file=seeds_file, input_dir=input_dir)


def get_gen_volumes(host_paths: HostPathsGen, tool_dir: Path) -> dict[str, dict[str, str]]:
    """
    Kept for compatibility; Modal path ignores mounts/volumes.
    """
    return {
        str(host_paths.seeds_file): {"bind": constants.SEEDS_FILE, "mode": "ro"},
        str(host_paths.input_dir): {"bind": f"{constants.IN_DIR}", "mode": "rw"},
        str(tool_dir / "tools" / "target" / "release" / "gen"): {"bind": constants.GEN_BIN, "mode": "ro"},
    }


def build_gen_command(gen_kwargs: dict[str, Any]) -> str:
    """Build the command for the generator tool."""
    gen_command = constants.GEN_BIN
    for key, value in gen_kwargs.items():
        if key == "dir":
            warnings.warn("`dir` is a reserved keyword and will be ignored.")
            continue
        gen_command += f" --{key}={value}"
    gen_command += f" {constants.SEEDS_FILE}"
    return gen_command


def _tar_gz_dir_bytes(src_dir: Path) -> bytes:
    """
    Create a tar.gz of src_dir (including the top-level folder).
    Extracting under /tmp yields /tmp/<src_dir.name>/...
    """
    src_dir = src_dir.resolve()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(src_dir, arcname=src_dir.name)
    return buf.getvalue()


def _read_modal_stream_to_str(stream) -> str:
    """
    Modal's sandbox process stdout/stderr can be either:
      - a file-like object returning bytes
      - a file-like object returning str
      - already a str
    This normalizes to a Python str.
    """
    if stream is None:
        return ""
    # Already a string
    if isinstance(stream, str):
        return stream
    # File-like with read()
    if hasattr(stream, "read"):
        data = stream.read()
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        if isinstance(data, str):
            return data
        return str(data)
    # Fallback
    return str(stream)


def _run_gen_in_modal_sandbox(
    *,
    gen_command: str,
    timeout: int,
    seeds_text: str,
    tool_dir: Path,
) -> list[tuple[str, str]]:
    """
    Modal Sandbox implementation.
    Strategy:
      1) If host binary exists at tool_dir/tools/target/release/gen, upload it.
      2) Else upload tool_dir/tools source tree, cargo build --release in sandbox,
         then run the built gen.

    Returns: [(filename, contents), ...] for generated IN_DIR/*.txt
    """
    app = modal.App.lookup("ale-bench-gen", create_if_missing=True)

    image = modal.Image.from_registry(
        constants.RUST_TOOL_DOCKER_IMAGE,
        add_python=False,
    )

    mem_mib = int(constants.MAX_MEMORY_LIMIT // (1024 * 1024))

    # Add significant buffer time for file reading operations (5 minutes)
    # This ensures the sandbox stays alive long enough to read all generated files
    # Modal sandboxes can terminate after the command completes, so we need extra time
    sandbox_timeout = max(timeout + 300, 600)  # At least 10 minutes, or timeout + 5 minutes

    sb = modal.Sandbox.create(
        app=app,
        image=image,
        cpu=1,
        memory=mem_mib,
        timeout=sandbox_timeout,
        workdir=constants.WORK_DIR,
    )

    try:
        seeds_path = constants.SEEDS_FILE
        in_dir = constants.IN_DIR
        gen_bin_dst = constants.GEN_BIN

        # Ensure dirs exist
        sb.exec(
            "/bin/sh",
            "-c",
            f"mkdir -p '{Path(seeds_path).parent}' '{Path(gen_bin_dst).parent}' '{in_dir}'",
            timeout=30,
        ).wait()

        # Write seeds file
        with sb.open(seeds_path, "w") as f:
            f.write(seeds_text)

        # Prebuilt binary path (if present)
        host_gen_bin = (tool_dir / "tools" / "target" / "release" / "gen").resolve()
        host_tools_dir = (tool_dir / "tools").resolve()

        if host_gen_bin.exists():
            # Upload binary into sandbox
            with sb.open(gen_bin_dst, "wb") as f:
                f.write(host_gen_bin.read_bytes())
            sb.exec("/bin/sh", "-c", f"chmod +x '{gen_bin_dst}'", timeout=30).wait()
        else:
            # Upload tools/ source and build in sandbox
            if not host_tools_dir.exists():
                raise AleBenchError(f"Could not find tools directory to build generator: {host_tools_dir}")

            tools_tgz = _tar_gz_dir_bytes(host_tools_dir)
            remote_tgz = "/tmp/tools.tar.gz"
            with sb.open(remote_tgz, "wb") as f:
                f.write(tools_tgz)

            sb.exec("/bin/sh", "-c", "mkdir -p /tmp && tar -xzf /tmp/tools.tar.gz -C /tmp", timeout=120).wait()

            # Build; if your image doesn't have cargo, stderr will say so
            build_cmd = "cd /tmp/tools && cargo build --release"
            bp = sb.exec("/bin/sh", "-c", build_cmd, timeout=max(timeout, 600))
            build_exit = bp.wait()
            build_stderr = _read_modal_stream_to_str(bp.stderr).strip()
            if build_exit != 0:
                msg = "Failed to build generator with cargo in Modal sandbox."
                if build_stderr:
                    msg += f"\nStandard error:\n{build_stderr}"
                raise AleBenchError(msg)

            # Copy built binary to expected GEN_BIN location
            cp_cmd = f"cp -f '/tmp/tools/target/release/gen' '{gen_bin_dst}' && chmod +x '{gen_bin_dst}'"
            sb.exec("/bin/sh", "-c", cp_cmd, timeout=60).wait()

        # Run generator
        p = sb.exec("/bin/sh", "-c", gen_command, timeout=timeout)
        exit_code = p.wait()
        stderr = _read_modal_stream_to_str(p.stderr).strip()

        if exit_code != 0:
            if stderr:
                raise AleBenchError(f"Failed to generate the case. The standard error is:\n{stderr}")
            raise AleBenchError("Failed to generate the case.")

        # List outputs and read files immediately to avoid sandbox termination
        # We do this in one go to minimize the time between generation and file reading
        ls = sb.exec("/bin/sh", "-c", f"ls -1 '{in_dir}'/*.txt 2>/dev/null || true", timeout=30)
        ls.wait()
        out = _read_modal_stream_to_str(ls.stdout).strip()
        paths = [line.strip() for line in out.splitlines() if line.strip()]

        if not paths:
            raise AleBenchError("No generated input files found in the output directory.")

        # Read all files immediately, storing contents in memory
        # We read files as quickly as possible to avoid sandbox termination
        generated: list[tuple[str, str]] = []
        failed_files: list[str] = []
        
        for path in sorted(paths):
            name = Path(path).name
            try:
                # Read file immediately - don't delay
                with open(path, "r") as f:
                    content = f.read()
                    generated.append((name, content))
            except Exception as e:
                # Catch any exception (NotFoundError, container finished, etc.)
                error_str = str(e).lower()
                failed_files.append(name)
                # If this is a container termination error, we need to stop and report
                if "container" in error_str and ("finished" in error_str or "terminated" in error_str or "has finished" in error_str):
                    if generated:
                        raise AleBenchError(
                            f"Modal sandbox terminated while reading files. "
                            f"Successfully read {len(generated)}/{len(paths)} files. "
                            f"Failed to read: {', '.join(failed_files)}. "
                            f"Error: {e}"
                        )
                    else:
                        raise AleBenchError(
                            f"Modal sandbox terminated before any files could be read. "
                            f"Expected {len(paths)} files. Error: {e}"
                        )
                # For other errors, continue trying to read remaining files
                # but we'll raise an error at the end if any failed
        
        # If we couldn't read all files, raise an error
        if failed_files:
            raise AleBenchError(
                f"Failed to read {len(failed_files)} file(s): {', '.join(failed_files)}. "
                f"Successfully read {len(generated)}/{len(paths)} files."
            )

        return generated

    except modal.exception.TimeoutError:
        raise AleBenchError(f"Failed to generate the case. Timeout after {timeout} seconds.")
    except modal.exception.NotFoundError as e:
        raise AleBenchError(
            f"Modal sandbox terminated prematurely. This may happen if the sandbox times out "
            f"or is terminated before file operations complete. Error: {e}"
        )
    finally:
        try:
            # Terminate the sandbox if it's still running
            # If it's already terminated, this will raise an exception which we ignore
            sb.terminate()
            # Give a moment for async connections to close properly
            time.sleep(0.2)
        except Exception:
            # Sandbox may already be terminated, which is fine
            pass


def run_gen_container(
    gen_volumes: dict[str, dict[str, str]],
    gen_command: str,
    timeout: int,
    *,
    tool_dir: Path,
    seeds_text: str,
) -> list[tuple[str, str]]:
    """
    Modal replacement for Docker runner.
    gen_volumes is accepted but ignored.
    """
    _ = gen_volumes
    return _run_gen_in_modal_sandbox(
        gen_command=gen_command,
        timeout=timeout,
        seeds_text=seeds_text,
        tool_dir=tool_dir,
    )


def generate_inputs(seeds: list[int], gen_kwargs: dict[str, Any], tool_dir: Path) -> list[str]:
    """Generate input cases using the generator tool (Modal backend)."""
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)

        gen_host_paths = setup_paths_gen(temp_dir, seeds)
        gen_volumes = get_gen_volumes(gen_host_paths, tool_dir)  # ignored by Modal, kept for API compatibility
        gen_command = build_gen_command(gen_kwargs)

        timeout = constants.GENERATION_TIMEOUT
        seeds_text = gen_host_paths.seeds_file.read_text()

        generated_files = run_gen_container(
            gen_volumes,
            gen_command,
            timeout,
            tool_dir=tool_dir,
            seeds_text=seeds_text,
        )

        # Materialize locally + preserve naming assertions
        for name, contents in generated_files:
            (gen_host_paths.input_dir / name).write_text(contents)

        input_files = sorted(list(gen_host_paths.input_dir.glob("*.txt")))
        generated_cases: list[str] = []
        for idx, input_file in enumerate(input_files):
            assert input_file.name == f"{idx:04d}.txt", (
                "The generated case files must be named `0000.txt`, `0001.txt`, ..."
            )
            generated_cases.append(input_file.read_text())

    return generated_cases