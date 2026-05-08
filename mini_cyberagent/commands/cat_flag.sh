#!/bin/bash

# Primary command: cat_flag <path>
cat_flag() {
    local path="$1"
    [ -f "$path" ] && [ -r "$path" ] && cat "$path" | tr -d '\0' | awk '{$1=$1};1'
}

# ✅ Unified doc interface — same semantic as Python's cmd_doc()
cmd_doc() {
    cat <<'DOC'
signature: cat_flag <path>
docstring: Reads a flag file safely; outputs raw content or nothing.
arguments:
    path (string, required): path to flag file
DOC
}

# CLI dispatch
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    case "${1:-}" in
        --help) cmd_doc; exit 0 ;;
        "") echo "Usage: $0 <path>"; exit 2 ;;
        *) cat_flag "$1";;
    esac
fi