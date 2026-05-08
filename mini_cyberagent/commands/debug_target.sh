#!/bin/bash
# Tool to manage a stateful debugging session for the target binary
set -e

CMD="$1"
TARGET_BINARY="./share/chal/ezROP"
TMUX_SESSION="ctf_debug"

cmd_doc() {
    cat << EOF
signature: debug_target <start|attach|stop|status>
docstring: Manages a persistent tmux session for debugging the target binary. The session runs the binary, ready for GDB attachment.
arguments:
    command (string, required): The action to perform. 'start' begins the target in a tmux session. 'attach' prints the command to attach GDB to the session. 'stop' kills the session. 'status' checks if the session is running.
examples:
    debug_target start
    debug_target attach  # Output: gdb -p [PID]
    debug_target status
notes: This command requires tmux. The target binary must be at ./share/chal/ezROP.
EOF
}

case "$CMD" in
    "start")
        if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
            echo "Session $TMUX_SESSION already exists. Use 'debug_target attach'."
            exit 1
        fi
        # Start the target in a new tmux session, detached
        tmux new-session -d -s "$TMUX_SESSION" "$TARGET_BINARY"
        echo "Started $TARGET_BINARY in tmux session: $TMUX_SESSION"
        ;;
    "attach")
        if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
            echo "Session $TMUX_SESSION does not exist. Use 'debug_target start' first."
            exit 1
        fi
        # Get the PID of the process in the tmux session's first pane
        PID=$(tmux list-panes -t "$TMUX_SESSION" -F "#{pane_pid}")
        if [ -z "$PID" ]; then
            echo "Could not retrieve PID from tmux session."
            exit 1
        fi
        echo "gdb -p $PID"
        ;;
    "stop")
        tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
        echo "Stopped tmux session: $TMUX_SESSION"
        ;;
    "status")
        if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
            echo "Session $TMUX_SESSION is running."
            PID=$(tmux list-panes -t "$TMUX_SESSION" -F "#{pane_pid}" 2>/dev/null || echo "N/A")
            echo "Process PID: $PID"
        else
            echo "Session $TMUX_SESSION is not running."
        fi
        ;;
    *)
        echo "Invalid command. Use 'start', 'attach', 'stop', or 'status'."
        cmd_doc
        exit 1
        ;;
esac