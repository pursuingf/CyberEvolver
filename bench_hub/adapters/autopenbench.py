from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from bench_hub.adapters.base import BenchmarkSource, LaunchSpec, NormalizedChallenge, derive_flag_format


TARGET_SUBNET_PLACEHOLDER = "{target_subnet}"
LEGACY_DISCOVERY_SUBNETS = (
    "192.168.0.0/16",
    "192.168.1.0/24",
    "192.168.2.0/24",
    "192.168.3.0/24",
    "192.168.4.0/24",
    "192.168.5.0/24",
)
LEGACY_IGNORE_HOST_HINT_RE = re.compile(r"\s*Always ignore the 192\.168\.122\.1 host!\s*")
AUTOPENBENCH_PROJECT_LOCAL_SUBNET_POLICY = {
    "pool": "172.31.0.0/16",
    "prefix": 24,
}


def template_autopenbench_task_networks(task: str) -> str:
    templated = str(task or "")
    for subnet in LEGACY_DISCOVERY_SUBNETS:
        templated = templated.replace(subnet, TARGET_SUBNET_PLACEHOLDER)
    templated = LEGACY_IGNORE_HOST_HINT_RE.sub(" ", templated)
    templated = re.sub(r"[ \t]{2,}", " ", templated)
    templated = re.sub(r" *\n *", "\n", templated)
    return templated.strip()


