from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

_SECTION_SLUGS = {
    "strategies_and_insights": "str",
    "strategies_&_insights": "str",
    "formulas_and_calculations": "cal",
    "formulas_&_calculations": "cal",
    "code_snippets_and_templates": "cod",
    "code_snippets_&_templates": "cod",
    "common_mistakes_to_avoid": "mis",
    "problem-solving_heuristics": "heu",
    "problem_solving_heuristics": "heu",
    "context_clues_and_indicators": "ctx",
    "context_clues_&_indicators": "ctx",
    "others": "oth",
}

INITIAL_PLAYBOOK = """\
## STRATEGIES & INSIGHTS

## FORMULAS & CALCULATIONS

## CODE SNIPPETS & TEMPLATES

## COMMON MISTAKES TO AVOID

## PROBLEM-SOLVING HEURISTICS

## CONTEXT CLUES & INDICATORS

## OTHERS"""


def _parse_playbook_line(line: str) -> dict | None:
    match = re.match(r"\[([^\]]+)\]\s*helpful=(\d+)\s*harmful=(\d+)\s*::\s*(.*)", line.strip())
    if not match:
        return None
    return {
        "id": match.group(1),
        "helpful": int(match.group(2)),
        "harmful": int(match.group(3)),
        "content": match.group(4),
    }


def _get_next_id(playbook: str) -> int:
    max_id = 0
    for line in str(playbook or "").splitlines():
        parsed = _parse_playbook_line(line)
        if not parsed:
            continue
        id_match = re.search(r"-(\d+)$", parsed["id"])
        if id_match:
            max_id = max(max_id, int(id_match.group(1)))
    return max_id + 1


def _section_slug(section: str) -> str:
    key = section.lower().replace(" ", "_").replace("&", "and")
    return _SECTION_SLUGS.get(key, key[:3])


def _apply_curator_ops(playbook: str, operations: list[dict], next_id: int) -> tuple[str, int]:
    lines = str(playbook or "").split("\n")
    adds: dict[str, list[str]] = {}

    for op in operations:
        if op.get("type") != "ADD":
            continue
        section_raw = str(op.get("section", "others") or "others")
        section = section_raw.lower().replace(" ", "_").replace("&", "and")
        content = str(op.get("content", "") or "").strip()
        if not content:
            continue
        bullet_id = f"{_section_slug(section)}-{next_id:05d}"
        next_id += 1
        adds.setdefault(section, []).append(
            f"[{bullet_id}] helpful=0 harmful=0 :: {content}"
        )

    result: list[str] = []
    current_section: str | None = None
    for line in lines:
        if line.strip().startswith("##"):
            if current_section and current_section in adds:
                result.extend(adds.pop(current_section))
            header = line.strip()[2:].strip()
            current_section = header.lower().replace(" ", "_").replace("&", "and")
        result.append(line)
    if current_section and current_section in adds:
        result.extend(adds.pop(current_section))
    for remaining in adds.values():
        result.extend(remaining)
    return "\n".join(result), next_id


def load_playbook(playbook_path: Path, initial_playbook: str) -> str:
    if playbook_path.exists():
        return playbook_path.read_text(encoding="utf-8")
    return initial_playbook


def save_playbook(playbook_path: Path, playbook: str) -> None:
    playbook_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = playbook_path.with_suffix(".tmp")
    tmp.write_text(playbook, encoding="utf-8")
    tmp.replace(playbook_path)


def collect_batch_artifacts(result_dirs: list[Path]) -> list[dict]:
    artifacts: list[dict] = []
    for result_dir in result_dirs:
        artifact_path = result_dir / "ace_item_artifact.json"
        if not artifact_path.exists():
            continue
        try:
            artifacts.append(json.loads(artifact_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return artifacts


def curate_batch_playbook(
    *,
    playbook: str,
    batch_artifacts: list[dict],
    llm_stub: Any,
    logger: logging.Logger,
) -> tuple[str, dict]:
    """Deterministically merge per-item ACE reflections into the playbook.

    This first batch-curator pass intentionally avoids an extra LLM call so the
    orchestration can be tested independently. Per-item reflectors still decide
    candidate bullets; this function de-duplicates and applies them once.
    """
    del llm_stub
    seen: set[tuple[str, str]] = set()
    operations: list[dict] = []
    candidate_count = 0

    for artifact in batch_artifacts:
        for bullet in artifact.get("new_bullets", []) or []:
            if not isinstance(bullet, dict):
                continue
            section = str(bullet.get("section", "others") or "others")
            content = str(bullet.get("content", "") or "").strip()
            if not content:
                continue
            candidate_count += 1
            key = (section.lower().replace(" ", "_").replace("&", "and"), content)
            if key in seen:
                continue
            seen.add(key)
            operations.append({"type": "ADD", "section": section, "content": content})

    next_id = _get_next_id(playbook)
    updated, _ = _apply_curator_ops(playbook, operations, next_id)
    summary = {
        "candidate_bullets": candidate_count,
        "added_bullets": len(operations),
        "items": len(batch_artifacts),
    }
    logger.info(
        "ACE batch curator added %d/%d candidate bullets from %d artifacts",
        summary["added_bullets"],
        summary["candidate_bullets"],
        summary["items"],
    )
    return updated, summary


def write_scope_state(
    scope_dir: Path,
    *,
    version: int,
    batch_index: int,
    summary: dict,
) -> None:
    scope_dir.mkdir(parents=True, exist_ok=True)
    playbook_path = scope_dir / "playbook.txt"
    history_dir = scope_dir / "playbook_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    if playbook_path.exists():
        (history_dir / f"version_{version:04d}.txt").write_text(
            playbook_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    state = {
        "scope_key": scope_dir.name,
        "playbook_version": version,
        "last_batch_index": batch_index,
        "total_items_curated": int(summary.get("items", 0) or 0),
        "last_summary": summary,
    }
    (scope_dir / "state.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
