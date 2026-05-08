from __future__ import annotations

from pathlib import Path
import ast
import difflib
import json
import logging
import py_compile
import re
import tempfile
import time
from typing import Dict, List, Tuple, Optional, Any

import threading
import weakref
from concurrent.futures.thread import _worker, _threads_queues
from jinja2 import Template
from jinja2 import Environment
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from common.utils.util import load_prompt_config, llm_invoke
from common.llm_dispatch.dispatcher import LLMDispatcherFatalError
from concurrent.futures import ThreadPoolExecutor, as_completed

from .codepatcher import CodePatcher, PatchAction, parse_action_blocks


class PhaseValidationError(RuntimeError):
    pass


class DaemonThreadPoolExecutor(ThreadPoolExecutor):
    def _adjust_thread_count(self):
        # if idle threads are available, don't spin new threads
        if self._idle_semaphore.acquire(timeout=0):
            return

        # When the executor gets lost, the weakref callback will wake up
        # the worker threads.
        def weakref_cb(_, q=self._work_queue):
            q.put(None)

        num_threads = len(self._threads)
        if num_threads < self._max_workers:
            thread_name = "%s_%d" % (self._thread_name_prefix or self, num_threads)
            t = threading.Thread(
                name=thread_name,
                target=_worker,
                args=(
                    weakref.ref(self, weakref_cb),
                    self._work_queue,
                    self._initializer,
                    self._initargs,
                ),
            )
            t.daemon = True
            t.start()
            self._threads.add(t)
            _threads_queues[t] = self._work_queue


_ALLOWED_ABLATION_MODES = ("none", "holistic", "no_forensic")


