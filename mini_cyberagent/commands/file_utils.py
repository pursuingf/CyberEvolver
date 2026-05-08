#!/usr/bin/env python3

import sys
import subprocess
from pathlib import Path

def file_utils(command: str, path: str, file_text: str = None) -> str:
    """
    signature: file_utils <command> [<args>]
    docstring: do some file related operations
    arguments:
        command: The command to execute. 
                Must be one of 'view'(View the entire file or directory listing), 
                'create'(Create a new file with the given file_text), 
                or 'write'(Overwrite an existing file with file_text).
        path: The path to the file or directory.
        file_text: The text to write to the file. Only used for 'create' and 'write' commands.
    """
    try:
        path_obj = Path(path)


        if command == "view":
            # Path must exist
            if not path_obj.exists():
                raise ValueError(f"The path {path} does not exist.")
            if path_obj.is_dir():
                # For directories: list non-hidden files up to 2 levels deep
                try:
                    result = subprocess.run(
                        ["find", str(path_obj), "-maxdepth", "2", "-not", "-path", "*/\\.*"],
                        capture_output=True,
                        text=True,
                    )
                    if result.stderr:
                        return f"Error listing directory: {result.stderr}"
                    return (
                        f"Here's the files and directories up to 2 levels deep in {path_obj}, excluding hidden items:\n"
                        + result.stdout
                    )
                except Exception as e:
                    raise ValueError(f"Failed to list directory: {e}")
            try:
                with open(path_obj, "r") as f:
                    return (
                        f"Here's the content in {path_obj}:\n"
                        + f.read()
                    )
            except Exception as e:
                raise ValueError(f"Failed to read file: {e}")
            
        elif command == "create":
            # Path must not exist
            if path_obj.exists():
                raise ValueError(f"Cannot create new file; {path} already exists.")
            if file_text is None:
                raise ValueError("Missing required `file_text` for 'create' command.")
            try:
                with open(path_obj, "w", encoding="utf-8") as f:
                    f.write(file_text)
            except Exception as e:
                raise ValueError(f"Failed to create file: {e}")
            return f"File created successfully at: {path}"

        elif command == "write":
            # Path must exist and must be a file
            if not path_obj.exists():
                raise ValueError(f"The file {path} does not exist.")
            if path_obj.is_dir():
                raise ValueError(f"{path} is a directory and cannot be edited as a file.")
            try:
                with open(path_obj, "w", encoding="utf-8") as f:
                    f.write(file_text)
            except Exception as e:
                raise ValueError(f"Failed to write to file {path}: {e}")
            return f"File at {path} has been written with new content."
        else:
            raise ValueError(f"Unknown or unsupported command: {command}")

    except Exception as e:
        return f"Error: {str(e)}"


if __name__ == "__main__":
    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print("Usage: file_utils <command> [<args>]")
        sys.exit(1)
    elif len(sys.argv) == 3:
        print(file_utils(sys.argv[1],sys.argv[2]))
    else:
        print(file_utils(sys.argv[1], sys.argv[2],sys.argv[3]))