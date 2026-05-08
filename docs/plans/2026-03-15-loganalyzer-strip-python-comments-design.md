# LogAnalyzer Python Comment Stripping Design

**Goal:** Remove full-line Python comments from action code blocks in generated summary markdown, without changing raw logs or non-Python actions.

## Scope

Only change `evolve/loganalyzer.py` at summary rendering time. Do not modify `parse_agent_log`, stored raw actions, or non-summary outputs.

## Behavior

- If an action looks like Python code, remove lines whose stripped form starts with `#`.
- Preserve inline comments like `x = 1  # keep`.
- Preserve shell/bash comments in non-Python actions.
- Apply the cleaning only when writing summary report action sections.

## Approach

Add a small helper in `LogAnalyzer` to detect Python-ish actions conservatively and strip only full-line comments. Use it for both raw and summarized action blocks in `_format_markdown_report`.
