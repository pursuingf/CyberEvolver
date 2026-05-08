#!/usr/bin/env python3
import argparse
import subprocess
import sys
import re
import signal

def main():
    # 1. Flat and Explicit Arguments
    parser = argparse.ArgumentParser(description="Execute command with timeout and regex verification.")
    parser.add_argument("--cmd", required=True, help="Command to run")
    parser.add_argument("--pattern", help="Regex pattern to search in output")
    parser.add_argument("--timeout", type=int, default=10, help="Timeout in seconds")

    args = parser.parse_args()

    # 2. Execution Physics (Low Freedom, High Reliability)
    try:
        # Using subprocess.run is safer than os.system
        result = subprocess.run(
            args.cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=args.timeout
        )

        # 3. Output Sanitization (Unix Philosophy)
        # Stdout is for data, Stderr is for logs
        stdout_clean = result.stdout.strip()
        stderr_clean = result.stderr.strip()

        if stderr_clean:
            print(f"[STDERR]: {stderr_clean}", file=sys.stderr)

        if result.returncode != 0:
            print(f"[FAIL] Command exited with {result.returncode}", file=sys.stderr)
            # We might still want to see stdout even on error
            if stdout_clean: print(stdout_clean)
            sys.exit(result.returncode)

        # 4. Verification Logic (Optional)
        if args.pattern:
            if re.search(args.pattern, stdout_clean):
                print(f"[SUCCESS] Pattern '{args.pattern}' found.", file=sys.stderr)
                print(stdout_clean)
                sys.exit(0)
            else:
                print(f"[FAIL] Pattern '{args.pattern}' NOT found.", file=sys.stderr)
                print(stdout_clean)
                sys.exit(1)
        else:
            # Pass-through mode
            print(stdout_clean)
            sys.exit(0)

    except subprocess.TimeoutExpired:
        print(f"[TIMEOUT] Execution exceeded {args.timeout}s", file=sys.stderr)
        sys.exit(124) # Standard timeout exit code
    except Exception as e:
        print(f"[ERROR] internal error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()