class RefinerLLMClient:
    def __init__(
        self,
        llm,
        prompt_cfg_path: str = "cyber_evolver/evolve/prompt.yml",
        logger=None,
        ablation_mode: str = "none",
    ):
        self.llm = llm
        self.prompt_cfg = load_prompt_config(prompt_cfg_path)
        self.logger = logger or logging.getLogger(self.__class__.__name__)
        self._patcher = CodePatcher(logger=self.logger)

        normalized_mode = (ablation_mode or "none").strip().lower()
        if normalized_mode not in _ALLOWED_ABLATION_MODES:
            raise ValueError(
                f"Unsupported ablation_mode: {ablation_mode!r}. "
                f"Must be one of {_ALLOWED_ABLATION_MODES}."
            )
        self.ablation_mode = normalized_mode

        # Define the sequential evolution phases
        self.evolution_pipeline = [
            {"id": 1, "name": "Evolution Phase1", "config_key": "user_prompt_coderefiner_phase_1"},
            {"id": 2, "name": "Evolution Phase2", "config_key": "user_prompt_coderefiner_phase_2"},
            {"id": 3, "name": "Evolution Phase3", "config_key": "user_prompt_coderefiner_phase_3"},
            {"id": 4, "name": "Evolution Phase4", "config_key": "user_prompt_coderefiner_phase_4"},
        ]

    # ----------------------------
    # Patch parsing & validation
    # ----------------------------
    @staticmethod
    def _normalize_rel_path(path: str) -> str:
        p = (path or "").strip()
        while p.startswith("./"):
            p = p[2:]
        return p

    @classmethod
    def _parse_patch_actions(cls, plan_text: str) -> List[PatchAction]:
        """
        Extract patch actions from the LLM output (Action-Based XML format).
        We intentionally only parse a minimal subset needed for strict validation.
        """
        return parse_action_blocks(plan_text)

    @staticmethod
    def _dump_failed_patch_attempt(
        out_dir: Path,
        phase_id: int,
        phase_name: str,
        attempt: int,
        error: str,
        plan_text: str,
        actions: List[PatchAction],
        summary: str,
        tmp_root: Path | None,
    ) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.time_ns()
        base = out_dir / f"phase{phase_id}_attempt{attempt}_{ts}"

        (base.with_suffix(".error.txt")).write_text(error or "", encoding="utf-8")
        (base.with_suffix(".response.txt")).write_text(plan_text or "", encoding="utf-8")

        actions_payload = [
            {
                "kind": a.kind,
                "start": int(a.start),
                "path": a.path,
                "search": a.search,
                "replace": a.replace,
                "content": a.content,
            }
            for a in actions
        ]
        meta = {
            "phase_id": phase_id,
            "phase_name": phase_name,
            "attempt": attempt,
            "error": error,
            "summary": summary,
            "actions": actions_payload,
        }
        (base.with_suffix(".json")).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        if tmp_root is None:
            return

        patch_log = tmp_root / "patch_apply.log"
        if patch_log.exists():
            (base.with_suffix(".patch_apply.log")).write_text(patch_log.read_text(encoding="utf-8"), encoding="utf-8")

    @staticmethod
    def _existing_skill_names(skills_dict: Dict[str, str]) -> set[str]:
        names: set[str] = set()
        for rel_path in (skills_dict or {}).keys():
            rel_path = rel_path.strip()
            if not rel_path.startswith("skills/"):
                continue
            parts = rel_path.split("/")
            if len(parts) >= 2:
                names.add(parts[1])
        return names

    @staticmethod
    def _is_safe_rel_path(rel_path: str) -> bool:
        if not rel_path:
            return False
        if ".." in rel_path:
            return False
        if rel_path.startswith("/"):
            return False
        if "submit.py" in rel_path:
            return False
        return True

    def _holistic_policy_instructions(self, context: Dict[str, Any]) -> str:
        existing = sorted(self._existing_skill_names(context.get("skills", {})))
        existing_str = ", ".join(existing) if existing else "(none)"
        return (
            "\n\n[HARD CONSTRAINTS — Holistic Mode]\n"
            "- You may patch any of: `system_template.txt`, `instance_template.txt`, "
            "`agent.py`, `observation_template.txt`, `output_parse_error_template.txt`, "
            "and files under `skills/`.\n"
            "- For non-skill files use only `<replace_code>` (no create/delete).\n"
            "- For files under `skills/` you may use `<create_file>`, `<replace_code>`, `<delete_file>`.\n"
            "- You MUST NOT delete `skills/skill_template` or any file beneath it.\n"
            f"- Existing skills: {existing_str}\n"
            "- If no improvement is needed in any layer, output analysis and NO patches.\n"
        )

    def _phase_policy_instructions(self, phase_id: int, context: Dict[str, Any]) -> str:
        if phase_id == 1:
            return (
                "\n\n[HARD CONSTRAINTS]\n"
                "- You may ONLY patch `system_template.txt`.\n"
                "- You may ONLY use `<replace_code>` actions (no create/delete).\n"
                "- If no improvement is needed, output analysis and NO patches.\n"
            )
        if phase_id == 2:
            return (
                "\n\n[HARD CONSTRAINTS]\n"
                "- You may ONLY patch `instance_template.txt`.\n"
                "- You may ONLY use `<replace_code>` actions (no create/delete).\n"
                "- If no improvement is needed, output analysis and NO patches.\n"
            )
        if phase_id == 3:
            existing = sorted(self._existing_skill_names(context.get("skills", {})))
            existing_str = ", ".join(existing) if existing else "(none)"
            return (
                "\n\n[HARD CONSTRAINTS]\n"
                "- You may ONLY patch files under `skills/`.\n"
                "- You may use `<create_file>`, `<replace_code>`, and `<delete_file>` under `skills/`.\n"
                "- You MAY add new skills, modify existing skills, or delete obsolete skills under `skills/`.\n"
                "- You MUST NOT delete `skills/skill_template` or any file beneath it.\n"
                f"- Existing skills: {existing_str}\n"
                "- If no improvement is needed, output analysis and NO patches.\n"
            )
        if phase_id == 4:
            return (
                "\n\n[HARD CONSTRAINTS]\n"
                "- You may ONLY patch: `agent.py`, `observation_template.txt`, `output_parse_error_template.txt`.\n"
                "- You may ONLY use `<replace_code>` actions (no create/delete).\n"
                "- `agent.py` edits may include import changes if required.\n"
                "- Keep changes minimal and challenge-agnostic; never touch flag submission logic.\n"
                "- Keep changes to each template file minimal (few lines; avoid rewrites) unless required to fix syntax.\n"
                "- If no improvement is needed, output analysis and NO patches.\n"
            )
        return ""

    def _validate_actions_holistic(self, actions: List[PatchAction], context: Dict[str, Any]) -> List[str]:
        """Validation for Ablation A holistic mode: union of all 4 phases' allowed scopes."""
        errors: List[str] = []

        if not actions:
            return errors

        non_skill_paths = {
            "system_template.txt",
            "instance_template.txt",
            "agent.py",
            "observation_template.txt",
            "output_parse_error_template.txt",
        }

        for a in actions:
            normalized = self._normalize_rel_path(a.path)
            if not self._is_safe_rel_path(normalized):
                errors.append(f"unsafe path: {a.path!r}")
                continue

            is_skill_path = normalized.startswith("skills/")
            if not (is_skill_path or normalized in non_skill_paths):
                errors.append(f"disallowed path in holistic mode: {a.path}")
                continue

            if is_skill_path:
                if a.kind not in {"create_file", "replace_code", "delete_file"}:
                    errors.append(f"disallowed action kind for skills/: {a.kind} ({a.path})")
            else:
                if a.kind != "replace_code":
                    errors.append(f"only <replace_code> allowed for {a.path}: got {a.kind}")

        existing_files = set((context.get("skills", {}) or {}).keys())
        created_paths = [self._normalize_rel_path(a.path) for a in actions if a.kind == "create_file"]
        for p in created_paths:
            if p in existing_files or f"./{p}" in existing_files:
                errors.append(f"holistic mode cannot overwrite existing skill file: {p}")

        protected_skill_root = "skills/skill_template"
        for a in actions:
            if a.kind != "delete_file":
                continue
            normalized = self._normalize_rel_path(a.path).rstrip("/")
            if normalized == protected_skill_root or normalized.startswith(f"{protected_skill_root}/"):
                errors.append(f"holistic mode cannot delete protected skill module content: {a.path}")

        return errors

    def _validate_actions_for_phase(self, phase_id: int, actions: List[PatchAction], context: Dict[str, Any]) -> List[str]:
        errors: List[str] = []

        if not actions:
            return errors

        def _allow_path(normalized: str) -> bool:
            if phase_id == 1:
                return normalized == "system_template.txt"
            if phase_id == 2:
                return normalized == "instance_template.txt"
            if phase_id == 3:
                return normalized.startswith("skills/")
            if phase_id == 4:
                return normalized in {
                    "agent.py",
                    "observation_template.txt",
                    "output_parse_error_template.txt",
                }
            return False

        allowed_kinds = (
            {"replace_code"}
            if phase_id in (1, 2, 4)
            else {"create_file", "replace_code", "delete_file"}
            if phase_id == 3
            else set()
        )

        for a in actions:
            normalized = self._normalize_rel_path(a.path)
            if not self._is_safe_rel_path(normalized):
                errors.append(f"unsafe path: {a.path!r}")
                continue
            if a.kind not in allowed_kinds:
                errors.append(f"disallowed action kind in phase {phase_id}: {a.kind} ({a.path})")
            if not _allow_path(normalized):
                errors.append(f"disallowed path in phase {phase_id}: {a.path}")

        if phase_id == 3:
            existing_files = set((context.get("skills", {}) or {}).keys())
            created_paths = [self._normalize_rel_path(a.path) for a in actions if a.kind == "create_file"]
            for p in created_paths:
                if p in existing_files or f"./{p}" in existing_files:
                    errors.append(f"phase 3 cannot overwrite existing skill file: {p}")

            protected_skill_root = "skills/skill_template"
            for a in actions:
                if a.kind != "delete_file":
                    continue
                normalized = self._normalize_rel_path(a.path).rstrip("/")
                if normalized == protected_skill_root or normalized.startswith(f"{protected_skill_root}/"):
                    errors.append(f"phase 3 cannot delete protected skill module content: {a.path}")

        if phase_id == 4:
            # No additional structural constraints for agent.py at this stage.
            pass

        return errors

    @staticmethod
    def _write_file(root: Path, rel_path: str, content: str) -> None:
        full_path = root / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content or "", encoding="utf-8")

    def _materialize_context_tree(self, root: Path, context: Dict[str, Any]) -> None:
        self._write_file(root, "agent.py", context.get("agent.py", ""))
        for filename, content in (context.get("prompt_templates", {}) or {}).items():
            normalized = self._normalize_rel_path(str(filename))
            self._write_file(root, normalized, content or "")
        for rel_path, content in (context.get("skills", {}) or {}).items():
            normalized = self._normalize_rel_path(rel_path)
            self._write_file(root, normalized, content or "")

    @staticmethod
    def _compile_python_tree(root: Path) -> List[str]:
        errors: List[str] = []
        for py_path in sorted(root.rglob("*.py")):
            try:
                py_compile.compile(str(py_path), doraise=True)
            except Exception as e:
                rel = str(py_path.relative_to(root))
                errors.append(f"python compile failed: {rel}: {e}")
        return errors

    @staticmethod
    def _validate_jinja_templates(root: Path) -> List[str]:
        """
        Validate Jinja syntax for the core prompt templates.
        This catches common mistakes like unclosed blocks, bad filters, etc.
        """
        errors: List[str] = []
        env = Environment()
        for filename in (
            "system_template.txt",
            "instance_template.txt",
            "observation_template.txt",
            "output_parse_error_template.txt",
        ):
            p = root / filename
            if not p.exists():
                continue
            try:
                env.parse(p.read_text(encoding="utf-8"))
            except Exception as e:
                errors.append(f"jinja syntax failed: {filename}: {e}")
        return errors

    @staticmethod
    def _validate_phase4_unicode_safety(root: Path) -> List[str]:
        errors: List[str] = []

        agent_path = root / "agent.py"
        if agent_path.exists():
            try:
                agent_text = agent_path.read_text(encoding="utf-8")
                tree = ast.parse(agent_text)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Name) and not node.id.isascii():
                        errors.append(
                            f"non-ascii identifier rejected: agent.py:{getattr(node, 'lineno', '?')} token={node.id!r}"
                        )
                    elif isinstance(node, ast.Attribute) and not node.attr.isascii():
                        errors.append(
                            f"non-ascii attribute rejected: agent.py:{getattr(node, 'lineno', '?')} token={node.attr!r}"
                        )
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.isascii():
                        errors.append(
                            f"non-ascii definition name rejected: agent.py:{getattr(node, 'lineno', '?')} token={node.name!r}"
                        )
                    elif isinstance(node, ast.arg) and not node.arg.isascii():
                        errors.append(
                            f"non-ascii argument rejected: agent.py:{getattr(node, 'lineno', '?')} token={node.arg!r}"
                        )
            except Exception as e:
                errors.append(f"unicode safety scan failed: agent.py: {e}")

        jinja_block_pattern = re.compile(r"(\{\{.*?\}\}|\{%.*?%\})", re.DOTALL)
        for filename in ("observation_template.txt", "output_parse_error_template.txt"):
            template_path = root / filename
            if not template_path.exists():
                continue
            try:
                template_text = template_path.read_text(encoding="utf-8")
                for match in jinja_block_pattern.finditer(template_text):
                    block = match.group(1)
                    if any(ord(ch) > 127 for ch in block):
                        errors.append(
                            f"non-ascii jinja block rejected: {filename}: {block[:80]!r}"
                        )
                        break
            except Exception as e:
                errors.append(f"unicode safety scan failed: {filename}: {e}")

        return errors

    @staticmethod
    def _changed_line_count(old_text: str, new_text: str) -> int:
        old_lines = (old_text or "").splitlines()
        new_lines = (new_text or "").splitlines()
        sm = difflib.SequenceMatcher(a=old_lines, b=new_lines)
        changed = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            changed += max(i2 - i1, j2 - j1)
        return changed

    @staticmethod
    def _changed_line_anchors(old_text: str, new_text: str) -> tuple[set[int], set[int]]:
        old_lines = (old_text or "").splitlines()
        new_lines = (new_text or "").splitlines()
        sm = difflib.SequenceMatcher(a=old_lines, b=new_lines)
        changed_old_lines: set[int] = set()
        inserted_at_old: set[int] = set()
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("replace", "delete"):
                changed_old_lines.update(range(i1 + 1, i2 + 1))
            if tag in ("replace", "insert"):
                inserted_at_old.add(i1 + 1)
        return changed_old_lines, inserted_at_old

    @staticmethod
    def _in_any_range(n: int, ranges: List[tuple[int, int]]) -> bool:
        return any(start <= n <= end for start, end in ranges)

    def _validate_phase4_line_scopes(self, context: Dict[str, Any], temp_root: Path) -> List[str]:
        errors: List[str] = []

        old_agent = context.get("agent.py", "") or ""
        new_agent_path = temp_root / "agent.py"
        new_agent = new_agent_path.read_text(encoding="utf-8") if new_agent_path.exists() else ""

        try:
            ast.parse(new_agent)
        except Exception as e:
            errors.append(f"phase 4 produced invalid Python in agent.py: {e}")
            return errors

        old_lines = old_agent.splitlines()
        obs_anchor = None
        err_anchor = None
        for i, line in enumerate(old_lines, start=1):
            if "Template(self.prompt_templates.observation_template).render" in line:
                obs_anchor = i
            if "Template(self.prompt_templates.output_parse_error_template).render" in line:
                err_anchor = i

        if obs_anchor is None or err_anchor is None:
            errors.append("phase 4 validator could not locate required render-call anchors in agent.py")
            return errors

        allowed_ranges: List[tuple[int, int]] = []
        for anchor in (obs_anchor, err_anchor):
            allowed_ranges.append((max(1, anchor - 2), anchor + 16))

        changed_old, inserted_at = self._changed_line_anchors(old_agent, new_agent)
        out_of_scope = sorted(
            n for n in (changed_old | inserted_at) if not self._in_any_range(n, allowed_ranges)
        )
        if out_of_scope:
            errors.append(
                "phase 4 agent.py edit out of allowed line scope; offending old-line anchors: "
                + ", ".join(map(str, out_of_scope[:20]))
            )

        for filename in ("observation_template.txt", "output_parse_error_template.txt"):
            old_text = (context.get("prompt_templates", {}) or {}).get(filename, "") or ""
            new_path = temp_root / filename
            if not new_path.exists():
                continue
            new_text = new_path.read_text(encoding="utf-8") or ""
            changed = self._changed_line_count(old_text, new_text)
            if changed > 30:
                errors.append(f"phase 4 changed too many lines in {filename}: {changed} (>30)")

        return errors

    def _apply_and_validate_holistic_patch(
        self,
        attempt: int,
        plan_text: str,
        context: Dict[str, Any],
        failure_log_dir: Path | None,
    ) -> None:
        """Materialize + validate patches in holistic mode (Ablation A)."""
        actions: List[PatchAction] = []
        summary = "No changes"
        tmp_root: Path | None = None
        phase_id = 0  # sentinel for holistic; only used for failure-log naming
        phase_name = "Evolution Holistic"

        try:
            actions = self._parse_patch_actions(plan_text)
            action_errors = self._validate_actions_holistic(actions, context)
            if action_errors:
                raise PhaseValidationError(" ; ".join(action_errors))

            td = tempfile.TemporaryDirectory(prefix="evo_holistic_")
            try:
                tmp_root = Path(td.name)
                self._materialize_context_tree(tmp_root, context)

                if actions:
                    patch_log = tmp_root / "patch_apply.log"
                    summary = self._patcher.apply_patches(tmp_root, plan_text, patch_log)

                    if (
                        summary.strip() == "No changes"
                        and failure_log_dir is not None
                        and patch_log.exists()
                    ):
                        patch_log_text = patch_log.read_text(encoding="utf-8", errors="replace")
                        if (
                            "💥 ERROR processing" in patch_log_text
                            or "❌ FAILED" in patch_log_text
                            or "Anchor failed" in patch_log_text
                        ):
                            self._dump_failed_patch_attempt(
                                out_dir=failure_log_dir / "no_changes",
                                phase_id=phase_id,
                                phase_name=phase_name,
                                attempt=attempt,
                                error="patch produced no changes (see patch_apply.log)",
                                plan_text=plan_text,
                                actions=actions,
                                summary=summary,
                                tmp_root=tmp_root,
                            )

                compile_errors = self._compile_python_tree(tmp_root)
                if compile_errors:
                    raise PhaseValidationError(" ; ".join(compile_errors[:5]))

                jinja_errors = self._validate_jinja_templates(tmp_root)
                if jinja_errors:
                    raise PhaseValidationError(" ; ".join(jinja_errors[:5]))

                # Run unicode safety on agent.py + observation/error templates if any of
                # those were potentially modified — same protection as phase 4.
                if any(
                    self._normalize_rel_path(a.path) in {
                        "agent.py",
                        "observation_template.txt",
                        "output_parse_error_template.txt",
                    }
                    for a in actions
                ):
                    unicode_errors = self._validate_phase4_unicode_safety(tmp_root)
                    if unicode_errors:
                        raise PhaseValidationError(" ; ".join(unicode_errors[:5]))

                if actions and summary.strip() == "No changes":
                    self.logger.info("Holistic patch applied no-op (summary=%r).", summary)
            finally:
                td.cleanup()

        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            if failure_log_dir is not None:
                try:
                    self._dump_failed_patch_attempt(
                        out_dir=failure_log_dir,
                        phase_id=phase_id,
                        phase_name=phase_name,
                        attempt=attempt,
                        error=str(e),
                        plan_text=plan_text,
                        actions=actions,
                        summary=summary,
                        tmp_root=tmp_root,
                    )
                except Exception:
                    pass
            raise

    def _apply_and_validate_phase_patch(
        self,
        phase_id: int,
        phase_name: str,
        attempt: int,
        plan_text: str,
        context: Dict[str, Any],
        failure_log_dir: Path | None,
    ) -> None:
        actions: List[PatchAction] = []
        summary = "No changes"
        tmp_root: Path | None = None

        try:
            actions = self._parse_patch_actions(plan_text)
            action_errors = self._validate_actions_for_phase(phase_id, actions, context)
            if action_errors:
                raise PhaseValidationError(" ; ".join(action_errors))

            td = tempfile.TemporaryDirectory(prefix=f"evo_phase{phase_id}_")
            try:
                tmp_root = Path(td.name)
                self._materialize_context_tree(tmp_root, context)

                if actions:
                    patch_log = tmp_root / "patch_apply.log"
                    summary = self._patcher.apply_patches(tmp_root, plan_text, patch_log)

                    # "No changes" is valid, but log artifacts when the patcher log indicates
                    # the model attempted changes that failed to apply (useful for prompt iteration).
                    if (
                        summary.strip() == "No changes"
                        and failure_log_dir is not None
                        and patch_log.exists()
                    ):
                        patch_log_text = patch_log.read_text(encoding="utf-8", errors="replace")
                        if (
                            "💥 ERROR processing" in patch_log_text
                            or "❌ FAILED" in patch_log_text
                            or "Anchor failed" in patch_log_text
                        ):
                            self._dump_failed_patch_attempt(
                                out_dir=failure_log_dir / "no_changes",
                                phase_id=phase_id,
                                phase_name=phase_name,
                                attempt=attempt,
                                error="patch produced no changes (see patch_apply.log)",
                                plan_text=plan_text,
                                actions=actions,
                                summary=summary,
                                tmp_root=tmp_root,
                            )

                # Always run syntax checks after each LLM call (even if no patches were proposed/applied).
                compile_errors = self._compile_python_tree(tmp_root)
                if compile_errors:
                    raise PhaseValidationError(" ; ".join(compile_errors[:5]))

                jinja_errors = self._validate_jinja_templates(tmp_root)
                if jinja_errors:
                    raise PhaseValidationError(" ; ".join(jinja_errors[:5]))

                if phase_id == 4:
                    unicode_errors = self._validate_phase4_unicode_safety(tmp_root)
                    if unicode_errors:
                        raise PhaseValidationError(" ; ".join(unicode_errors[:5]))

                # Phase 4 structural scope enforcement intentionally disabled (imports at top must be allowed).

                if actions and summary.strip() == "No changes":
                    self.logger.info("Phase %s patch applied no-op (summary=%r).", phase_id, summary)
            finally:
                td.cleanup()

        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            if failure_log_dir is not None:
                try:
                    self._dump_failed_patch_attempt(
                        out_dir=failure_log_dir,
                        phase_id=phase_id,
                        phase_name=phase_name,
                        attempt=attempt,
                        error=str(e),
                        plan_text=plan_text,
                        actions=actions,
                        summary=summary,
                        tmp_root=tmp_root,
                    )
                except Exception:
                    # Never let debug logging mask the original failure.
                    pass
            raise

    def _format_skills_context(
        self,
        skills_dict: Dict[str, str],
        p_summaries: Optional[List[Tuple[str, str]]] = None,
        max_skills: int = 4,
        template_skill_name: str = "skill_template",
    ) -> str:
        """
        Format the skills context for the LLM.

        Output has two parts:
        1) Selected Skill Modules: include full content only for selected modules (based on template or reports).
        2) Other Skill Modules: include description.md for all modules (path + description).

        If the number of skill modules > max_skills:
        - always include skill_template first (if exists)
        - include other skills only if `skill_name in report` for any report in p_summaries
        - cap total selected modules to max_skills
        """
        if not skills_dict:
            return "(No skills currently available)"

        def _clean_desc(text: str, max_lines: int = 3, max_chars: int = 400) -> str:
            """Clean description to a short, concise version."""
            if not text:
                return "(empty)"
            s = "\n".join(line.rstrip() for line in text.strip().splitlines())
            lines = [ln for ln in s.splitlines() if ln.strip() != ""]
            if not lines:
                return "(empty)"
            s = "\n".join(lines[:max_lines])
            if len(s) > max_chars:
                s = s[:max_chars].rstrip() + "…"
            return s

        # 1) Collect skill modules + their description.md (for catalog)
        skill_modules = []
        skill_desc: Dict[str, str] = {}  # skill_name -> description content
        skill_paths: Dict[str, str] = {}  # skill_name -> full path

        for path, content in skills_dict.items():
            parts = path.split("/")
            if len(parts) >= 2 and parts[0] == "skills":
                skill_name = parts[1]
                skill_modules.append(skill_name)
                skill_paths[skill_name] = path  # Store full path for each skill

                # Capture description.md if present
                if parts[-1] == "description.md":
                    skill_desc[skill_name] = content

        unique_skills = sorted(set(skill_modules))

        # 2) Selection logic (filter selected skills)
        need_filter = len(unique_skills) > max_skills
        selected_skills = set(unique_skills)

        if need_filter:
            selected_skills = set()

            # Always keep template first (if exists)
            if template_skill_name in unique_skills:
                selected_skills.add(template_skill_name)

            # Only keep skills whose name appears in any report
            reports = ""
            if p_summaries:
                reports = "\n".join((r or "") for _, r in p_summaries)

            for s in unique_skills:
                if s == template_skill_name:
                    continue
                if reports and (s in reports):
                    selected_skills.add(s)
                if len(selected_skills) >= max_skills:
                    break

            # Fallback: if template missing and none matched, keep first max_skills
            if not selected_skills:
                selected_skills = set(unique_skills[:max_skills])

        # 3) Deterministic path sort; template module first
        def sort_key(path: str):
            parts = path.split("/")
            skill = parts[1] if len(parts) >= 2 else ""
            is_template = 0 if skill == template_skill_name else 1
            return (is_template, skill, path)

        sorted_paths = sorted(skills_dict.keys(), key=sort_key)

        # 4) Format selected full content
        formatted = []
        formatted.append("\n## Selected Skill Modules (full content)")
        formatted.append(
            f"Selected modules ({len(selected_skills)}/{len(unique_skills)}): "
            + ", ".join(sorted(selected_skills))
        )

        current_skill = None
        for path in sorted_paths:
            parts = path.split("/")
            if len(parts) < 2 or parts[0] != "skills":
                continue

            skill_name = parts[1]
            if skill_name not in selected_skills:
                continue

            filename = parts[-1]

            if skill_name != current_skill:
                formatted.append(f"\n# --- Skill Module: {skill_name} ---")
                formatted.append(f"Path: {skill_paths[skill_name]}")  # Display path
                current_skill = skill_name

            content = skills_dict[path]
            lang = ""
            if filename.endswith(".py"):
                lang = "python"
            elif filename.endswith((".sh", ".bash")):
                lang = "bash"
            elif filename.endswith(".md"):
                lang = "markdown"
            elif filename.endswith(".json"):
                lang = "json"

            formatted.append(f"## File: {path}\n```{lang}\n{content}\n```")

        # 5) Format other skills (only show description)
        other_skills_desc = []
        other_skills_desc.append("\n## Other Skill Modules (descriptions only)")
        other_skills_desc.append(
            f"- Total modules: {len(unique_skills)}; full content capped at {max_skills}.\n"
            f"- Showing description.md for unselected modules."
        )

        for s in unique_skills:
            if s not in selected_skills:
                desc = skill_desc.get(s, "")
                if not desc:
                    other_skills_desc.append(f"\n### {s} (missing `skills/{s}/description.md`)")
                else:
                    other_skills_desc.append(f"\n### {s}\nPath: {skill_paths[s]}\n{_clean_desc(desc)}")

        # Combine selected skills and other skills
        return "\n".join(formatted + other_skills_desc) if (formatted or other_skills_desc) else "(No skills currently available)"


    
    def generate_plan(
        self,
        p_summaries: List[Tuple[str, str]],
        gp_summaries: List[Tuple[str, str]],
        context: Dict[str, Any],
        failure_log_dir: str | Path | None = None,
    ) -> str:
        """
        Executes the multi-phase evolution protocol in parallel.
        Each phase runs independently with its own context, no shared message history.
        """

        # 1. Prepare raw context primitives (shared by all phases)
        tools_str = ""
        for rel_path, content in context.get("tools", {}).items():
            lang = "python" if rel_path.endswith(".py") else "bash" if rel_path.endswith((".sh", ".bash")) else ""
            tools_str += f"## File: {rel_path}\n```{lang}\n{content}\n```\n"

        skills_raw = context.get("skills", {})
        skills_str = self._format_skills_context(skills_raw, p_summaries)

        system_prompt = self.prompt_cfg.get("system_prompt_coderefiner", "")
        base_user_tmpl = self.prompt_cfg.get("user_prompt_coderefiner", "")

        if not base_user_tmpl or not system_prompt:
            self.logger.error("Critical evolution prompts missing in prompt.yml")
            raise ValueError("Core evolution templates are undefined.")

        # Render the foundational knowledge (Logs, Current Code, Arsenal)
        user_template = Template(base_user_tmpl)
        rendered_base_context = user_template.render(
            p_summaries=p_summaries,
            gp_summaries=gp_summaries,
            agent_implementation=context.get("agent.py", ""),
            prompt_templates=context.get("prompt_templates", ""),
            tools_context=tools_str,
            skill_context=skills_str,
            patch=context.get("patch", "")
        )

        failure_log_dir_path = Path(failure_log_dir) if failure_log_dir is not None else None

        if self.ablation_mode == "holistic":
            self.logger.info("Base context prepared, starting holistic mutation (Ablation A)...")
            return self._execute_holistic(
                rendered_base_context,
                system_prompt,
                context,
                failure_log_dir_path,
            )

        self.logger.info(f"Base context prepared, starting parallel evolution phases...")

        # 2. Execute all phases in parallel
        phase_results = self._execute_phases_parallel(
            rendered_base_context,
            system_prompt,
            context,
            failure_log_dir_path,
        )

        # 3. Combine results from all phases
        return self._combine_phase_results(phase_results)

    def _execute_holistic(
        self,
        rendered_base_context: str,
        system_prompt: str,
        context: Dict[str, Any],
        failure_log_dir: Path | None,
    ) -> str:
        """Single-call holistic mutation (Ablation A).

        Reads `user_prompt_coderefiner_holistic` from prompt_cfg as the mandate,
        renders gen0_system_template, makes one LLM call, validates against the
        union of all phase scopes, retries up to 3 times on validation failure.
        """
        mandate_raw = self.prompt_cfg.get(
            "user_prompt_coderefiner_holistic",
            "Proceed with a single holistic mutation across any allowed files.",
        )
        mandate_instr = Template(mandate_raw).render(
            gen0_system_template=context.get("gen0_system_template", ""),
        )
        policy_instr = self._holistic_policy_instructions(context)

        max_attempts = 3
        last_err: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            current_payload = f"{rendered_base_context}\n\n{mandate_instr}{policy_instr}"
            message_history: List[BaseMessage] = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=current_payload),
            ]
            self.logger.debug(
                "Starting holistic mutation attempt %d/%d (payload=%d chars)",
                attempt,
                max_attempts,
                len(current_payload),
            )

            try:
                response = llm_invoke(
                    self.llm,
                    message_history,
                    meta={
                        "component": "cyber_evolver.evolve.refiner_agent",
                        "phase_id": 0,
                        "phase_name": "Evolution Holistic",
                        "attempt": attempt,
                        "ablation_mode": "holistic",
                    },
                )
                evolution_segment = (response.content or "").strip()
                self._apply_and_validate_holistic_patch(
                    attempt=attempt,
                    plan_text=evolution_segment,
                    context=context,
                    failure_log_dir=failure_log_dir,
                )
                header = (
                    f"\n\n{'='*20}\n"
                    f"HOLISTIC MUTATION (Ablation A — single LLM call)\n"
                    f"{'='*20}\n"
                )
                return header + evolution_segment
            except (KeyboardInterrupt, SystemExit):
                raise
            except LLMDispatcherFatalError:
                raise
            except Exception as e:
                last_err = e
                self.logger.warning(
                    "Holistic mutation attempt %d failed validation/apply: %s",
                    attempt,
                    str(e),
                )

        raise PhaseValidationError(
            f"Holistic mutation failed after {max_attempts} attempts: {last_err}"
        )

    def _execute_phases_parallel(
        self,
        rendered_base_context: str,
        system_prompt: str,
        context: Dict[str, Any],
        failure_log_dir: Path | None,
    ) -> Dict[int, Dict[str, Any]]:
        """
        Execute all evolution phases in parallel.
        Each phase runs independently with its own message history.
        """
        phase_results = {}

        executor = DaemonThreadPoolExecutor(max_workers=len(self.evolution_pipeline))
        futures: List[Any] = []
        future_to_phase: Dict[Any, Dict[str, Any]] = {}
        try:
            # Submit all phase tasks
            for phase in self.evolution_pipeline:
                future = executor.submit(
                    self._execute_single_phase,
                    phase,
                    rendered_base_context,
                    system_prompt,
                    context,
                    failure_log_dir,
                )
                futures.append(future)
                future_to_phase[future] = phase

            # Collect results as they complete
            for future in as_completed(future_to_phase):
                phase = future_to_phase[future]
                try:
                    result = future.result()
                    phase_results[phase["id"]] = {
                        "phase": phase,
                        "result": result,
                        "success": True,
                    }
                    self.logger.info(f"✅ Phase {phase['id']} ({phase['name']}) completed successfully")
                except (KeyboardInterrupt, SystemExit):
                    raise
                except LLMDispatcherFatalError:
                    raise
                except Exception as e:
                    self.logger.error(f"❌ Phase {phase['id']} ({phase['name']}) failed: {e}", exc_info=True)
                    phase_results[phase["id"]] = {
                        "phase": phase,
                        "error": str(e),
                        "success": False,
                    }
        except (KeyboardInterrupt, SystemExit):
            # Avoid hanging on executor shutdown(wait=True) when user is interrupting.
            for f in futures:
                f.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            # Normal path: wait for completion.
            executor.shutdown(wait=True, cancel_futures=False)

        return phase_results

    def _execute_single_phase(
        self,
        phase: Dict,
        rendered_base_context: str,
        system_prompt: str,
        context: Dict[str, Any],
        failure_log_dir: Path | None,
    ) -> str:
        """
        Execute a single evolution phase independently.
        Each phase gets its own fresh message history.
        """
        phase_id = int(phase["id"])
        mandate_raw = self.prompt_cfg.get(phase["config_key"], f"Proceed with {phase['name']}.")
        mandate_instr = Template(mandate_raw).render(
            gen0_system_template=context.get("gen0_system_template", ""),
        )
        policy_instr = self._phase_policy_instructions(phase_id, context)

        max_attempts = 3
        last_err: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            current_payload = f"{rendered_base_context}\n\n{mandate_instr}{policy_instr}"

            message_history: List[BaseMessage] = [SystemMessage(content=system_prompt), HumanMessage(content=current_payload)]
            self.logger.debug(
                "Starting phase %s (%s) attempt %d/%d (payload=%d chars)",
                phase_id,
                phase["name"],
                attempt,
                max_attempts,
                len(current_payload),
            )

            try:
                response = llm_invoke(
                    self.llm,
                    message_history,
                    meta={
                        "component": "cyber_evolver.evolve.refiner_agent",
                        "phase_id": phase_id,
                        "phase_name": phase["name"],
                        "attempt": attempt,
                    },
                )
                evolution_segment = (response.content or "").strip()
                self._apply_and_validate_phase_patch(
                    phase_id=phase_id,
                    phase_name=phase["name"],
                    attempt=attempt,
                    plan_text=evolution_segment,
                    context=context,
                    failure_log_dir=failure_log_dir,
                )
                return evolution_segment
            except (KeyboardInterrupt, SystemExit):
                raise
            except LLMDispatcherFatalError:
                raise
            except Exception as e:
                last_err = e
                self.logger.warning(
                    "Phase %s (%s) attempt %d failed validation/apply: %s",
                    phase_id,
                    phase["name"],
                    attempt,
                    str(e),
                )

        raise PhaseValidationError(
            f"Phase {phase_id} ({phase['name']}) failed after {max_attempts} attempts: {last_err}"
        )

    def _combine_phase_results(self, phase_results: Dict[int, Dict[str, Any]]) -> str:
        """
        Combine results from all parallel phases into a single output.
        """
        combined_output = []

        # Sort by phase ID for consistent output order
        sorted_phase_ids = sorted(phase_results.keys())

        for phase_id in sorted_phase_ids:
            result_info = phase_results[phase_id]
            phase = result_info['phase']

            header = f"\n\n{'='*20}\nPHASE {phase['id']}: {phase['name']}\n{'='*20}\n"
            combined_output.append(header)

            if result_info.get('success', False):
                combined_output.append(result_info['result'])
            else:
                error_msg = f"CRITICAL FAILURE IN PHASE {phase['id']}: {result_info.get('error', 'Unknown error')}"
                combined_output.append(error_msg)
                self.logger.error(error_msg)

        # Add summary header
        summary_header = f"\n{'='*40}\nPARALLEL EVOLUTION COMPLETE\n{'='*40}\n"
        summary_header += f"Executed {len(phase_results)} phases in parallel\n"
        summary_header += f"Successful phases: {sum(1 for r in phase_results.values() if r.get('success', False))}/{len(phase_results)}\n"

        return summary_header + "".join(combined_output)
