# Wait PIDs Then Start Single-PID Design

## Goal
Change `scripts/wait_pids_then_start.sh` from a two-PID interface to a single-PID interface so it can wait for one specific process to exit and then launch an arbitrary follow-up command.

## Scope
This change only updates the shell script interface and behavior for waiting on one PID. It does not add process-name matching, restart tracking, timeout support, or multi-PID waiting.

## Design
- Accept exactly one leading PID argument.
- Treat every remaining CLI argument as the command to execute after that PID exits.
- Preserve long commands and arbitrary argument lists by storing the remaining arguments as `CMD=( "$@" )` and executing them with `exec "${CMD[@]}"`.
- Keep the existing polling model based on `kill -0 "$pid"` once per second.
- Update the usage and error messages to describe the new contract: `<pid> <command> [args...]`.

## Verification
- Add a focused regression test that starts a short-lived process, runs the script against its PID, and verifies the follow-up command executes.
- Verify the regression test uses a long argument value so we exercise argument forwarding instead of only a trivial command.
- Re-run the focused test after the script change.
