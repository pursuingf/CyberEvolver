#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Interface
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 8126
DEFAULT_COMMAND_TIMEOUT_S = 240
DEFAULT_STARTUP_TIMEOUT_S = 180


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return int(sock.getsockname()[1])


def http_json(method: str, url: str, *, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url=url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for_tcp(host: str, port: int, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"server did not open {host}:{port} within {timeout_s}s: {last_error}")


@dataclass(frozen=True)
class CommandProfile:
    label: str
    family: str
    template: str
    timeout_s: int = DEFAULT_COMMAND_TIMEOUT_S


@dataclass(frozen=True)
class ChallengeProfile:
    chal_id: str
    commands: tuple[CommandProfile, ...]


PROFILES: tuple[ChallengeProfile, ...] = (
    ChallengeProfile(
        chal_id="apb-in-vitro-access_control-vm0",
        commands=(
            CommandProfile("subnet_ping_24", "subnet_discovery", "nmap -sn {subnet}"),
            CommandProfile("service_version", "service_probe", "nmap -sV {target_ip}"),
            CommandProfile("service_port22", "single_port_probe", "nmap -sV -p 22 {target_ip}"),
        ),
    ),
    ChallengeProfile(
        chal_id="apb-in-vitro-web_security-vm0",
        commands=(
            CommandProfile("subnet_ping_24", "subnet_discovery", "nmap -sn {subnet}"),
            CommandProfile("service_version", "service_probe", "nmap -sV {target_ip}"),
            CommandProfile("service_port80", "single_port_probe", "nmap -sV -p 80 {target_ip}"),
        ),
    ),
    ChallengeProfile(
        chal_id="apb-in-vitro-network_security-vm0",
        commands=(
            CommandProfile(
                "network_scan_16_fast",
                "large_network_discovery",
                "nmap -sn -n --min-parallelism 100 --max-retries 1 {major16}",
                timeout_s=300,
            ),
            CommandProfile("subnet_ping_24", "subnet_discovery", "nmap -sn {subnet}"),
        ),
    ),
    ChallengeProfile(
        chal_id="apb-real-world-cve-vm7",
        commands=(
            CommandProfile("subnet_ping_24", "subnet_discovery", "nmap -sn {subnet}"),
            CommandProfile("service_version", "service_probe", "nmap -sV {target_ip}"),
            CommandProfile("fullport_version", "fullport_probe", "nmap -p- -sV {target_ip}", timeout_s=300),
        ),
    ),
    ChallengeProfile(
        chal_id="apb-in-vitro-network_security-vm1",
        commands=(
            CommandProfile(
                "fullport_version_fast",
                "fullport_probe",
                "nmap -p- -sV --min-parallelism 100 --max-retries 1 {target_ip}",
                timeout_s=300,
            ),
        ),
    ),
    ChallengeProfile(
        chal_id="apb-real-world-cve-vm5",
        commands=(
            CommandProfile("service_version", "service_probe", "nmap -sV {target_ip}", timeout_s=300),
            CommandProfile("service_port3000", "single_port_probe", "nmap -sV -p 3000 {target_ip}", timeout_s=300),
        ),
    ),
)


def sanitize_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    ):
        env.pop(key, None)
    return env


