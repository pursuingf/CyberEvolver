#!/usr/bin/env python3

import sys
import os

def _get_input_content(arg_content: str = None) -> str:
    """
    Helper to get content either from arguments or from stdin (Heredoc).
    """
    if arg_content is not None:
        return arg_content
    
    # Check if data is being piped or redirected to stdin
    if not sys.stdin.isatty():
        return sys.stdin.read()
    
    return None

def _view_file(file_path: str, start_line: int = 1, end_line: int = None):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        raise IOError(f"Could not read file: {e}")

    total_lines = len(lines)
    if end_line is None or end_line > total_lines:
        end_line = total_lines
    if start_line < 1:
        start_line = 1
    if start_line > end_line:
        return  # nothing to show

    sys.stdout.write(f"<<OPENING FILE: {file_path}>>\n")
    if total_lines == 0:
        sys.stdout.write("<EMPTY FILE>\n")
    else:
        for i in range(start_line - 1, end_line):
            line_text = lines[i]
            sys.stdout.write(f"{i+1:4d} | {line_text}")
            if not line_text.endswith('\n'):
                sys.stdout.write("\n")
    sys.stdout.write(f"<<EOF>>\n")

def cmd_doc() -> str:
    return """
signature: editor <path> <command> [args...]
docstring: An advanced line-based file editor that supports Heredoc for multi-line content.
arguments:
    path(string, required): Target file path.
    command(string, required): 
        - 'view': Read file with line numbers. 
        - 'create': Create or overwrite a file. 
        - 'patch': Replace specific lines. 
        - 'insert': Insert after a specific line. 
usage:
    1. VIEW: Read file content with line numbers.
        - `editor <path> view`: Reads the ENTIRE file.
        - `editor <path> view <start_line>`: Reads from <start_line> to the end of the file.
        - `editor <path> view <start_line> <end_line>`: Reads specific lines (inclusive).
        *Tip: Always use 'view' before 'patch' to confirm line numbers.*

    2. CREATE: Create a new file or OVERWRITE an existing one.
        Command: `editor <path> create <<EOF ... EOF`
        *Note: Automatically creates parent directories if they don't exist.*
        Example (Heredoc):
        editor ./src/main.py create <<EOF
        def main():
            print("Hello")
        EOF

    3. PATCH: Replace a range of lines with new content.
        Command: `editor <path> patch <start_line> <end_line> <<EOF ... EOF`
        *CRITICAL WARNINGS:*
        - **Indentation**: You must provide the EXACT indentation in your content. If the surrounding code uses 4 spaces, your heredoc content must start with 4 spaces.
        - **Replacement**: This completely removes lines from <start> to <end> and puts your content in their place.
        Example (Fixing a bug on lines 10-12):
        editor script.py patch 10 12 <<EOF
            # Indentation matches the function body
            if x > 0:
                return True
        EOF

    4. INSERT: Insert content AFTER a specific line number.
        Command: `editor <path> insert <after_line> <<EOF ... EOF`
        - Use `after_line=0` to insert at the very top of the file.
        Example (Adding imports at top):
        editor script.py insert 0 <<EOF
        import os
        import sys
        EOF
"""