class AutoPenBenchAdapter:
    adapter_kind = "autopenbench"

    def discover(self, source: BenchmarkSource) -> dict[str, NormalizedChallenge]:
        root = Path(source.root)
        games_path = root / "data" / "games.json"
        with open(games_path, "r", encoding="utf-8") as handle:
            games = json.load(handle)

        challenges: dict[str, NormalizedChallenge] = {}
        for level, categories in games.items():
            for category, entries in categories.items():
                for index, entry in enumerate(entries):
                    target = entry["target"]
                    vm = target.split("_")[-1]
                    challenge_id = f"apb-{level}-{category}-{vm}"
                    benchmark_root = root / "benchmark"
                    machines_root = benchmark_root / "machines"
                    machine_root = machines_root / level / category / vm
                    source_fields = dict(entry)
                    source_fields.update(
                        {
                            "level": level,
                            "category": category,
                            "vm": vm,
                            "game_index": index,
                            "benchmark_root": str(benchmark_root.resolve()),
                            "machine_root": str(machine_root.resolve()),
                            "base_compose_path": str((machines_root / "docker-compose.yml").resolve()),
                            "category_compose_path": str((machines_root / level / category / "docker-compose.yml").resolve()),
                            "command_milestones_path": str(
                                (benchmark_root / "milestones" / "command_milestones" / level / category / f"{vm}.txt").resolve()
                            ),
                            "stage_milestones_path": str(
                                (benchmark_root / "milestones" / "stage_milestones" / level / category / f"{vm}.txt").resolve()
                            ),
                            "solution_path": str(
                                (benchmark_root / "solutions" / level / category / f"{vm}.txt").resolve()
                            ),
                        }
                    )
                    source_fields.update(self._load_existing_challenge_overrides(machine_root))
                    raw_task = str(source_fields.get("task") or entry["task"])
                    templated_task = template_autopenbench_task_networks(raw_task)
                    source_fields["task_original"] = source_fields.get("task_original") or entry["task"]
                    source_fields["task"] = templated_task
                    source_fields["description"] = template_autopenbench_task_networks(
                        str(source_fields.get("description") or raw_task)
                    )

                    challenges[challenge_id] = {
                        "id": challenge_id,
                        "benchmark_name": "autopenbench",
                        "benchmark": "autopenbench",
                        "benchmark_family": "autopenbench",
                        "adapter_kind": self.adapter_kind,
                        "prompt_template_profile": "autopenbench",
                        "task_profile": "pentest_remote",
                        "name": target,
                        "category": category,
                        "description": source_fields["description"],
                        "task": templated_task,
                        "files": [],
                        "flag": entry["flag"],
                        "flag_format": derive_flag_format(entry["flag"]),
                        "full_path": str(machine_root.resolve()),
                        "source_fields": source_fields,
                    }

        return challenges

    def build_launch_spec(self, challenge: NormalizedChallenge) -> LaunchSpec:
        source_fields = challenge["source_fields"]
        target = source_fields["target"]
        target_port = self._resolve_target_port(source_fields)
        working_directory = str((Path(source_fields["benchmark_root"]) / "machines").resolve())
        runtime_patches = {}
        compose_config = self._load_compose_stack(source_fields)
        if target_port is not None:
            runtime_patches["target_ports"] = {
                target: target_port,
            }
        target_port_protocols = dict(source_fields.get("target_port_protocols", {}) or {})
        if target_port_protocols:
            runtime_patches["target_port_protocols"] = target_port_protocols
        explicit_network_mode = str(source_fields.get("network_mode", "") or "").strip()
        if explicit_network_mode:
            runtime_patches["network_mode"] = explicit_network_mode
        explicit_agent_network = str(source_fields.get("agent_network", "") or "").strip()
        if explicit_agent_network:
            runtime_patches["agent_network"] = explicit_agent_network
        available_networks = set((compose_config.get("networks", {}) or {}).keys())
        if "net-main_network" in available_networks and "network_mode" not in runtime_patches:
            runtime_patches["network_mode"] = "compose_project_local"
        if "net-main_network" in available_networks and "agent_network" not in runtime_patches:
            runtime_patches["agent_network"] = "net-main_network"
        if "net-main_network" in available_networks:
            runtime_patches.setdefault(
                "project_local_subnet_pool",
                AUTOPENBENCH_PROJECT_LOCAL_SUBNET_POLICY["pool"],
            )
            runtime_patches.setdefault(
                "project_local_subnet_prefix",
                AUTOPENBENCH_PROJECT_LOCAL_SUBNET_POLICY["prefix"],
            )

        return LaunchSpec(
            mode="compose",
            working_directory=working_directory,
            compose_files=[
                source_fields["base_compose_path"],
                source_fields["category_compose_path"],
            ],
            target_services=[target],
            dependency_services=list(source_fields.get("compose_dependency_services", []) or []),
            runtime_patches=runtime_patches,
            exposure_mode=source_fields.get("exposure_mode", "host_ports"),
        )

    def _resolve_target_port(self, source_fields: dict[str, str]) -> int | None:
        if source_fields.get("internal_port") is not None:
            return int(source_fields["internal_port"])
        target = source_fields["target"]
        category = source_fields["category"]
        level = source_fields["level"]
        compose_config = self._load_compose_stack(source_fields)
        target_service = ((compose_config.get("services", {}) or {}).get(target, {}) or {})

        port = self._port_from_service_config(target_service)
        if port is not None:
            return port

        port = self._port_from_dockerfile(Path(source_fields["machine_root"]) / "Dockerfile")
        if port is not None:
            return port

        port = self._port_from_command(target_service.get("command"))
        if port is not None:
            return port

        return self._infer_target_port(level, category, target)

    def _load_compose_stack(self, source_fields: dict[str, str]) -> dict:
        merged: dict = {}
        for compose_path in (
            source_fields["base_compose_path"],
            source_fields["category_compose_path"],
        ):
            with open(compose_path, "r", encoding="utf-8") as handle:
                current = yaml.safe_load(handle) or {}
            merged = self._merge_compose_dicts(merged, current)
        return merged

    def _merge_compose_dicts(self, base: dict, override: dict) -> dict:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_compose_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _port_from_service_config(self, service_config: dict) -> int | None:
        ports = service_config.get("ports", []) or []
        for port_def in ports:
            port = self._parse_port_def(port_def)
            if port is not None:
                return port

        exposed = service_config.get("expose", []) or []
        for port_def in exposed:
            port = self._parse_port_def(port_def)
            if port is not None:
                return port

        return None

    def _port_from_dockerfile(self, dockerfile_path: Path) -> int | None:
        if not dockerfile_path.exists():
            return None

        content = dockerfile_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.upper().startswith("EXPOSE "):
                continue
            for token in stripped.split()[1:]:
                port = self._parse_port_def(token)
                if port is not None:
                    return port
        return None

    def _port_from_command(self, command: str | list[str] | None) -> int | None:
        if not command:
            return None
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)

        if "service ssh start" in command:
            return 22
        if "service snmpd start" in command:
            return 161
        if "php -S 0.0.0.0:80" in command or "httpd -D FOREGROUND" in command:
            return 80
        return None

    def _parse_port_def(self, port_def: object) -> int | None:
        if isinstance(port_def, int):
            return port_def
        if not isinstance(port_def, str):
            return None

        text = port_def.strip()
        if not text:
            return None

        if ":" in text:
            text = text.split(":")[-1]
        text = text.split("/")[0]
        if text.isdigit():
            return int(text)
        return None

    def _infer_target_port(self, level: str, category: str, target: str) -> int | None:
        del target
        if category == "access_control":
            return 22
        if category == "web_security":
            return 80
        if category == "cryptography":
            return 8080
        if level == "real-world" and category == "cve":
            return None
        if category == "network_security":
            return None
        return None

    def _load_existing_challenge_overrides(self, machine_root: Path) -> dict:
        challenge_path = machine_root / "challenge.json"
        if not challenge_path.exists():
            return {}
        with open(challenge_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return {
            key: payload[key]
            for key in (
                "agent_network",
                "compose_dependency_services",
                "exposure_mode",
                "internal_port",
                "network_mode",
                "target_port_protocols",
                "target_ports",
            )
            if key in payload
        }
