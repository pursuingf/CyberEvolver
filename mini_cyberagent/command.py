import ast
import textwrap
import re
import subprocess
import tempfile
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import importlib.util
import uuid
@dataclass
class CommandMetadata:
    name: str
    signature: str
    raw_docstring: Optional[str]
    summary: str

class Command:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"Command file not found: {file_path}")
        
        with open(self.file_path, "r", encoding="utf-8") as f:
            self.content = f.read()
        
        self.content = self._ensure_shebang(self.content, self.file_path.suffix.lower())
        self.name = self.file_path.stem
        self.metadata = self._extract_metadata()

    def _ensure_shebang(self, content: str, suffix: str) -> str:
        """Ensure content starts with appropriate shebang. Modify in-memory only."""
        lines = content.splitlines()
        if lines and lines[0].startswith('#!'):
            return content  # already has shebang

        shebang = {
            '.py': '#!/usr/bin/env python3',
            '.sh': '#!/usr/bin/env bash'
        }.get(suffix)

        if shebang:
            return shebang + '\n' + content
        return content

    # ======================
    # 🔒 Safe Dynamic Execution
    # ======================

    def _run_cmd_doc_python(self) -> Optional[str]:
        """Safely call cmd_doc() in Python with timeout + restricted builtins."""
        try:
            # Create safe module
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(self.content)
                f.flush()
                
                spec = importlib.util.spec_from_file_location("_cmd", f.name)
                module = importlib.util.module_from_spec(spec)
                
                # Restrict builtins
                safe_builtins = {
                    k: v for k, v in __builtins__.items()
                    if k in {'len', 'str', 'int', 'float', 'bool', 'type', 'isinstance', 'hasattr', 'getattr', 'print'}
                }
                safe_builtins.update({
                    '__import__': lambda name, *a, **kw: __import__(name) if name in {'os', 'sys', 'json', 'time'} else None,
                })
                module.__builtins__ = safe_builtins

                # Set timeout
                def timeout_handler(signum, frame):
                    raise TimeoutError("cmd_doc() timeout")

                old_handler = signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(1)  # 1 second max
                try:
                    spec.loader.exec_module(module)
                    if hasattr(module, 'cmd_doc') and callable(module.cmd_doc):
                        result = module.cmd_doc()
                        if isinstance(result, str):
                            return result
                finally:
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, old_handler)
                    Path(f.name).unlink(missing_ok=True)
        except Exception:
            pass
        return None

    def _run_cmd_doc_bash(self) -> Optional[str]:
        """Safely call cmd_doc in Bash with timeout + isolation."""
        marker = f"__CMD_DOC_MARKER_{uuid.uuid4().hex}__"
        bash_cmd = (
            f"set +e; "
            f"function exit() {{ :; }}; "
            f"function exec() {{ :; }}; "
            f"source /dev/stdin >/dev/null 2>&1; "
            f"echo '{marker}'; "
            f"type cmd_doc >/dev/null 2>&1 && cmd_doc"
        )
        try:
            result = subprocess.run(
                ['timeout', '1', 'bash', '--noprofile', '--norc', '-c', bash_cmd],
                input=self.content.encode('utf-8'),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, 
                check=False
            )
            out = result.stdout.decode('utf-8', errors='ignore').rstrip()
            phrased_doc = out.split(marker, 1)[1].strip()
            return phrased_doc if phrased_doc.strip() else None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, UnicodeDecodeError):
            return None

    # ======================
    # 📜 Static Fallback (if dynamic fails)
    # ======================

    def _extract_cmd_doc_from_ast(self, tree: ast.AST) -> Optional[str]:
        cmd_doc_func = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "cmd_doc"),
            None
        )
        if not cmd_doc_func or len(cmd_doc_func.body) != 1:
            return None
        stmt = cmd_doc_func.body[0]
        if not isinstance(stmt, ast.Return) or stmt.value is None:
            return None
        if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
            return stmt.value.value
        if isinstance(stmt.value, ast.Str):  # Py <3.8
            return stmt.value.s
        return None

    def _extract_doc_from_bash_static(self, content: str) -> Optional[str]:
        # Try: cmd_doc() { cat <<'DOC' ... DOC; }
        m = re.search(
            r"^\s*cmd_doc\s*\(\)\s*\{[^}]*?cat\s+<<([\"']?)(\w+)\1\s*$(.*?)^\2\s*;",
            content, re.MULTILINE | re.DOTALL
        )
        return m.group(3).rstrip() if m else None


    def _extract_metadata(self) -> CommandMetadata:
        suffix = self.file_path.suffix.lower()
        raw_doc = None

        # ✅ Step 1: Try dynamic execution
        if suffix == ".py":
            raw_doc = self._run_cmd_doc_python()
        elif suffix == ".sh":
            raw_doc = self._run_cmd_doc_bash()

        # 🔁 Step 2: Fallback to static if needed
        if raw_doc is None:
            if suffix == ".py":
                try:
                    tree = ast.parse(self.content)
                    raw_doc = self._extract_cmd_doc_from_ast(tree)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    pass
            elif suffix == ".sh":
                raw_doc = self._extract_doc_from_bash_static(self.content)

        # Build signature
        signature = f"{self.name} [...]"
        if suffix == ".py":
            try:
                tree = ast.parse(self.content)
                target_func = next(
                    (n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef)
                     and not n.name.startswith('_')
                     and n.name == self.name),
                    None
                )
                if target_func:
                    args = [arg.arg for arg in target_func.args.args]
                    if target_func.args.vararg:
                        args.append(f"*{target_func.args.vararg.arg}")
                    if target_func.args.kwarg:
                        args.append(f"**{target_func.args.kwarg.arg}")
                    signature = f"{target_func.name}({', '.join(args)})"
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass

        summary = self._make_summary(raw_doc)
        return CommandMetadata(
            name=self.name,
            signature=signature,
            raw_docstring=raw_doc,
            summary=summary
        )

    def _make_summary(self, raw_doc: Optional[str]) -> str:
        if not raw_doc or not raw_doc.strip():
            return "(no description)"
        lines = raw_doc.splitlines()
        para = []
        for line in lines:
            s = line.strip()
            if s == "":
                break
            para.append(s)
        return " ".join(para) if para else "(empty docstring)"

    def get_prompt_info(self) -> str:
        doc = self.metadata.raw_docstring
        if doc and doc.strip():
            doc = doc if doc[0]=='\n' else '\n'+ doc
            indented = textwrap.indent(doc.rstrip(), '  ')
            return f"{self.name}:{indented}\n"
        else:
            return f"{self.name}:\n  {self.metadata.signature}\n  {self.metadata.summary}\n"


if __name__ == "__main__":
    import sys
    import traceback
    from pathlib import Path

    cmd_dir = Path("./mini_cyberagent/commands")
    cmd_files = [
        f for f in cmd_dir.glob("*")
        if f.is_file()
        and f.suffix.lower() in {".py", ".sh"}
        and f.name not in {"__init__.py", Path(__file__).name}
        and not f.name.startswith("test_")
        and f.stem != "_"
    ]

    if not cmd_files:
        print("⚠️  No .py or .sh command files found.", file=sys.stderr)
        sys.exit(0)

    print(f"🔍 Testing {len(cmd_files)} tools (dynamic + static)...\n")
    success = 0
    for fp in sorted(cmd_files):
        try:
            cmd = Command(str(fp))
            print(f"✅ {fp.name}")
            print(cmd.get_prompt_info().rstrip())
            print("-" * 40)
            success += 1
        except Exception as e:
            print(f"❌ {fp.name} → {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            print("-" * 40)

    print(f"\n📊 {success}/{len(cmd_files)} OK")
    sys.exit(0 if success == len(cmd_files) else 1)
