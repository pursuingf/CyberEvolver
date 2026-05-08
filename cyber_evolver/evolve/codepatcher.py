import re
from pathlib import Path
import textwrap
from dataclasses import dataclass
from typing import List
import logging
import autopep8
import ast
import html
import shutil

def auto_fix_indentation(code_str):
    try:
        if '\t' in code_str:
            code_str = code_str.replace('\t', '    ')  
        return code_str  
        check_version = textwrap.dedent(code_str)
        try:
            ast.parse(check_version)
            return code_str
        except (IndentationError, TabError) as e:
            print(e)
            fixed_code = autopep8.fix_code(code_str, options={'aggressive': 1})
            return fixed_code
    except Exception as e:
        print(f"autopep8 failed: {e}")
        return code_str

@dataclass(frozen=True)
class PatchAction:
    kind: str  # replace_code | create_file | delete_file
    start: int
    path: str
    search: str | None = None
    replace: str | None = None
    content: str | None = None


def parse_action_blocks(plan: str) -> List[PatchAction]:
    """
    Parse all action blocks from the LLM plan (Action-Based XML format).

    Returns a list of PatchAction, ordered by appearance.
    """
    def _strip_outer_markdown_fence(text: str) -> str:
        """
        If `text` is a single fenced Markdown code block like:
          ```python
          ...
          ```
        unwrap it (remove the fence lines) while preserving the inner content.
        """
        if not text:
            return text

        lines = text.splitlines(keepends=True)
        first_nonempty = None
        last_nonempty = None
        for i, ln in enumerate(lines):
            if ln.strip():
                first_nonempty = i
                break
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip():
                last_nonempty = i
                break

        if first_nonempty is None or last_nonempty is None or first_nonempty >= last_nonempty:
            return text

        first = lines[first_nonempty].strip()
        if not (first.startswith("```") or first.startswith("~~~")):
            return text

        fence = first[:3]
        last = lines[last_nonempty].strip()
        if not last.startswith(fence):
            return text

        # Remove the outer fence lines; keep everything else unchanged.
        return "".join(lines[first_nonempty + 1 : last_nonempty])

    def _clean_block(text: str) -> str:
        if not text:
            return ""
        if text.startswith("\n"):
            text = text[1:]
        text = text.rstrip()
        return textwrap.dedent(text)

    plan = re.sub(r"<rationale>[\s\S]*?</rationale>", "", plan or "", flags=re.IGNORECASE)

    actions: List[PatchAction] = []

    replace_pattern = re.compile(
        r'<replace_code\s+path=["\']([^"\'>\n\r]+)["\']\s*>'
        r"[\s\S]*?<search>(.*?)</search>\s*"
        r"<replace>(.*?)</replace>\s*"
        r"</replace_code>",
        re.DOTALL | re.IGNORECASE,
    )
    for m in replace_pattern.finditer(plan):
        actions.append(
            PatchAction(
                kind="replace_code",
                start=m.start(),
                path=m.group(1).strip(),
                search=_strip_outer_markdown_fence(html.unescape(m.group(2))),
                replace=_strip_outer_markdown_fence(
                    auto_fix_indentation(html.unescape(m.group(3)))
                ),
            )
        )

    create_pattern = re.compile(
        r'<create_file\s+path=["\']([^"\'>\n\r]+)["\']\s*>(.*?)</create_file>',
        re.DOTALL | re.IGNORECASE,
    )
    for m in create_pattern.finditer(plan):
        path = m.group(1).strip()
        body = m.group(2)

        content_match = re.search(r"<content>(.*?)</content>", body, re.DOTALL | re.IGNORECASE)
        raw_content = content_match.group(1) if content_match else body

        actions.append(
            PatchAction(
                kind="create_file",
                start=m.start(),
                path=path,
                content=_strip_outer_markdown_fence(_clean_block(html.unescape(raw_content))),
            )
        )

    delete_pattern = re.compile(
        r'<delete_file\s+path=["\']([^"\'>\n\r]+)["\']\s*(?:/?>\s*|>\s*</delete_file>)',
        re.DOTALL | re.IGNORECASE,
    )
    for m in delete_pattern.finditer(plan):
        actions.append(PatchAction(kind="delete_file", start=m.start(), path=m.group(1).strip()))

    actions.sort(key=lambda a: a.start)
    return actions
       