def editor(path: str, command: str, *args) -> None:

    
    file_path = os.path.expanduser(path)

    # --- 1. VIEW ---
    if command == "view":
        start_line = 1
        end_line = None
        if len(args) >= 1: start_line = int(args[0])
        if len(args) >= 2: end_line = int(args[1])
        _view_file(file_path, start_line, end_line)

    # --- 2. CREATE ---
    elif command == "create":
        content = _get_input_content(args[0] if len(args) > 0 else None)
        if content is None:
            raise ValueError("No content provided. Usage: editor <path> create 'content' OR echo 'content' | editor ...")

        directory = os.path.dirname(file_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        sys.stdout.write(f"Successfully created '{file_path}' ({len(content)} chars).\n")
        _view_file(file_path)

    # --- 3. PATCH ---
    elif command == "patch":
        if len(args) < 2:
            raise ValueError("Usage: patch <path> <start_line> <end_line> [content]")

        start_line = int(args[0])
        end_line = int(args[1])
        content = _get_input_content(args[2] if len(args) > 2 else None)

        if content is None:
            raise ValueError("No content provided for patch.")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        total_lines = len(lines)
        if start_line < 1:
            raise ValueError(f"start_line must be ≥ 1, got {start_line}")
        if end_line < start_line:
            raise ValueError(f"end_line ({end_line}) < start_line ({start_line})")
        if start_line > total_lines:
            raise ValueError(f"start_line {start_line} exceeds file length ({total_lines} lines)")
        if end_line > total_lines:
            raise ValueError(f"end_line {end_line} exceeds file length ({total_lines} lines). ")


        # >>> Parse new content robustly
        new_lines = []
        if content.strip() != "":  # non-empty (even if just spaces)
            # Preserve exact line endings: splitlines(True) keeps \n, \r\n etc.
            for line in content.splitlines(True):
                new_lines.append(line)
            # Ensure last line ends with newline (match original file style if possible)
            if new_lines and not new_lines[-1].endswith(('\n', '\r\n')):
                # Guess line ending from original file
                orig_line_end = '\n'
                if lines and lines[-1].endswith('\r\n'):
                    orig_line_end = '\r\n'
                new_lines[-1] += orig_line_end
        # else: content is truly empty → new_lines remains [], meaning "delete"

        old_line_count = end_line - start_line + 1
        new_line_count = len(new_lines)

        # >>> Build final lines
        # lines[0 : start_line-1]  → before patch
        # new_lines                → replacement
        # lines[end_line : ]       → after patch (end_line is 1-based, so 0-based index = end_line)
        final_lines = lines[:start_line-1] + new_lines + lines[end_line:]

        # >>> Write back
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(final_lines)

        # >>> Inform user precisely
        action = "replaced"
        if new_line_count == 0:
            action = "deleted"
        elif old_line_count == 0:
            action = "inserted"  # shouldn't happen here, but safe
        sys.stdout.write(
            f"Successfully {action} lines {start_line}–{end_line} "
            f"({old_line_count} → {new_line_count} lines) in '{file_path}'.\n"
        )

        # >>> Show context: 50 lines before start_line, 50 after the replaced block
        # New content occupies [start_line, start_line + new_line_count - 1]
        block_start = start_line
        block_end = start_line + new_line_count - 1
        view_start = max(1, block_start - 50)
        view_end = block_end + 50

        # Special: if deleted (new_line_count=0), highlight gap
        if new_line_count == 0:
            sys.stdout.write(
                f"<<CONTEXT AROUND DELETION (lines {start_line}–{end_line} REMOVED)>>\n"
            )
        else:
            sys.stdout.write(
                f"<<CONTEXT AROUND PATCH (showing lines {view_start}–{view_end})>>\n"
            )
        _view_file(file_path, view_start, view_end)

    # --- 4. INSERT ---
    elif command == "insert":
        if len(args) < 1:
            raise ValueError("Usage: insert <path> <after_line> [content]")

        after_line = int(args[0])
        content = _get_input_content(args[1] if len(args) > 1 else None)

        if content is None:
            raise ValueError("No content provided for insert.")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        new_lines = [line + '\n' if not line.endswith('\n') else line for line in content.splitlines(True)]
        if not new_lines and content.strip():
            new_lines = [content + '\n']
        if not new_lines and not content.strip():
            new_lines = []

        # Insert AFTER `after_line`, so new content starts at position `after_line + 1`
        final_lines = lines[:after_line] + new_lines + lines[after_line:]

        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(final_lines)

        sys.stdout.write(f"Successfully inserted content after line {after_line} in '{file_path}'.\n")

        insert_start = after_line + 1
        insert_end = after_line + len(new_lines)
        view_start = max(1, after_line - 50 + 1)  # up to 50 lines BEFORE insertion point
        view_end = insert_end + 50                # 50 lines AFTER the inserted block
        sys.stdout.write(f"<<CONTEXT AROUND INSERT (lines {view_start}–{view_end})>>\n")
        _view_file(file_path, view_start, view_end)


    else:
        raise ValueError(f"Unknown command: '{command}'")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: editor <path> <command> [args...]", file=sys.stderr)
        sys.exit(1)

    path_arg = sys.argv[1]
    cmd_arg = sys.argv[2]
    extra_args = sys.argv[3:]

    try:
        editor(path_arg, cmd_arg, *extra_args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)