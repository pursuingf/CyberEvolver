from __future__ import annotations

import shutil
from pathlib import Path


PROMPT_FILE_MAP = {
    "system_template.txt": "system_template.txt",
    "instance_template.txt": "instance_template.txt",
    "observation_template.txt": "observation_template.txt",
    "output_parse_error_template.txt": "output_parse_error_template.txt",
}


# Some benchmark_family values (from benchmark JSON files) don't match the
# prompt_profiles directory name.  Map them here so templates are found.
_FAMILY_ALIASES: dict[str, str] = {
    "nyu_ctf": "ctfbench",
    "cybench": "ctfbench",
    "intercode_ctf": "ctfbench",
}


def _normalize_family(benchmark_family: str | None) -> str:
    raw = str(benchmark_family or "").strip().lower()
    return _FAMILY_ALIASES.get(raw, raw)


def resolve_prompt_profile_sources(
    project_root: Path,
    benchmark_family: str | None,
    prompt_variant: str | None = None,
) -> dict[str, Path]:
    project_root = Path(project_root)
    defaults_root = project_root / "cyber_evolver" /"gen0_root" / "skill_based"
    family = _normalize_family(benchmark_family)
    variant = str(prompt_variant or "").strip().lower()
    family_root = project_root / "bench_hub" /"benchmarks" / "prompt_profiles" / family if family else None
    variant_root = family_root / variant if family_root is not None and variant else None

    selected: dict[str, Path] = {}
    for destination_name, source_name in PROMPT_FILE_MAP.items():
        default_path = defaults_root / source_name
        if not default_path.exists():
            raise FileNotFoundError(f"Missing default prompt template: {default_path}")

        selected_path = default_path
        if variant_root is not None:
            override_path = variant_root / source_name
            if override_path.exists():
                selected_path = override_path
        if family_root is not None:
            override_path = family_root / source_name
            if selected_path == default_path and override_path.exists():
                selected_path = override_path

        selected[destination_name] = selected_path

    return selected


def materialize_prompt_templates(
    *,
    destination_dir: Path,
    project_root: Path,
    benchmark_family: str | None,
    prompt_variant: str | None = None,
) -> dict[str, Path]:
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    selected = resolve_prompt_profile_sources(
        project_root=Path(project_root),
        benchmark_family=benchmark_family,
        prompt_variant=prompt_variant,
    )

    written: dict[str, Path] = {}
    for destination_name, source_path in selected.items():
        destination_path = destination_dir / destination_name
        shutil.copyfile(source_path, destination_path)
        written[destination_name] = destination_path

    return written
