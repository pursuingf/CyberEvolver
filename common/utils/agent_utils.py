import re

def output_wrapper(command: str, raw_output: str, max_chars: int = 5000, binary_threshold: float = 0.5) -> str:
    """
    Wrapper for Shell Output: Handles truncation, binary detection, and ANSI cleaning.
    
    Args:
        command (str): The command executed by the mini_cyberagent.
        raw_output (str): The raw stdout/stderr from the shell.
        max_chars (int): Max characters allowed before truncation.
        binary_threshold (float): Ratio of non-printable chars (0.0 - 1.0) to trigger binary protection.
    
    Returns:
        str: Cleaned, safe, and potentially truncated output for the LLM.
    """
    
    # 1. Preprocess by stripping ANSI color codes.
    # Many tools, such as ls --color and grep --color, emit escape sequences.
    # They are valid terminal output but usually waste LLM context.
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    clean_text = ansi_escape.sub('', raw_output)
    
    # 2. Check whether output is empty.
    if not clean_text:
        # If raw output existed but was removed, it was only control/color characters.
        if raw_output: 
             return "[System]: Output contained only control characters/colors and was cleaned."
        return "[System]: Command executed successfully with no output."
    hint = ''' <Information>
Control characters/colors contained in the output have been cleaned to save tokens and protect context window.
</Information> \n''' 

    # 3. Binary or garbled-output detection.
    # Count non-printable characters while allowing common whitespace.
    # The first 1000 characters are enough; large output does not need a full scan.
    sample_segment = clean_text[:1000]
    
    # Allowed whitespace: newline, carriage return, tab, and form feed.
    # isprintable() accepts many unicode characters but rejects binary control bytes.
    printable_chars = sum(1 for c in sample_segment if c.isprintable() or c in '\n\r\t\x0c')
    total_sampled = len(sample_segment)
    
    if total_sampled > 0:
        non_printable_ratio = (total_sampled - printable_chars) / total_sampled
        
        # Hide output if the garbled-character ratio exceeds the configured threshold.
        if non_printable_ratio > binary_threshold:
            return (
                f"--- [SYSTEM ALERT: BINARY OUTPUT DETECTED] ---\n"
                f"Command: {command}\n"
                f"Status: Output hidden to protect context window.\n"
                f"Reason: High ratio of non-printable characters ({non_printable_ratio:.1%} detected).\n"
                f"Analysis: You likely tried to cat/print a binary file (e.g., ELF, image, compiled code).\n"
                f"Action Suggested: \n"
                f"  1. Use 'file {command.split()[-1] if command.split() else 'filename'}' to check file type.\n"
                f"  2. Use 'strings' to extract readable text.\n"
                f"  3. Use 'xxd' or 'hexdump' to view hex representation.\n"
                f"  4. DO NOT run this command again without filters."
            )

    # 4. Truncation logic.
    original_len = len(clean_text)
    
    if original_len <= max_chars:
        return hint + clean_text

    # Truncate long output.
    truncated_content = clean_text[:max_chars]
    total_lines = clean_text.count('\n') + 1
    shown_lines = truncated_content.count('\n') + 1
    hidden_lines = total_lines - shown_lines

    # 5. Generate a context-aware suggestion.
    cmd_lower = command.lower()
    suggestion = "Use 'grep', 'head', 'tail' to filter specific information."
    
    if "objdump" in cmd_lower and "grep" not in cmd_lower:
        suggestion = "Output is massive assembly. Use 'grep' to find specific functions (e.g., 'grep main')."
    elif "base64" in cmd_lower and "grep" not in cmd_lower:
        suggestion = "Base64 block too large. Decode to a file: 'base64 -d > output_file'."

    wrapper_msg = (
        f"{hint + truncated_content}\n"
        f"\n"
        f"--- [SYSTEM WARNING: OUTPUT TRUNCATED] ---\n"
        f"Original Size: {original_len} chars ({total_lines} lines). Shown: First {max_chars} chars.\n"
        f"Hidden Content: ~{hidden_lines} lines were removed to save context.\n"
        f"NOTE: This is NOT an error in your command, but a limitation of the display.\n"
        f"ACTION: {suggestion}"
    )
    
    return wrapper_msg
