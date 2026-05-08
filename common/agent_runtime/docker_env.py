import errno
import fcntl
import logging
import os
import pty
import re
import select
import time
from re import sub
import shlex
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Union
import shutil
from pathlib import Path
import docker
from docker.errors import APIError, NotFound
from common.utils.container_paths import opaque_token

SHELL_WRAPPER_FALSE_POSITIVE_RETURNCODES = {126, 255}
TARGET_CONNECTIVITY_ERROR_MARKERS = (
    "connection refused",
    "connection timed out",
    "timed out",
    "no route to host",
    "network is unreachable",
)


def is_shell_wrapper_false_positive(command: str, output: str, returncode: int) -> bool:
    normalized_command = command.lower()
    if "nc" not in normalized_command:
        return False
    if "| head" not in normalized_command and "head -c" not in normalized_command:
        return False
    if returncode not in SHELL_WRAPPER_FALSE_POSITIVE_RETURNCODES:
        return False

    normalized_output = output.lower()
    if "operation not permitted" not in normalized_output:
        return False

    if "exec /usr/bin/bash" in normalized_output or "exec /usr/bin/sh" in normalized_output:
        return True

    return "failed to run command" in normalized_output and "bash" in normalized_output


def sanitize_agent_false_positive_output(command: str, output: str) -> str:
    del command
    del output
    return "[SYSTEM] command hit a local shell-wrapper false positive; try another read method\n"


def is_target_connectivity_error(output: str, returncode: int) -> bool:
    if returncode == 0:
        return False
    normalized_output = output.lower()
    return any(marker in normalized_output for marker in TARGET_CONNECTIVITY_ERROR_MARKERS)


@dataclass
class DockerEnvironmentConfig:
    image: str
    cwd: str = "/"
    """Working directory in which to execute commands."""
    network_name: str | None = None
    """docker network to connect to"""
    network_ipv4_address: str | None = None
    """Optional static IPv4 address to use when connecting to network_name."""
    env: dict[str, str] = field(default_factory=dict)
    """Environment variables to set in the container."""
    forward_env: list[str] = field(default_factory=list)
    """Environment variables to forward to the container.
    Variables are only forwarded if they are set in the host environment.
    In case of conflict with `env`, the `env` variables take precedence.
    """
    timeout: int = 30
    """Timeout for executing commands in the container."""
    executable: str = os.getenv("DOCKER_EXECUTABLE", "docker")
    """Path to the docker/container executable."""
    run_args: list[str] = field(default_factory=lambda: ["--rm"])
    """Additional arguments to pass to the docker/container executable.
    Default is ["--rm"], which removes the container after it exits.
    """
    container_timeout: str = "2h"
    """Max duration to keep container running. Uses the same format as the sleep command."""
    pull_timeout: int = 300
    """Timeout in seconds for pulling images."""
    container_name: str | None = None
    """Name for the container. If specified, will use an existing container
    with this name if available, or create a persistent container with this name.
    If not specified, creates a temporary container with a random name."""
    is_persistent: bool = False


