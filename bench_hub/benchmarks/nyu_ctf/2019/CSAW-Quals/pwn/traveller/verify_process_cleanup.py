#!/usr/bin/env python3
import argparse
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_IMAGE = "llmctf/2019q-pwn-traveller:latest"
PROMPT = b"> "


def run(cmd: list[str], check: bool = True, capture_output: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("DOCKER_BUILDKIT", "0")
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture_output,
        text=True,
        cwd=str(ROOT),
        env=env,
    )


def build_local_image(tag: str) -> str:
    run(["docker", "build", "-t", tag, "."])
    return tag


def container_port(container_name: str) -> int:
    result = run(["docker", "port", container_name, "8000/tcp"])
    _, port = result.stdout.strip().rsplit(":", 1)
    return int(port)


def count_traveller_processes(container_name: str) -> int:
    result = run(
        [
            "docker",
            "exec",
            container_name,
            "sh",
            "-lc",
            "ps -C traveller -o pid= | wc -l",
        ]
    )
    return int(result.stdout.strip())


def wait_for_zero_traveller_processes(container_name: str, timeout: float = 5.0) -> int:
    deadline = time.time() + timeout
    latest = -1
    while time.time() < deadline:
        latest = count_traveller_processes(container_name)
        if latest == 0:
            return 0
        time.sleep(0.2)
    return latest


def read_until_prompt(sock: socket.socket, timeout: float = 5.0) -> bytes:
    sock.settimeout(timeout)
    chunks: list[bytes] = []
    while PROMPT not in b"".join(chunks):
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
    data = b"".join(chunks)
    if PROMPT not in data:
        raise RuntimeError(f"did not receive prompt, got: {data!r}")
    return data


def exercise_connections(host: str, port: int, repeats: int) -> None:
    for _ in range(repeats):
        with socket.create_connection((host, port), timeout=5.0) as sock:
            read_until_prompt(sock)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify traveller does not leak per-connection processes.")
    parser.add_argument("--image", default=None, help="Image to run. If omitted, builds the local challenge image.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    tag = args.image or f"traveller-cleanup-check:{uuid.uuid4().hex[:12]}"
    built_locally = args.image is None
    container_name = f"traveller-cleanup-{uuid.uuid4().hex[:12]}"

    try:
        if built_locally:
            print(f"[+] building local image {tag}", flush=True)
            build_local_image(tag)

        print(f"[+] starting container {container_name} from {tag}", flush=True)
        run(["docker", "run", "--rm", "-d", "-P", "--name", container_name, tag])
        port = container_port(container_name)
        print(f"[+] mapped to {args.host}:{port}", flush=True)

        exercise_connections(args.host, port, args.repeats)
        leaked = wait_for_zero_traveller_processes(container_name)
        if leaked != 0:
            print(f"[-] leak detected: {leaked} traveller process(es) remained after disconnects", file=sys.stderr)
            return 1

        print("[+] no leaked traveller processes detected", flush=True)
        return 0
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if built_locally:
            subprocess.run(
                ["docker", "rmi", "-f", tag],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )


if __name__ == "__main__":
    sys.exit(main())
