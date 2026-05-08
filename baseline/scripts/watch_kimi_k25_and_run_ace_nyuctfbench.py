#!/usr/bin/env python3
"""Wait for Kimi-K2.5 vLLM readiness, then launch an ACE NYUCTFBench run.

The watcher probes the OpenAI-compatible chat/completions endpoint every
minute. Once the endpoint can complete a tiny request, it launches ACE in
challenge evolution mode: each challenge owns one playbook and iterates up to
``--evolve-depth`` times.

All child stdout/stderr streams are written under baseline/logs/watchers/.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PYTHON = "/home/pgroup/pxd-team/miniconda3/envs/ctf_agent/bin/python"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch Kimi-K2.5 readiness and launch ACE NYUCTFBench runs.",
    )
    parser.add_argument("--model-key", default="Kimi-K2.5")
    parser.add_argument("--model-config", default=str(REPO_ROOT / "common" / "configs" / "model.yml"))
    parser.add_argument("--python", default=DEFAULT_PYTHON)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--probe-timeout-seconds", type=int, default=30)
    parser.add_argument("--benchmark", default="nyu_ctf")
    parser.add_argument("--step-limit", type=int, default=10)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--evolve-depth", type=int, default=4)
    parser.add_argument("--extend-depth", type=int, default=None)
    parser.add_argument("--resume-run-dir", default=None)
    parser.add_argument("--ace-prompt-profile", default=None)
    parser.add_argument("--run-id-prefix", default=None)
    parser.add_argument("--log-root", default=str(REPO_ROOT / "baseline" / "logs" / "watchers"))
    parser.add_argument("--challenge-server-script", default=str(REPO_ROOT / "bench_hub" /"server" / "challenge_server.py"))
    parser.add_argument("--challenge-server-bind-host", default="0.0.0.0")
    parser.add_argument("--challenge-server-public-host", default="127.0.0.1")
    parser.add_argument("--challenge-server-base-port", type=int, default=8000)
    parser.add_argument("--challenge-server-ready-timeout-seconds", type=int, default=60)
    parser.add_argument("--challenge-server-log-dir", default=str(REPO_ROOT / "logs" / "target_servers"))
    parser.add_argument("--no-start-challenge-server", action="store_true")
    parser.add_argument("--keep-challenge-server", action="store_true")
    return parser.parse_args()


def load_model_config(path: Path, model_key: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if model_key not in data:
        raise KeyError(f"Model key {model_key!r} not found in {path}")
    return dict(data[model_key] or {})


def probe_chat_completion(model_cfg: dict[str, Any], timeout_s: int) -> tuple[bool, str]:
    base_url = str(model_cfg["openai_api_base"]).rstrip("/")
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model_cfg["model"],
        "messages": [{"role": "user", "content": "Reply exactly: OK"}],
        "max_tokens": 4,
        "temperature": 0,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {model_cfg.get('openai_api_key', '')}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")[:300]
        return False, f"HTTP {exc.code}: {text}"
    except Exception as exc:
        return False, repr(exc)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False, f"non-json response: {text[:300]}"
    choices = parsed.get("choices")
    if not choices:
        return False, f"missing choices: {text[:300]}"
    return True, "ok"


def build_batch_command(
    *,
    python_bin: str,
    model_key: str,
    benchmark: str,
    step_limit: int,
    run_id: str,
    max_workers: int,
    evolve_depth: int,
    extend_depth: int | None,
    resume_run_dir: str | None,
    ace_prompt_profile: str | None,
    challenge_server_url: str,
) -> list[str]:
    command = [
        python_bin,
        "baseline/batch/run_batch_baseline.py",
        "--agent",
        "ace_agent",
        "--model",
        model_key,
        "--benchmark",
        benchmark,
        "--challenge-server-url",
        challenge_server_url,
        "--max-workers",
        str(max_workers),
        "--step-limit",
        str(step_limit),
        "--run-id",
        run_id,
        "--ace-evolve-mode",
        "challenge",
    ]
    if extend_depth is not None:
        command.extend(["--ace-extend-depth", str(extend_depth)])
    else:
        command.extend(["--ace-evolve-depth", str(evolve_depth)])
    if resume_run_dir:
        command.extend(["--resume-run-dir", resume_run_dir])
    if ace_prompt_profile:
        command.extend(["--ace-prompt-profile", ace_prompt_profile])
    return command


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


def find_available_port(host: str, start_port: int) -> int:
    for port in range(start_port, start_port + 500):
        if port_is_free(host, port):
            return port
    raise RuntimeError(f"No available port found from {start_port}")


def challenge_server_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/openapi.json", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return payload.get("info", {}).get("title") == "CTF Manager Server"
    except Exception:
        return False


def wait_for_challenge_server(url: str, proc: subprocess.Popen, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if challenge_server_ready(url):
            return
        if proc.poll() is not None:
            raise RuntimeError(f"challenge_server exited early with code {proc.returncode}: {url}")
        time.sleep(1)
    raise TimeoutError(f"challenge_server did not become ready within {timeout_s}s: {url}")


def start_challenge_server(
    *,
    python_bin: str,
    script_path: str,
    bind_host: str,
    public_host: str,
    start_port: int,
    namespace: str,
    log_dir: Path,
    ready_timeout_s: int,
) -> tuple[subprocess.Popen, str, int, Path]:
    port = find_available_port(public_host, start_port)
    url = f"http://{public_host}:{port}"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{namespace}_{port}.log"
    env = os.environ.copy()
    env["CTF_NAMESPACE"] = namespace
    env.setdefault("CTF_STARTUP_TIMEOUT_S", "180")
    env.setdefault("CTF_PORT_OPEN_STABILITY_CHECKS", "2")
    fh = log_path.open("ab")
    proc = subprocess.Popen(
        [python_bin, script_path, bind_host, str(port)],
        cwd=str(REPO_ROOT),
        stdout=fh,
        stderr=subprocess.STDOUT,
        env=env,
    )
    wait_for_challenge_server(url, proc, ready_timeout_s)
    return proc, url, port, log_path


def stop_process(proc: subprocess.Popen, name: str, log) -> None:
    if proc.poll() is not None:
        return
    log(f"stopping {name} pid={proc.pid}")
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        log(f"{name} did not stop after SIGTERM; sending SIGKILL")
        proc.kill()
        proc.wait(timeout=10)


def launch(name: str, command: list[str], log_dir: Path) -> subprocess.Popen:
    log_path = log_dir / f"{name}.stdout.log"
    cmd_path = log_dir / f"{name}.cmd.txt"
    cmd_path.write_text(" ".join(command) + "\n", encoding="utf-8")
    fh = log_path.open("ab")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        stdout=fh,
        stderr=subprocess.STDOUT,
        env=env,
    )


def main() -> int:
    args = parse_args()
    model_cfg = load_model_config(Path(args.model_config), args.model_key)
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id_prefix = args.run_id_prefix or f"kimi_k25_nyu_ace_{run_stamp}"
    log_dir = Path(args.log_root) / run_id_prefix
    log_dir.mkdir(parents=True, exist_ok=True)
    watcher_log = log_dir / "watcher.log"

    def log(message: str) -> None:
        line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with watcher_log.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    log(f"watching model_key={args.model_key} base={model_cfg.get('openai_api_base')}")
    while True:
        ok, detail = probe_chat_completion(model_cfg, args.probe_timeout_seconds)
        if ok:
            log("probe succeeded; launching ACE NYUCTFBench runs")
            break
        log(f"probe failed: {detail}; retrying in {args.interval_seconds}s")
        time.sleep(args.interval_seconds)

    challenge_servers: dict[str, subprocess.Popen] = {}
    try:
        if args.no_start_challenge_server:
            ctf_url = "http://127.0.0.1:8000"
            log("not starting challenge_server; run will use http://127.0.0.1:8000")
        else:
            ctf_log_dir = Path(args.challenge_server_log_dir)
            ctf_proc, ctf_url, ctf_port, ctf_log = start_challenge_server(
                python_bin=args.python,
                script_path=args.challenge_server_script,
                bind_host=args.challenge_server_bind_host,
                public_host=args.challenge_server_public_host,
                start_port=args.challenge_server_base_port,
                namespace=f"{run_id_prefix}_challenge_evolve",
                log_dir=ctf_log_dir,
                ready_timeout_s=args.challenge_server_ready_timeout_seconds,
            )
            challenge_servers = {"challenge_server": ctf_proc}
            log(f"challenge_server url={ctf_url} pid={ctf_proc.pid} log={ctf_log}")

        cmd = build_batch_command(
            python_bin=args.python,
            model_key=args.model_key,
            benchmark=args.benchmark,
            step_limit=args.step_limit,
            run_id=f"{run_id_prefix}_challenge_evolve",
            max_workers=args.workers,
            evolve_depth=args.evolve_depth,
            extend_depth=args.extend_depth,
            resume_run_dir=args.resume_run_dir,
            ace_prompt_profile=args.ace_prompt_profile,
            challenge_server_url=ctf_url,
        )

        processes = {
            "challenge_evolve": launch("challenge_evolve", cmd, log_dir),
        }
        log(f"launched challenge_evolve pid={processes['challenge_evolve'].pid}")

        exit_code = 0
        for name, proc in processes.items():
            code = proc.wait()
            log(f"{name} finished with exit_code={code}")
            if code != 0:
                exit_code = code
        log("watcher complete")
        return exit_code
    finally:
        if challenge_servers and not args.keep_challenge_server:
            for name, proc in challenge_servers.items():
                stop_process(proc, name, log)


if __name__ == "__main__":
    raise SystemExit(main())