class DockerEnvironment:
    def __init__(self, *, config: DockerEnvironmentConfig, logger: logging.Logger | None = None):
        """This class executes bash commands in a Docker container using direct docker commands.
        See `DockerEnvironmentConfig` for keyword arguments.
        """
        self.logger = logger or logging.getLogger("ctfenv")
        self.container_id: str | None = None
        self.config = config
        # Tracks the per-challenge runtime network we last attached to, so
        # successive `_sync_runtime_network` calls can detach the previous one
        # before attaching the new one. `config.network_name` remains the base
        # shared network (e.g. "ctfnet") and is not mutated at runtime.
        self._runtime_network_name: str | None = None
        self._start_container()
        self._wait_for_container_ready()
        if self.config.network_name:
            self._connect_to_network(self.config.network_name, self.config.network_ipv4_address)

    def get_template_vars(self) -> dict[str, Any]:
        return asdict(self.config)

    def _start_container(self):
        """Start the Docker container and return the container ID."""
        if self.config.container_name:
            container_name = f"{self.config.container_name}"
            # Check if container with specified name already exists
            try:
                cmd = [
                    self.config.executable,
                    "ps",
                    "-a",
                    "--filter",
                    f"name={container_name}",
                    "--format",
                    "{{.ID}}"
                ]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                existing_container_id = result.stdout.strip()
                
                if existing_container_id:
                    # Container exists, check if it's running
                    self.logger.info(f"Found existing container {self.config.container_name} with ID {existing_container_id}")
                    
                    # Check if container is running
                    cmd = [
                        self.config.executable,
                        "ps",
                        "--filter",
                        f"name={container_name}",
                        "--format",
                        "{{.ID}}"
                    ]
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    is_running = existing_container_id in result.stdout.strip() 
                    
                    if not is_running:
                        # rm the existing container
                        cmd = [self.config.executable, "rm", existing_container_id] # There maybe something wrong becasuse persistent container must be running
                        subprocess.run(cmd, check=True)
                        self.logger.info(f"Remove existing container {self.config.container_name}. There maybe something wrong becasuse persistent container must be running")
                    else:
                        self.container_id = existing_container_id
                        return
            except subprocess.CalledProcessError as e:
                self.logger.warning(f"Error checking for existing container: {e.stderr.strip()}")
            
            # Container doesn't exist, create a new persistent one
            self.logger.info(f"Creating new persistent container {self.config.container_name}")
            cmd = [
                self.config.executable,
                "run",
                "-d",
                "--name",
                container_name,
                "-w",
                self.config.cwd,
                "--memory","1g",
                *self.config.run_args,
                self.config.image,
                "tail", "-f", "/dev/null" # Keep container alive unless manually stopped
            ]
        else:
            # Create a temporary container with random name
            container_name = f"ctfenv-{uuid.uuid4().hex[:8]}"
            cmd = [
                self.config.executable,
                "run",
                "-d",
                "--name",
                container_name,
                "-w",
                self.config.cwd,
                "--memory", "1g",
                *self.config.run_args,
                self.config.image,
                "sleep",
                self.config.container_timeout,
            ]
        
        self.logger.debug(f"Starting container with command: {shlex.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.config.pull_timeout,  # docker pull might take a while
            check=True,
        )
        self.container_id = result.stdout.strip()
        self.logger.info(f"Started container with ID {self.container_id}")

    def _connect_to_network(self, network_name: str, ipv4_address: str | None = None):
        """Connect the container to a Docker network (e.g., 'ctfnet')."""
        if not self.container_id:
            return
        # docker network inspect ctfnet --format '{{range $id, $cfg := .Containers}}{{slice $id 0 12}}{{" "}}{{end}}'
        try:
            cmd_inspect = [
            self.config.executable,
            "network",
            "inspect",
            network_name,
            "--format",  "{{range $id,$cfg:=.Containers}}{{slice $id 0 12}}\n{{end}}"
            ]
            result = subprocess.run(cmd_inspect, capture_output=True, text=True)
            if result.returncode != 0:
                self.logger.warning(f"Failed to inspect container {self.container_id}: {result.stderr.strip()}")
                return
            if self.container_id in result.stdout.strip():
                self.logger.info(f"Container {self.container_id} is already connected to network {network_name}")
            else:
                cmd = [self.config.executable, "network", "connect"]
                if ipv4_address:
                    cmd.extend(["--ip", ipv4_address])
                cmd.extend([network_name, self.container_id])
                subprocess.run(cmd, check=True, capture_output=True, text=True)
                self.logger.info(f"Connected container {self.container_id} to network '{network_name}'")
        except subprocess.CalledProcessError as e:
            self.logger.warning(f"Failed to connect to network '{network_name}': {e.stderr.strip()}")

    def _disconnect_from_network(self, network_name: str) -> None:
        """Disconnect the container from a Docker network, ignoring failures.

        Used when switching runtime networks between challenges so the agent
        container does not leak traffic onto a prior challenge's subnet.
        """
        if not self.container_id or not network_name:
            return
        cmd = [
            self.config.executable,
            "network",
            "disconnect",
            network_name,
            self.container_id,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            self.logger.info(
                f"Disconnected container {self.container_id} from network '{network_name}'"
            )
        else:
            # Common benign cases: network already gone, or endpoint absent.
            self.logger.debug(
                f"Could not disconnect {self.container_id} from '{network_name}': "
                f"{result.stderr.strip()}"
            )

    def _wait_for_container_ready(self, timeout: float = 15.0, poll_interval: float = 0.5):
        """Wait until the container's writable layer is ready for cp/exec.

        ``docker run -d`` returns the container ID before the RW layer is
        fully initialised, which causes ``docker cp`` to fail with
        "RWLayer ... is unexpectedly nil".  Poll ``docker inspect`` until
        the container reports ``running`` state.
        """
        if not self.container_id:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = subprocess.run(
                    [self.config.executable, "inspect",
                     "--format", "{{.State.Running}}", self.container_id],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and "true" in result.stdout.lower():
                    return
            except subprocess.TimeoutExpired:
                pass
            time.sleep(poll_interval)
        self.logger.warning(
            "Container %s did not reach 'running' state within %.0fs",
            self.container_id, timeout,
        )

    def cp_to_container(self, src: Union[str, Path], dst: str, *, timeout: int | None = None) -> None:
        """
        Copy file/folder from host to container.
        Automatically detects if src is a directory and performs recursive copy.
        
        Args:
            src: Host path (str or Path)
            dst: Container path (e.g., '/home/user/target/')
            timeout: Optional docker cp timeout in seconds.
        """
        assert self.container_id, "Container not started"
        
        # Resolve to absolute path to avoid ambiguity
        src = Path(src).resolve()

        if not src.exists():
            raise FileNotFoundError(f"Source path does not exist: {src}")

        # Detect type for better logging and validation
        if src.is_dir():
            # Directory: docker cp works recursively by default
            log_msg = f"Copied directory (recursive): {src} → container:{dst}"
        elif src.is_file():
            # File
            log_msg = f"Copied file: {src} → container:{dst}"
        else:
            # Symlink, Socket, etc.
            log_msg = f"Copied item: {src} → container:{dst}"

        # Construct command
        # Note: docker cp handles both files and directories natively
        cmd = [self.config.executable, "cp", str(src), f"{self.container_id}:{dst}"]

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
            self.logger.debug(log_msg)
        except subprocess.TimeoutExpired as e:
            raise TimeoutError(
                f"Timed out copying {src} to {self.container_id}:{dst} "
                f"after {timeout}s"
            ) from e
        except subprocess.CalledProcessError as e:
            # Capture stderr for better debugging
            err_msg = e.stderr.strip() if e.stderr else "Unknown Docker error"
            raise RuntimeError(
                f"Failed to copy {src} to {self.container_id}:{dst}\n"
                f"Command: {' '.join(cmd)}\n"
                f"Error: {err_msg}"
            ) from e

    def cp_from_container(self, src: str, dst: Union[str, Path]) -> None:
        """
        Copy file/folder from container to host.
        Args:
            src: Container path (e.g., '/flag.txt')
            dst: Host path
        """
        assert self.container_id, "Container not started"
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.config.executable, "cp", f"{self.container_id}:{src}", str(dst)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        self.logger.debug(f"Copied container:{src} → {dst}")

    def _sync_runtime_network(self, runtime_context: dict[str, Any] | None) -> None:
        """Attach the agent container to the challenge's runtime network.

        For benchmarks where the subnet itself is part of the challenge
        (autopenbench, cvebench), each challenge lives on its own runtime
        network. When we move between challenges we must disconnect from the
        previous runtime network first, otherwise the container ends up
        multi-homed and leaks traffic across challenges.

        The base `config.network_name` (e.g. the shared "ctfnet") is left
        untouched — only the per-challenge runtime attachment is swapped.
        """
        if not runtime_context:
            return

        runtime = dict(runtime_context.get("runtime", {}) or {})
        network_name = runtime.get("network_name")
        if not network_name:
            return
        network_name = str(network_name)

        # Already attached to this runtime network — nothing to do.
        current_runtime_network = getattr(self, "_runtime_network_name", None)
        if network_name == current_runtime_network:
            return
        # Runtime network equals the base shared network; no per-challenge
        # switch is needed (nyu_ctf-style flat topology).
        if network_name == self.config.network_name:
            self._runtime_network_name = None
            return

        # Drop the previous runtime network before adding the new one.
        if current_runtime_network and current_runtime_network != network_name:
            self._disconnect_from_network(current_runtime_network)

        self._connect_to_network(network_name, self.config.network_ipv4_address)
        self._runtime_network_name = network_name

    def _prepare_challenge_files(self, challenge: dict) -> str:
        """
        Copy all challenge files (based on challenge)
        into a dedicated container directory: `/ctf/{dir_name}`

        Returns:
            str: the dir name, e.g., `ctfenv_cb-gla-web-challenge1_a1b2c3d4`
        """

        challenge_info = challenge
        challenge_id = challenge_info.get("id")
        file_paths = challenge_info.get("files", [])
        if not file_paths:
            self.logger.warning(f"No 'file' field in challenge {challenge_id}")

        benchmark_family = str(challenge_info.get("benchmark_family", "") or "").lower()
        if benchmark_family == "cvebench":
            challenge_token = opaque_token(str(challenge_id or ""))
        else:
            challenge_token = str(challenge_id or "challenge")
        dir_name = f"ctfenv_{challenge_token}_{uuid.uuid4().hex[:8]}"
        container_dir = f"/ctf/{dir_name}"
        host_tmp_dir = Path("/tmp") / dir_name

        try:
            # Step 1: Create temp host dir and copy all files there
            host_tmp_dir.mkdir(parents=True, exist_ok=True)
            for rel_path in file_paths:
                src = Path(challenge_info["full_path"] +"/"+ rel_path)
                if not src.exists():
                    self.logger.error(f"Challenge file missing: {src}")
                    continue
                dst = host_tmp_dir / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.is_file():
                    shutil.copy2(src, dst)
                else:
                    shutil.copytree(src, dst, dirs_exist_ok=True)

            # Step 2: Create target dir in container
            self.execute(f"mkdir -p {container_dir}")

            # Step 3: Copy entire dir into container
            self.cp_to_container(host_tmp_dir, "/ctf/")

            self.logger.info(f"Copied challenge files to container:{container_dir}")
            return dir_name

        finally:
            # Cleanup host temp dir
            if host_tmp_dir.exists():
                shutil.rmtree(host_tmp_dir, ignore_errors=True)
                
    def _run_command(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        append_timeout_message: bool,
        exception_output: str,
    ) -> dict[str, Any]:
        self.logger.debug(f"Env execute command: {command} with cwd :{cwd} timeout: {timeout}")
        inner_command = command
        host_timeout = timeout

        if timeout:
            host_timeout = timeout + 5
            safe_cmd = shlex.quote(command)
            # Use setsid + timeout --signal=KILL to ensure ALL child processes
            # (including forked daemons/listeners) are killed when timeout fires.
            # Without this, `timeout` only kills the direct bash child, leaving
            # orphaned processes that hold the docker exec stdout pipe open forever.
            inner_command = f"setsid timeout --signal=KILL {timeout}s bash -c {safe_cmd}"

        cmd = [self.config.executable, "exec", "-w", cwd]
        cmd.extend([self.container_id, "bash", "-lc", inner_command])

        try:
            result = subprocess.run(
                cmd,
                text=True,
                timeout=host_timeout,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if timeout and result.returncode == 124:
                self.logger.error(f"Docker command timed out for {cmd}")
                output = result.stdout
                if append_timeout_message:
                    output = f"{output} [SYSTEM] Docker command timed out"
                return {"output": output, "returncode": result.returncode}
            return {"output": result.stdout, "returncode": result.returncode}
        except Exception as e:
            self.logger.error(f"Unexcepted error running docker command: {e}")
            return {"output": exception_output or f"{e}", "returncode": -1}

    def agent_execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a command in the Docker container and return the result as a dict."""
        self._sync_runtime_network(runtime_context)
        result = self._run_command(
            command,
            cwd,
            timeout=timeout,
            append_timeout_message=True,
            exception_output="Unexcepted error running docker command",
        )
        if timeout and result["returncode"] == 124:
            return result

        if is_shell_wrapper_false_positive(command, result["output"], result["returncode"]):
            self.logger.warning(
                "Sanitized local shell-wrapper false positive for command %r. Raw output: %r",
                command,
                result["output"],
            )
            return {
                "output": sanitize_agent_false_positive_output(command, result["output"]),
                "returncode": result["returncode"],
            }

        coordinator = getattr(self, "runtime_coordinator", None)
        if (
            runtime_context is not None
            and coordinator is not None
            and is_target_connectivity_error(result["output"], result["returncode"])
        ):
            recovery = coordinator.recover_and_refresh(
                runtime_context,
                reason=result["output"].strip() or "connectivity failure",
            )
            if recovery.recovered:
                retried = self._run_command(
                    command,
                    cwd,
                    timeout=timeout,
                    append_timeout_message=True,
                    exception_output="Unexcepted error running docker command",
                )
                if retried["returncode"] == 0:
                    retried["output"] = f"[SYSTEM] target recovered; retried once\n{retried['output']}"
                return retried
        return result
              
    def execute(self, command: str, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        """Execute a command in the Docker container and return the result as a dict."""
        result = self._run_command(
            command,
            cwd,
            timeout=timeout,
            append_timeout_message=False,
            exception_output="",
        )
        if result["returncode"] == -1 and not result["output"]:
            return {"output": "Unexcepted error running docker command", "returncode": -1}
        return result

    def open_persistent_shell(
        self,
        *,
        cwd: str = "",
        timeout_start: float = 5.0,
    ) -> "PersistentShell":
        """Open a long-lived bash session inside the container via a host PTY.

        Use this when the agent needs to run multi-step interactive commands
        (e.g. ``ssh user@host`` followed by a separate password prompt).
        Each ``run()`` call sees the same shell state (cwd, env, ssh
        session). The shell is *not* automatically closed; the caller is
        responsible for ``shell.close()`` in a ``finally`` block.
        """
        assert self.container_id, "Container not started"
        shell = PersistentShell(
            container_id=self.container_id,
            executable=self.config.executable,
            cwd=cwd or self.config.cwd,
            logger=self.logger,
        )
        shell.start(timeout=timeout_start)
        return shell

    def cleanup(self, *, force: bool = False):
        """Stop and remove the Docker container."""
        if getattr(self, "container_id", None) is not None:  # if init fails early, container_id might not be set
            if self.config.container_name and self.config.is_persistent and not force:
                return
            container_id = self.container_id
            subprocess.run(
                [self.config.executable, "rm", "-f", container_id],
                check=False,
                capture_output=True,
                text=True,
            )
            self.container_id = None

    def __del__(self):
        """Cleanup container when object is destroyed."""
        self.cleanup()

    def mkdir(self, path: str):
        self.execute(f"mkdir -p {path}")

    def symlink_dir_content(self, src_dir: str, dst_dir: str):
        """
        Recursively create symlinks with cp -rs.
        dst_dir appears to contain every file from src_dir, but entries are links.
        src_dir must be an absolute path inside the container.
        """
        # Ensure the destination directory exists.
        self.execute(f"mkdir -p {dst_dir}")
        # -r: recursive, -s: symlink.
        self.execute(f"cp -rs {src_dir}/* {dst_dir}/")

    def exists(self, path: str) -> bool:
        res = self.execute(f"test -e {path}")
        return res['returncode'] == 0
    
    def hardlink_dir_content(self, src_dir: str, dst_dir: str):
        """
        Recursively create the directory tree with `cp -rl` and hard-link files.
        dst_dir gets the same directory tree as src_dir, while files are hard
        links to save space. src_dir and dst_dir must be on the same filesystem.
        """
        # Ensure the destination directory exists.
        self.execute(f"mkdir -p {dst_dir}")
        
        # Use cp -rl: recursive traversal with hard-linked files.
        cmd = f"cp -rl {src_dir}/* {dst_dir}/ 2>/dev/null || true"
        result = self.execute(cmd)
        
        # Empty directories or unmatched globs can make GNU cp fail; || true
        # keeps execution non-fatal. More complex rsync/find fallbacks are avoided.
        
        self.logger.debug(f"Hard-linked {src_dir}/* → {dst_dir} (result code: {result['returncode']})")


# =============================================================================
# Persistent shell — long-lived bash session via host PTY for interactive flows
# =============================================================================


class PersistentShell:
    """A long-lived bash session inside a container, fronted by a host PTY.

    The motivating use case is multi-step interactive commands such as:

        run("ssh student@10.10.10.5")     -> ssh asks for a password
        run("hunter2")                     -> password is fed into the same ssh
        run("cat /root/flag")              -> command runs in the remote shell

    Single-shot ``docker exec`` cannot do this because each call gets a new
    process with no shared stdin and no controlling TTY. We emulate the
    upstream VulnBot ``RemoteShell`` flow by spawning ``docker exec -i -t``
    once, attaching it to a PTY we own, and detecting either our custom
    prompt (command finished) or a known interactive prompt (waiting for
    operator input).
    """

    PROMPT_TOKEN = "__VBSHELL_RDY_5fb1a7__"
    _PROMPT_BYTES = PROMPT_TOKEN.encode("utf-8")

    # Tails that mean "the program is waiting for the user to type something".
    # All matching is done lower-cased, so list lower-case substrings only.
    INTERACTIVE_PROMPTS = (
        b"password:",
        b"password for ",
        b"[sudo] password",
        b"(yes/no)",
        b"(yes/no/[fingerprint])",
        b"continue connecting (yes/no",
        b"--more--",
        b"[y/n]:",
        b"[y/n]?",
        b"[y/n] ",
        b"[y/N]:",
        b"[y/N] ",
        b"[Y/n]:",
        b"[Y/n] ",
    )

    # Generic fallback prompt — useful once we are inside ssh and our PS1
    # marker is no longer in effect. Matches things like "user@host:~$ " or
    # "root@target /tmp #". We deliberately avoid matching a bare "$" so we
    # don't accidentally truncate command output that ends in a dollar sign.
    GENERIC_PROMPT_RE = re.compile(
        rb"[A-Za-z0-9_.\-]+@[A-Za-z0-9_.\-]+(?::[^\r\n]{0,200})?[#$]\s*$"
    )

    def __init__(
        self,
        *,
        container_id: str,
        executable: str = "docker",
        cwd: str = "",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.container_id = container_id
        self.executable = executable
        self.cwd = cwd
        self.logger = logger or logging.getLogger("PersistentShell")
        self._proc: Optional[subprocess.Popen] = None
        self._master_fd: Optional[int] = None
        self._closed = False

    # -- lifecycle ----------------------------------------------------------

    def start(self, *, timeout: float = 5.0) -> None:
        master_fd, slave_fd = pty.openpty()
        # Disable echo and post-processing on our side. Note: docker exec
        # with -t allocates its own PTY inside the container, which sets
        # its own termios — the host-side tweak is best-effort only and we
        # also issue ``stty -echo -onlcr`` once bash has come up.
        try:
            import termios
            attrs = termios.tcgetattr(slave_fd)
            attrs[3] &= ~(termios.ECHO | termios.ECHOE | termios.ECHOK | termios.ECHONL)
            attrs[1] &= ~termios.OPOST
            termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
        except Exception:
            pass

        # Pre-seed PS1 / PROMPT_COMMAND through `docker exec --env` so the
        # very first prompt bash renders is already our distinctive marker.
        # That makes the initial drain reliable without racing against bash
        # printing a default user@host prompt.
        cmd = [
            self.executable, "exec", "-i", "-t",
            "--env", f"PS1={self.PROMPT_TOKEN}",
            "--env", "PS2=",
            "--env", "PROMPT_COMMAND=",
            "--env", "TERM=dumb",
            "--env", "HISTFILE=/dev/null",
            self.container_id, "bash", "--noprofile", "--norc", "-i",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
            )
        finally:
            os.close(slave_fd)
        self._master_fd = master_fd

        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Wait for bash's first prompt before issuing any commands — that's
        # how we know it's ready *and* that the container-side TTY is hooked
        # up. Until then the in-container termios still has echo on, so any
        # bytes we'd send would show up in the buffer.
        self._read_until(self._PROMPT_BYTES, timeout=timeout)

        # Now turn off echo / NL→CRLF translation in the *container* TTY so
        # subsequent commands aren't echoed back into stdout.
        init_parts = [
            "stty -echo -onlcr 2>/dev/null || true",
            "bind 'set enable-bracketed-paste off' 2>/dev/null || true",
        ]
        if self.cwd:
            init_parts.insert(0, f"cd {shlex.quote(self.cwd)} 2>/dev/null || true")
        self._send("; ".join(init_parts) + "\n")
        self._read_until(self._PROMPT_BYTES, timeout=timeout)

    def is_alive(self) -> bool:
        if self._closed or self._proc is None:
            return False
        return self._proc.poll() is None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._master_fd is not None and self._proc and self._proc.poll() is None:
                # Best-effort polite shutdown: Ctrl-C then exit.
                try:
                    self._send(b"\x03")
                    self._send(b"exit\n")
                except OSError:
                    pass
                time.sleep(0.05)
        except Exception:
            pass
        try:
            if self._master_fd is not None:
                os.close(self._master_fd)
        except OSError:
            pass
        self._master_fd = None
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        except Exception:
            pass
        self._proc = None

    def __enter__(self) -> "PersistentShell":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- low-level I/O ------------------------------------------------------

    def _send(self, data) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        if self._master_fd is None:
            return
        try:
            os.write(self._master_fd, data)
        except OSError as exc:
            self.logger.debug("PersistentShell write failed: %s", exc)

    def _read_until(self, needle: bytes, *, timeout: float) -> bytearray:
        buf = bytearray()
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            r, _, _ = select.select([self._master_fd], [], [], min(0.1, remaining))
            if not r:
                continue
            try:
                chunk = os.read(self._master_fd, 4096)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    continue
                break
            if not chunk:
                break
            buf.extend(chunk)
            if needle in buf:
                return buf
        return buf

    # -- public command interface ------------------------------------------

    def run(
        self,
        command: str,
        *,
        timeout: float = 60.0,
        idle_settle: float = 0.4,
    ) -> dict:
        """Send ``command`` followed by newline and read until the shell is
        either back at our prompt, sitting on a known interactive prompt, or
        the timeout elapses.

        Returns a dict with keys:

        * ``output`` (str) — everything the shell emitted, with the trailing
          PS1 marker stripped if it was seen
        * ``returncode`` (int) — exit code if the local-shell prompt was seen
          (queried via ``echo $?``); ``-1`` otherwise
        * ``timed_out`` (bool) — True if neither prompt nor interactive tail
          appeared before ``timeout``
        * ``waiting_for_input`` (bool) — True if an interactive prompt is
          believed to be waiting for the next ``run()`` call
        """
        if not self.is_alive():
            return {
                "output": "[shell closed]",
                "returncode": -1,
                "timed_out": False,
                "waiting_for_input": False,
            }

        cmd_bytes = command.rstrip("\n").encode("utf-8") + b"\n"
        self._send(cmd_bytes)

        deadline = time.time() + timeout
        last_data_time = time.time()
        buf = bytearray()
        prompt_seen = False
        generic_prompt_seen = False
        waiting_for_input = False

        while time.time() < deadline:
            r, _, _ = select.select([self._master_fd], [], [], 0.1)
            if r:
                try:
                    chunk = os.read(self._master_fd, 4096)
                except OSError as exc:
                    if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                        continue
                    break
                if chunk:
                    buf.extend(chunk)
                    last_data_time = time.time()

            tail = bytes(buf[-512:])

            # Our own prompt — command completed in the *local* shell.
            if self._PROMPT_BYTES in tail:
                prompt_seen = True
                break

            tail_lower = tail.lower()
            # Interactive prompt — needs operator input. Wait briefly for the
            # output to settle so we don't false-fire on a string that just
            # happened to scroll past.
            if any(p in tail_lower for p in self.INTERACTIVE_PROMPTS):
                if time.time() - last_data_time > idle_settle:
                    waiting_for_input = True
                    break

            # Generic remote-shell prompt (we are inside ssh) — also a
            # natural completion point. Same idle settle to be safe.
            if (
                self.GENERIC_PROMPT_RE.search(tail)
                and time.time() - last_data_time > idle_settle
            ):
                generic_prompt_seen = True
                break

        timed_out = not (prompt_seen or generic_prompt_seen or waiting_for_input)

        if timed_out:
            # Try to interrupt and recover so subsequent run()s start clean.
            try:
                self._send(b"\x03")
            except Exception:
                pass
            recovery = self._read_until(self._PROMPT_BYTES, timeout=2.0)
            buf.extend(recovery)
            if self._PROMPT_BYTES in recovery:
                prompt_seen = True

        text = buf.decode("utf-8", errors="replace")
        # Strip the trailing local prompt artefact for readability.
        if prompt_seen:
            idx = text.rfind(self.PROMPT_TOKEN)
            if idx >= 0:
                text = text[:idx]

        returncode = -1
        if prompt_seen and not waiting_for_input:
            returncode = self._fetch_returncode()

        return {
            "output": text,
            "returncode": returncode,
            "timed_out": timed_out,
            "waiting_for_input": waiting_for_input,
        }

    def _fetch_returncode(self) -> int:
        if self._master_fd is None:
            return -1
        marker = "__VBSHELL_RC__"
        try:
            self._send(f"echo {marker}$?\n")
        except Exception:
            return -1
        deadline = time.time() + 3.0
        buf = bytearray()
        while time.time() < deadline:
            r, _, _ = select.select([self._master_fd], [], [], 0.1)
            if not r:
                continue
            try:
                chunk = os.read(self._master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf.extend(chunk)
            if self._PROMPT_BYTES in buf:
                break
        text = buf.decode("utf-8", errors="replace")
        m = re.search(rf"{re.escape(marker)}(-?\d+)", text)
        if not m:
            return -1
        try:
            return int(m.group(1))
        except ValueError:
            return -1
