#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SERVER_DIR = ROOT / "showdown_server"


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="showdown-docker-shim-test")
    args = parser.parse_args()

    build = run(["docker", "build", "-t", args.image, str(SERVER_DIR)])
    if build.returncode != 0:
        sys.stdout.write(build.stdout)
        sys.stderr.write(build.stderr)
        return build.returncode

    probe = run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            args.image,
            "-lc",
            "command -v docker",
        ]
    )
    sys.stdout.write(probe.stdout)
    sys.stderr.write(probe.stderr)
    return probe.returncode


if __name__ == "__main__":
    raise SystemExit(main())