class CodePatcher:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    def apply_patches(self, src_root: Path, plan: str, log_path: Path = None) -> str:
        """
        Apply patches based on the new Action-Based XML format:
        1. <replace_code> (Modify/Delete content)
        2. <create_file> (New file)
        3. <delete_file> (Remove file)
        """
        log_entries = []
        summary_entries = []

        # --- Helper: Unified Logger ---
        def _log(msg: str, level="info"):
            log_entries.append(msg)
            if level == "info":
                self.logger.info(msg)
            elif level == "warning":
                self.logger.warning(msg)
            elif level == "error":
                self.logger.error(msg)

        # --- Helper: Block cleaner (for fuzzy matching) ---
        def _clean_block(text: str) -> str:
            if not text:
                return ""
            # Remove the very first newline if it exists (common in XML content)
            if text.startswith("\n"):
                text = text[1:]
            # Remove trailing whitespace
            text = text.rstrip()
            # Dedent to handle indentation inside the XML tags
            return textwrap.dedent(text)

        actions = parse_action_blocks(plan)

        if not actions:
            _log("⚠️ No valid action blocks (<replace_code>, <create_file>, <delete_file>) found.", "warning")
            if log_path:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text("\n".join(log_entries), encoding="utf-8")
            return "No changes"

        _log(f"🏁 Starting Patch Process: Found {len(actions)} actions.")

        # 2. Execute Actions
        for i, action in enumerate(actions, 1):
            rel_path = action.path
            action_type = action.kind
            
            _log(f"\n{'='*40}")
            _log(f"🔧 Action {i}/{len(actions)}: [{action_type.upper()}] {rel_path}")
            _log(f"{'='*40}")

            # 🔒 Safety check
            if ".." in rel_path or rel_path.startswith("/") or "submit.py" in rel_path:
                msg = f"❌ SKIP unsafe path: {rel_path}"
                _log(msg, "error")
                continue

            full_path = src_root / rel_path

            try:
                # === HANDLER: CREATE FILE ===
                if action_type == 'create_file':
                    if full_path.exists():
                        _log(f"❌ File already exists, overwriting: {rel_path}, SKIP")
                        continue
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    content = action.content or ""
                    full_path.write_text(content, encoding="utf-8")
                    
                    _log(f"✅ SUCCESS: Created file ({len(content)} bytes)")
                    summary_entries.append(f"+{rel_path}")

                # === HANDLER: DELETE FILE ===
                elif action_type == 'delete_file':
                    if full_path.exists():
                        if full_path.is_dir():
                            shutil.rmtree(full_path)
                            _log(f"✅ SUCCESS: Deleted directory")
                        else:
                            full_path.unlink()
                            _log(f"✅ SUCCESS: Deleted file")
                        summary_entries.append(f"-{rel_path}")
                    else:
                        _log(f"⚠️ File not found, cannot delete: {rel_path}")

                # === HANDLER: REPLACE CODE ===
                elif action_type == 'replace_code':
                    if not full_path.exists():
                        _log(f"❌ Error: File not found for modification: {rel_path}", "error")
                        continue

                    original_content = full_path.read_text(encoding="utf-8")
                    search_block = action.search or ""
                    replace_block = action.replace or ""
                    # Detect intent: Modification or Code Deletion?
                    is_deletion = not replace_block.strip()
                    op_name = "DELETE CODE" if is_deletion else "MODIFY CODE"
                    
                    _log(f"   • Operation: {op_name}")
                    if is_deletion:
                        _log("   • Replacement block is empty -> Removing target code.")

                    # Strategy 1: Exact Match
                    exact_count = original_content.count(search_block)
                    
                    if exact_count > 1:
                        _log(search_block)
                        _log(f"❌ FAILED: Search block is not unique. Found {exact_count} occurrences.")
                        _log("   • Please provide more context in <search> to disambiguate.")
                        continue
                    
                    if exact_count == 1:
                        # Perform replacement
                        new_content = original_content.replace(search_block, replace_block)
                        full_path.write_text(new_content, encoding="utf-8")
                        _log(f"✅ SUCCESS: Exact Match Applied.\n {replace_block}")
                        summary_entries.append(f"~{rel_path}")
                        continue
                    
                    search_block = _clean_block(search_block)
                    replace_block = _clean_block(replace_block)
                    # Strategy 2: Fuzzy Match
                    _log("❓ Exact match failed (not found). Switching to Fuzzy Strategy...")
                    new_content = self._fuzzy_replace(
                        rel_path, original_content, search_block, replace_block, log_entries
                    )

                    if new_content != original_content:
                        full_path.write_text(new_content, encoding="utf-8")
                        _log(f"✅ SUCCESS: Fuzzy Match Applied.")
                        summary_entries.append(f"~{rel_path}(fuzzy)")
                    else:
                        _log(f"❌ FAILED: Fuzzy match could not locate the block.")
                        # Preview snippet
                        preview = search_block[:80].replace('\n', '\\n') + "..." 
                        _log(f"   • Looked for: '{preview}'")

            except Exception as e:
                msg = f"💥 ERROR processing {rel_path}: {str(e)}"
                _log(msg, "error")

        # Save logs
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("\n".join(log_entries), encoding="utf-8")

        return ", ".join(summary_entries) if summary_entries else "No changes"

    def _fuzzy_replace(self, filename: str, full_text: str, search_block: str, replace_block: str, log_entries: List[str], threshold: float = 0.6) -> str:
        """
        Precise Block Replacement with Detailed Trace Logging.
        Kept largely same as original but optimized for the new cleaner inputs.
        """
        # Internal logger wrapper
        def _flog(msg):
            log_entries.append(msg)

        def _get_fingerprint(line: str) -> str:
            # Remove comments and whitespace for matching
            if '#' in line and filename.endswith('.py'): line = line.split('#', 1)[0]
            return "".join(line.split())

        def _get_indent(line: str) -> int:
            return len(line) - len(line.lstrip())

        _flog(f"🔍 --- Fuzzy Analysis ---")
        
        full_lines = full_text.splitlines(keepends=True)
        search_lines = search_block.splitlines()
        replace_lines = replace_block.splitlines()

        # 1. Build Fingerprints
        search_fingerprints = [fp for l in search_lines if (fp := _get_fingerprint(l))]
        
        if not search_fingerprints:
            _flog("⚠️ SKIP: Search block has no content (whitespace only).")
            return full_text

        first_search_fp = search_fingerprints[0]
        full_lines_fp = [_get_fingerprint(l) for l in full_lines]

        # 2. Find Candidates (Anchoring)
        candidates = [i for i, fp in enumerate(full_lines_fp) if fp == first_search_fp]

        if not candidates:
            _flog(f"❌ Anchor failed: Start of block not found.")
            return full_text

        # 3. Verify Candidates
        best_candidate = -1
        best_score = -1.0
        best_match_end_idx = -1

        for start_idx in candidates:
            match_count = 0
            curr_full_idx = start_idx
            curr_search_idx = 0
            
            # Look ahead to match the sequence
            while curr_full_idx < len(full_lines) and curr_search_idx < len(search_fingerprints):
                src_fp = full_lines_fp[curr_full_idx]
                target_fp = search_fingerprints[curr_search_idx]
                
                # Skip empty lines in source to be lenient
                if not src_fp:
                    curr_full_idx += 1
                    continue
                
                if src_fp == target_fp:
                    match_count += 1
                    curr_search_idx += 1
                    curr_full_idx += 1
                else:
                    # Break on mismatch
                    break
            
            score = match_count / len(search_fingerprints)
            if score > best_score:
                best_score = score
                best_candidate = start_idx
                best_match_end_idx = curr_full_idx

        _flog(f"   • Best Match at Line {best_candidate+1} (Score: {best_score:.2f})")

        if best_score < threshold:
            _flog(f"⚠️ Score too low (<{threshold}). Aborting.")
            return full_text

        # 4. Handle Indentation & Replacement
        source_start_line = full_lines[best_candidate]
        original_indent = _get_indent(source_start_line)
        
        # Calculate relative indentation of the first search line vs the first replace line
        # This helps if the LLM outputted the XML with different base indentation
        first_replace_indent = _get_indent(replace_lines[0]) if replace_lines else 0
        
        final_replace_lines = []
        for line in replace_lines:
            if not line.strip():
                final_replace_lines.append("\n") # Preserve empty lines
                continue
            
            # Current line's indent relative to the replacement block's start
            current_line_indent = _get_indent(line)
            relative_indent = current_line_indent - first_replace_indent
            
            # Apply original source indentation + relative indent
            new_indent_level = max(0, original_indent + relative_indent)
            final_replace_lines.append((" " * new_indent_level) + line.lstrip() + "\n")

        # 5. Stitch it together
        replacement_text = "".join(final_replace_lines)
        
        before = "".join(full_lines[:best_candidate])
        # Note: best_match_end_idx points to the line AFTER the matched block in source
        after = "".join(full_lines[best_match_end_idx:])
        
        return before + replacement_text + after
