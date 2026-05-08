from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
from pathlib import Path

import yaml

from bench_hub.adapters.autopenbench import AutoPenBenchAdapter
from bench_hub.adapters.base import BenchmarkSource, NormalizedChallenge


def generate_autopenbench_layout(*, source_root: str | Path, benchmark_root: str | Path) -> dict[str, dict]:
    source_root = Path(source_root).resolve()
    benchmark_root = Path(benchmark_root).resolve()
    benchmark_root.mkdir(parents=True, exist_ok=True)
    dest_root = benchmark_root / "autopenbench"
    _copy_autopenbench_source(source_root=source_root, dest_root=dest_root)
    _sanitize_legacy_compose_networks(dest_root=dest_root)

    adapter = AutoPenBenchAdapter()
    challenges = adapter.discover(
        BenchmarkSource(
            adapter_kind=adapter.adapter_kind,
            root=source_root,
        )
    )

    index_data: dict[str, dict] = {}
    for challenge_id, challenge in sorted(challenges.items()):
        challenge_payload, index_entry = _build_layout_entry(
            adapter=adapter,
            benchmark_root=benchmark_root,
            challenge=challenge,
        )
        challenge_root = benchmark_root / index_entry["path"]
        challenge_root.mkdir(parents=True, exist_ok=True)
        (challenge_root / "challenge.json").write_text(
            json.dumps(challenge_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        index_data[challenge_id] = index_entry

    (benchmark_root / "autopenbench.json").write_text(
        json.dumps(index_data, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return index_data


def _copy_autopenbench_source(*, source_root: Path, dest_root: Path) -> None:
    shutil.copytree(source_root / "data", dest_root / "data", dirs_exist_ok=True)
    machines_root = source_root / "benchmark" / "machines"

    def ignore_runtime_artifacts(directory: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name.startswith("docker-compose.runtime") and name.endswith(".yml")}
        if Path(directory).resolve() == machines_root.resolve():
            allowed = {"docker-compose.yml", "in-vitro", "real-world"}
            ignored.update(name for name in names if name not in allowed)
        return ignored

    shutil.copytree(
        source_root / "benchmark",
        dest_root / "benchmark",
        dirs_exist_ok=True,
        ignore=ignore_runtime_artifacts,
    )


def _sanitize_legacy_compose_networks(*, dest_root: Path) -> None:
    machines_root = dest_root / "benchmark" / "machines"
    for compose_path in machines_root.glob("**/docker-compose.yml"):
        config = _load_yaml_file(compose_path)
        _strip_static_network_settings(config)
        compose_path.write_text(
            yaml.safe_dump(config, sort_keys=False, indent=2),
            encoding="utf-8",
        )


def _build_layout_entry(
    *,
    adapter: AutoPenBenchAdapter,
    benchmark_root: Path,
    challenge: NormalizedChallenge,
) -> tuple[dict, dict]:
    source_fields = challenge["source_fields"]
    launch_spec = adapter.build_launch_spec(challenge)
    target = source_fields["target"]
    internal_port = ((launch_spec.runtime_patches.get("target_ports") or {}).get(target))

    relative_path = Path("autopenbench") / "benchmark" / "machines" / source_fields["level"] / source_fields["category"] / source_fields["vm"]
    challenge_root = benchmark_root / relative_path
    dependency_services = _collect_compose_dependencies(
        compose_files=[Path(path) for path in launch_spec.compose_files],
        target_services=list(launch_spec.target_services),
    )
    _write_challenge_local_compose(
        compose_files=[Path(path) for path in launch_spec.compose_files],
        project_root=benchmark_root / "autopenbench" / "benchmark" / "machines",
        challenge_root=challenge_root,
        target_services=list(launch_spec.target_services),
        dependency_services=dependency_services,
    )

    index_entry = {
        "benchmark": "autopenbench",
        "benchmark_family": "autopenbench",
        "category": source_fields["category"],
        "challenge": target,
        "level": source_fields["level"],
        "path": relative_path.as_posix(),
        "prompt_template_profile": "autopenbench",
        "target": target,
        "vm": source_fields["vm"],
    }

    challenge_payload = {
        "adapter_kind": "challenge_json",
        "benchmark_family": "autopenbench",
        "category": source_fields["category"],
        "command_milestones_path": _relative_command_milestones_path(source_fields),
        "compose_dependency_services": dependency_services,
        "compose_files": [
            "compose.yml",
        ],
        "compose_target_services": list(launch_spec.target_services),
        "description": source_fields.get("description") or challenge["description"],
        "exposure_mode": launch_spec.exposure_mode,
        "files": [],
        "flag": source_fields["flag"],
        "game_index": source_fields["game_index"],
        "internal_port": internal_port,
        "level": source_fields["level"],
        "name": target,
        "prompt_template_profile": "autopenbench",
        "solution_path": _relative_solution_path(source_fields),
        "stage_milestones_path": _relative_stage_milestones_path(source_fields),
        "target": target,
        "task": source_fields["task"],
        "task_profile": "pentest_remote",
        "vm": source_fields["vm"],
        "vulnerability": source_fields["vulnerability"],
    }
    if launch_spec.runtime_patches.get("target_port_protocols"):
        challenge_payload["target_port_protocols"] = launch_spec.runtime_patches["target_port_protocols"]
    if source_fields.get("target_ports"):
        challenge_payload["target_ports"] = source_fields["target_ports"]
    return challenge_payload, index_entry


def _write_challenge_local_compose(
    *,
    compose_files: list[Path],
    project_root: Path,
    challenge_root: Path,
    target_services: list[str],
    dependency_services: list[str],
) -> None:
    config = _load_compose_stack(compose_files)
    services_config = config.get("services", {}) or {}
    launch_services = set(target_services) | set(dependency_services)

    filtered_services: dict = {}
    referenced_networks: set[str] = set()
    for service_name in target_services + dependency_services:
        service_config = services_config.get(service_name)
        if not service_config:
            continue
        rewritten = _rewrite_service_for_challenge_root(
            copy.deepcopy(service_config),
            project_root=project_root,
            challenge_root=challenge_root,
        )
        _filter_depends_on(rewritten, launch_services)
        _record_service_networks(rewritten, referenced_networks)
        filtered_services[service_name] = rewritten

    rendered: dict = {}
    if config.get("version") is not None:
        rendered["version"] = config["version"]
    rendered["services"] = filtered_services

    networks_config = config.get("networks", {}) or {}
    if networks_config and referenced_networks:
        rendered["networks"] = {
            name: {}
            for name in sorted(referenced_networks)
            if name in networks_config
        }

    challenge_root.mkdir(parents=True, exist_ok=True)
    (challenge_root / "compose.yml").write_text(
        yaml.safe_dump(rendered, sort_keys=False, indent=2),
        encoding="utf-8",
    )


def _strip_static_network_settings(config: dict) -> None:
    services_config = config.get("services", {}) or {}
    for service_config in services_config.values():
        networks = service_config.get("networks")
        if isinstance(networks, dict):
            service_config["networks"] = list(networks.keys())

    networks_config = config.get("networks", {}) or {}
    if isinstance(networks_config, dict):
        config["networks"] = {network_name: {} for network_name in networks_config}


def _load_compose_stack(compose_files: list[Path]) -> dict:
    merged: dict = {}
    for compose_path in compose_files:
        current = _load_yaml_file(compose_path)
        merged = _merge_compose_dicts(merged, current)
    return merged


def _load_yaml_file(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _merge_compose_dicts(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_compose_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _collect_compose_dependencies(*, compose_files: list[Path], target_services: list[str]) -> list[str]:
    config = _load_compose_stack(compose_files)
    services_config = config.get("services", {}) or {}
    dependencies: list[str] = []
    seen = set(target_services)
    queue = list(target_services)

    while queue:
        service_name = queue.pop(0)
        service_config = services_config.get(service_name, {}) or {}
        for dependency in _depends_on_names(service_config.get("depends_on", {})):
            if dependency in seen:
                continue
            seen.add(dependency)
            dependencies.append(dependency)
            queue.append(dependency)
    return dependencies


def _depends_on_names(depends_on: object) -> list[str]:
    if isinstance(depends_on, dict):
        return list(depends_on.keys())
    if isinstance(depends_on, list):
        return [str(item) for item in depends_on]
    return []


def _rewrite_service_for_challenge_root(service_config: dict, *, project_root: Path, challenge_root: Path) -> dict:
    service_config.pop("container_name", None)
    if "build" in service_config:
        service_config["build"] = _rewrite_build(service_config["build"], project_root=project_root, challenge_root=challenge_root)
    if "volumes" in service_config:
        service_config["volumes"] = _rewrite_volumes(service_config["volumes"], project_root=project_root, challenge_root=challenge_root)
    if "networks" in service_config:
        service_config["networks"] = _rewrite_networks_as_logical_names(service_config["networks"])
    return service_config


def _rewrite_networks_as_logical_names(networks: object) -> object:
    if isinstance(networks, dict):
        return list(networks.keys())
    return networks


def _rewrite_build(build_config: object, *, project_root: Path, challenge_root: Path) -> object:
    if isinstance(build_config, str):
        return _rewrite_relative_host_path(build_config, project_root=project_root, challenge_root=challenge_root)
    if isinstance(build_config, dict):
        rewritten = dict(build_config)
        if "context" in rewritten:
            rewritten["context"] = _rewrite_relative_host_path(
                str(rewritten["context"]),
                project_root=project_root,
                challenge_root=challenge_root,
            )
        return rewritten
    return build_config


def _rewrite_volumes(volumes: object, *, project_root: Path, challenge_root: Path) -> object:
    if not isinstance(volumes, list):
        return volumes
    return [
        _rewrite_volume(volume, project_root=project_root, challenge_root=challenge_root)
        for volume in volumes
    ]


def _rewrite_volume(volume: object, *, project_root: Path, challenge_root: Path) -> object:
    if isinstance(volume, str):
        pieces = volume.split(":")
        if len(pieces) < 2:
            return volume
        source = pieces[0]
        if not _is_relative_host_path(source):
            return volume
        pieces[0] = _rewrite_relative_host_path(source, project_root=project_root, challenge_root=challenge_root)
        return ":".join(pieces)

    if isinstance(volume, dict):
        rewritten = dict(volume)
        source = rewritten.get("source")
        if isinstance(source, str) and _is_relative_host_path(source):
            rewritten["source"] = _rewrite_relative_host_path(source, project_root=project_root, challenge_root=challenge_root)
        return rewritten

    return volume


def _is_relative_host_path(path: str) -> bool:
    return path.startswith("./") or path.startswith("../")


def _rewrite_relative_host_path(raw_path: str, *, project_root: Path, challenge_root: Path) -> str:
    path = Path(raw_path)
    if path.is_absolute():
        return raw_path
    corrected = _correct_known_autopenbench_relative_path(path, project_root)
    absolute_path = corrected if corrected is not None else (project_root / path).resolve()
    relative = os.path.relpath(absolute_path, challenge_root.resolve())
    if relative == ".":
        return "."
    if not relative.startswith("."):
        relative = f"./{relative}"
    return relative.replace(os.sep, "/")


def _correct_known_autopenbench_relative_path(path: Path, project_root: Path) -> Path | None:
    pieces = path.parts
    if len(pieces) >= 2 and pieces[0] == "network_security":
        candidate = (project_root / "in-vitro" / path).resolve()
        if candidate.exists():
            return candidate
    return None


def _filter_depends_on(service_config: dict, allowed_services: set[str]) -> None:
    depends_on = service_config.get("depends_on")
    if isinstance(depends_on, dict):
        filtered = {name: value for name, value in depends_on.items() if name in allowed_services}
        if filtered:
            service_config["depends_on"] = filtered
        else:
            service_config.pop("depends_on", None)
    elif isinstance(depends_on, list):
        filtered = [name for name in depends_on if name in allowed_services]
        if filtered:
            service_config["depends_on"] = filtered
        else:
            service_config.pop("depends_on", None)


def _record_service_networks(service_config: dict, referenced_networks: set[str]) -> None:
    networks = service_config.get("networks", {}) or {}
    if isinstance(networks, dict):
        referenced_networks.update(networks.keys())
    elif isinstance(networks, list):
        referenced_networks.update(str(name) for name in networks)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate repo-local AutoPenBench challenge metadata")
    parser.add_argument("--source-root", required=True, help="Path to the AutoPenBench source root")
    parser.add_argument(
        "--benchmark-root",
        default="benchmarks",
        help="Repo-local benchmark root to populate",
    )
    return parser


def _relative_command_milestones_path(source_fields: dict[str, str]) -> str:
    return str(
        Path("../../../../milestones/command_milestones")
        / source_fields["level"]
        / source_fields["category"]
        / f"{source_fields['vm']}.txt"
    )


def _relative_stage_milestones_path(source_fields: dict[str, str]) -> str:
    return str(
        Path("../../../../milestones/stage_milestones")
        / source_fields["level"]
        / source_fields["category"]
        / f"{source_fields['vm']}.txt"
    )


def _relative_solution_path(source_fields: dict[str, str]) -> str:
    return str(
        Path("../../../../solutions")
        / source_fields["level"]
        / source_fields["category"]
        / f"{source_fields['vm']}.txt"
    )


def main() -> None:
    args = _build_parser().parse_args()
    generate_autopenbench_layout(
        source_root=args.source_root,
        benchmark_root=args.benchmark_root,
    )


if __name__ == "__main__":
    main()