class ManagedServer:
    def __init__(self, host: str, port: int, *, namespace: str, startup_timeout_s: int) -> None:
        self.host = host
        self.port = port
        self.namespace = namespace
        self.startup_timeout_s = startup_timeout_s
        self.proc: subprocess.Popen[str] | None = None

    def __enter__(self) -> "ManagedServer":
        env = os.environ.copy()
        env["CTF_NAMESPACE"] = self.namespace
        env["CTF_STARTUP_TIMEOUT_S"] = str(self.startup_timeout_s)
        env["CTF_PORT_OPEN_STABILITY_CHECKS"] = "1"
        cmd = [
            os.environ.get("PYTHON_BIN", sys.executable),
            str(ROOT / "bench_hub/server" / "challenge_server.py"),
            self.host,
            str(self.port),
        ]
        self.proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        wait_for_tcp(self.host, self.port, timeout_s=20.0)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)

        leftover = f"ctfnet_{self.namespace}"
        subprocess.run(
            ["docker", "network", "rm", leftover],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def first_target_service(launch: dict[str, Any]) -> dict[str, Any]:
    services = launch.get("services") or []
    if not services:
        raise RuntimeError(f"launch response for {launch.get('chal_id')} has no services")
    for service in services:
        if service.get("inner_ip"):
            return service
    return services[0]


def derive_scan_vars(launch: dict[str, Any]) -> dict[str, str]:
    service = first_target_service(launch)
    target_ip = service.get("inner_ip") or service.get("ip")
    if not target_ip:
        raise RuntimeError(f"cannot derive target IP from launch response for {launch.get('chal_id')}")

    subnet = launch.get("network_subnet")
    if subnet:
        subnet = str(IPv4Interface(f"{target_ip}/{subnet.split('/')[-1]}").network)
    else:
        octets = target_ip.split(".")
        subnet = f"{octets[0]}.{octets[1]}.{octets[2]}.0/24"
    first, second, *_ = target_ip.split(".")
    major16 = f"{first}.{second}.0.0/16"
    return {
        "target_ip": target_ip,
        "subnet": subnet,
        "major16": major16,
    }


def run_scan_in_network(
    *,
    network_name: str,
    command: str,
    timeout_s: int,
    ) -> dict[str, Any]:
    env = sanitize_env()
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        network_name,
        "ctfenv:latest",
        "bash",
        "-lc",
        command,
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        elapsed = time.monotonic() - started
        output = (result.stdout or "") + (result.stderr or "")
        return {
            "command": command,
            "elapsed_s": round(elapsed, 3),
            "returncode": result.returncode,
            "timed_out": False,
            "tail": "\n".join(output.strip().splitlines()[-12:]),
        }
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        output = (stdout + stderr).strip()
        return {
            "command": command,
            "elapsed_s": round(elapsed, 3),
            "returncode": None,
            "timed_out": True,
            "tail": "\n".join(output.splitlines()[-12:]),
        }


def recommend_timeouts(results: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = {}
    timed_out: dict[str, int] = {}
    for item in results:
        grouped.setdefault(item["family"], []).append(float(item["elapsed_s"]))
        if item["timed_out"]:
            timed_out[item["family"]] = timed_out.get(item["family"], 0) + 1

    recommendations: dict[str, Any] = {}
    for family, values in sorted(grouped.items()):
        values = sorted(values)
        p95 = values[-1] if len(values) < 20 else statistics.quantiles(values, n=100)[94]
        max_value = values[-1]
        recommended = int(max(max_value * 1.25, p95 * 1.2, 15))
        recommendations[family] = {
            "samples": len(values),
            "max_elapsed_s": round(max_value, 3),
            "p95_elapsed_s": round(p95, 3),
            "timed_out": timed_out.get(family, 0),
            "recommended_timeout_s": recommended,
        }
    return recommendations


def stop_launch(server_url: str, chal_id: str, run_id: str | None) -> None:
    url = f"{server_url}/launch/{urllib.parse.quote(chal_id)}"
    if run_id:
        url += "?" + urllib.parse.urlencode({"run_id": run_id})
    try:
        http_json("DELETE", url, timeout=60.0)
    except urllib.error.HTTPError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark representative AutoPenBench nmap scans.")
    parser.add_argument("--host", default=DEFAULT_SERVER_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_SERVER_PORT)
    parser.add_argument("--namespace", default=f"nmapbench_{now_stamp()}")
    parser.add_argument("--command-timeout", type=int, default=DEFAULT_COMMAND_TIMEOUT_S)
    parser.add_argument("--startup-timeout", type=int, default=DEFAULT_STARTUP_TIMEOUT_S)
    parser.add_argument("--output", default=f"reports/nmap_benchmarks_{now_stamp()}.json")
    parser.add_argument("--challenges", nargs="*", help="Only run the selected challenge IDs.")
    args = parser.parse_args()

    output_path = (ROOT / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    server_url = f"http://{args.host}:{args.port}"
    all_results: list[dict[str, Any]] = []

    selected_profiles = PROFILES
    if args.challenges:
        wanted = set(args.challenges)
        selected_profiles = tuple(profile for profile in PROFILES if profile.chal_id in wanted)
        missing = sorted(wanted - {profile.chal_id for profile in selected_profiles})
        if missing:
            raise SystemExit(f"unknown challenge profile(s): {', '.join(missing)}")

    with ManagedServer(
        args.host,
        args.port,
        namespace=args.namespace,
        startup_timeout_s=args.startup_timeout,
    ):
        for challenge in selected_profiles:
            launch = None
            run_id = None
            try:
                params = urllib.parse.urlencode(
                    {
                        "parallel_mode": "network",
                        "target_scope": "per_agent",
                        "force_recreate": "true",
                    }
                )
                launch = http_json(
                    "GET",
                    f"{server_url}/launch/{urllib.parse.quote(challenge.chal_id)}?{params}",
                    timeout=float(args.startup_timeout) + 60.0,
                )
                run_id = launch.get("run_id")
                network_name = launch["network_name"]
                scan_vars = derive_scan_vars(launch)

                print(f"[launch] {challenge.chal_id} network={network_name} target={scan_vars['target_ip']}", flush=True)
                for profile in challenge.commands:
                    timeout_s = profile.timeout_s or args.command_timeout
                    command = profile.template.format(**scan_vars)
                    print(f"[scan] {challenge.chal_id} {profile.label}: {command}", flush=True)
                    result = run_scan_in_network(
                        network_name=network_name,
                        command=command,
                        timeout_s=timeout_s,
                    )
                    result.update(
                        {
                            "chal_id": challenge.chal_id,
                            "label": profile.label,
                            "family": profile.family,
                            "target_ip": scan_vars["target_ip"],
                            "subnet": scan_vars["subnet"],
                            "major16": scan_vars["major16"],
                            "network_name": network_name,
                            "run_id": run_id,
                        }
                    )
                    all_results.append(result)
                    status = "TIMEOUT" if result["timed_out"] else f"rc={result['returncode']}"
                    print(f"[done] {challenge.chal_id} {profile.label}: {result['elapsed_s']}s {status}", flush=True)
            finally:
                stop_launch(server_url, challenge.chal_id, run_id)

    payload = {
        "generated_at": datetime.now().isoformat(),
        "namespace": args.namespace,
        "server_url": server_url,
        "results": all_results,
        "recommendations": recommend_timeouts(all_results),
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print(flush=True)
    print(f"saved report: {output_path}", flush=True)
    print(json.dumps(payload["recommendations"], indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
