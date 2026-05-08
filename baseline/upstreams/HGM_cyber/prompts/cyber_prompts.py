# Adapted from HGM's prompts/self_improvement_prompt.py.
# Minimal changes: swap SWE-bench references for cyber security,
# remove fields that don't apply (github_issue, predicted_patch, test_patch).

import json
import os
import random

from hgmlib.common_utils import load_json_file, read_file
from hgmlib.docker_utils import safe_log


coding_agent_summary = """# Cyber Agent Summary

- **Main File**: `cyber_agent.py`
  - Primary Class: `AgenticSystem`
  - The `forward()` function is the central entry point.
  - Prompts are located within the `forward()` function.
- **Tools**: `tools/`
  - The `tools/` directory contains various tools that LLMs can use to perform specific tasks.
  - Each tool must have a `tool_info()` function that returns a JSON object containing 'name', 'description', and 'input_schema'. The 'input_schema' should be a JSON object containing 'type', 'properties', and 'required'.
  - Each tool must have a `tool_function()` function that takes the arguments defined in input_schema, performs the tool's task, and returns a string.
  - See other tools for reference.
- **Utilities**: `utils/`
  - The `utils/` directory contains utility functions used across the codebase.

- **Additional Details**:
  - The agent is a cybersecurity penetration testing agent that exploits vulnerabilities in target services.
  - The agent has access to bash commands and a check_done tool to verify exploit success.
  - The agent is very good at automatically utilizing the right available tools at the right time. So do not have an agentic flow that explicitly forces a tool's usage.
  - Common tools, such as bash commands, are easy for the agent to recognize and use appropriately. However, more complex and niche tools may require explicit instructions in the prompt.
  - Tools should be designed to be as general as possible, ensuring they work across any cyber security challenge. Avoid hardcoding challenge-specific details or behaviors.
  - Do not use 'while True' loops in the agent's code. This can cause the agent to get stuck and not respond.
  - Verify the implementation details of helper functions prior to usage to ensure proper integration and expected behavior.
  - Do not install additional packages or dependencies directly. Update `requirements.txt` if new dependencies are required and install them using `pip install -r requirements.txt`.
\n\n"""


diagnose_system_message = """Here is the implementation of the cyber agent.

# Cyber Agent Implementation
----- Cyber Agent Implementation Start -----
{code}
----- Cyber Agent Implementation End -----

Your task is to identify ONE detailed plan that would improve the agent's cyber security capabilities. The improvement should not be specific to any particular CVE or target service. Focus on general improvements that can enhance the agent's overall penetration testing capabilities.
"""

diagnose_prompt = """
# Agent Running Log
----- Agent Running Log Start -----
{md_log}
----- Agent Running Log End -----

# Challenge Result
The agent's result on the cyber security challenge.
----- Challenge Result Start -----
Solved: {eval_log}
----- Challenge Result End -----

Respond precisely in the following format including the JSON start and end markers:

```json
<JSON>
```

In <JSON>, provide a JSON response with the following fields:
- "log_summarization": Analyze the above logs and summarize how the agent tried to exploit the target. Note which tools and how they are used, the agent's attack approach, and any issues encountered.
- "potential_improvements": Identify potential improvements to the cyber agent that could enhance its penetration testing capabilities. Focus on the agent's general capabilities (e.g., better or new tools usable across any target) rather than challenge-specific fixes.
- "improvement_proposal": Choose ONE high-impact improvement from the identified potential improvements and describe it in detail. This should be a focused and comprehensive plan to enhance the agent's overall capability.
- "implementation_suggestion": Referring to the cyber agent's summary and implementation, think critically about what feature or tool could be added or improved to best implement the proposed improvement. If the proposed feature can be implemented by modifying the existing tools, describe the modifications needed, instead of suggesting a new tool.
- "problem_description": Phrase the improvement proposal and implementation suggestion as a GitHub issue description. It should clearly describe the feature so that a software engineer viewing the issue and the repository can implement it.

Your response will be automatically parsed, so ensure that the string response is precisely in the correct format. Do NOT include the `<JSON>` tag in your output."""

diagnose_prompt_emptypatches = """There are some cyber security challenges where the agent did not produce any meaningful output or never attempted to verify the exploit via check_done. Since the agent is stochastic, it may not always produce results. Handle cases where the agent fails to attempt exploitation. For example, the simplest solution is to ask the agent to try again.

Respond precisely in the following format including the JSON start and end markers:

```json
<JSON>
```

In <JSON>, provide a JSON response with the following fields:
- "potential_improvements": Identify potential improvements to the cyber agent's system. All necessary dependencies and environment setup have already been handled, so do not focus on these aspects.
- "improvement_proposal": Choose ONE high-impact improvement from the identified potential improvements and describe it in detail. This should be a focused and comprehensive plan to enhance the agent's overall capability.
- "implementation_suggestion": Referring to the cyber agent's summary and implementation, think critically about what feature could be added or improved to best implement the proposed improvement.
- "problem_description": Phrase the improvement proposal and implementation suggestion as a GitHub issue description. It should clearly describe the feature so that a software engineer viewing the issue and the repository can implement it.

Your response will be automatically parsed, so ensure that the string response is precisely in the correct format. Do NOT include the `<JSON>` tag in your output."""

diagnose_prompt_stochasticity = """Since the cyber agent is stochastic, it may not produce the correct exploit for the given challenge on the first try. Take into account the agent's stochastic nature and provide a solution to handle such cases. For example, one solution could be to ask the agent to try multiple times and select the best approach. Giving previous attempts as context to the agent may also help.

Respond precisely in the following format including the JSON start and end markers:

```json
<JSON>
```

In <JSON>, provide a JSON response with the following fields:
- "potential_improvements": Identify potential improvements to the cyber agent's system. All necessary dependencies and environment setup have already been handled, so do not focus on these aspects.
- "improvement_proposal": Choose ONE high-impact improvement from the identified potential improvements and describe it in detail. This should be a focused and comprehensive plan to enhance the agent's overall capability.
- "implementation_suggestion": Referring to the cyber agent's summary and implementation, think critically about what feature could be added or improved to best implement the proposed improvement.
- "problem_description": Phrase the improvement proposal and implementation suggestion as a GitHub issue description. It should clearly describe the feature so that a software engineer viewing the issue and the repository can implement it.

Your response will be automatically parsed, so ensure that the string response is precisely in the correct format. Do NOT include the `<JSON>` tag in your output."""

diagnose_prompt_contextlength = """While the cyber agent is attempting to solve challenges, it encounters an error due to the input being too long for the requested model. This error is likely due to the context length exceeding the model's maximum input size. Handle cases where the input is too long for the model. The cyber agent is mainly using the file `llm_withtools.py`. LLMs typically have a context window of 200k tokens. Handle context length only if the context window limit is reached and caught as an exception; otherwise, it is okay to leave it as is.

<error_message>
Error in get_response_withtools: Error code: 400 - {'message': 'Input is too long for requested model.'}
</error_message>

Respond precisely in the following format including the JSON start and end markers:

```json
<JSON>
```

In <JSON>, provide a JSON response with the following fields:
- "potential_improvements": Identify potential improvements to the cyber agent's system. All necessary dependencies and environment setup have already been handled, so do not focus on these aspects.
- "improvement_proposal": Choose ONE high-impact improvement from the identified potential improvements and describe it in detail. This should be a focused and comprehensive plan to enhance the agent's overall capability.
- "implementation_suggestion": Referring to the cyber agent's summary and implementation, think critically about what feature could be added or improved to best implement the proposed improvement.
- "problem_description": Phrase the improvement proposal and implementation suggestion as a GitHub issue description. It should clearly describe the feature and details so that a software engineer viewing the issue and the repository can implement it.

Your response will be automatically parsed, so ensure that the string response is precisely in the correct format. Do NOT include the `<JSON>` tag in your output."""

problem_description_prompt = (
    """# To Implement\n\n{implementation_suggestion}\n\n{problem_description}"""
)


def get_problem_description_prompt(response_json):
    return coding_agent_summary + problem_description_prompt.format(
        implementation_suggestion=response_json["implementation_suggestion"],
        problem_description=response_json["problem_description"],
    )


def read_mdlog_file(filepath, filter=True):
    if not filter:
        return read_file(filepath)
    filter_content = [
        "Error in get_response_withtools",
    ]
    filtered_lines = []
    with open(filepath, "r") as f:
        for line in f:
            if not any(line.startswith(fc) for fc in filter_content):
                filtered_lines.append(line.rstrip("\n"))
    return "\n".join(filtered_lines).strip()


def find_selfimprove_eval_logs(entry, out_dir, commit_id="initial", filter=True):
    """Find agent logs and results for a given challenge."""
    predictions_dir = os.path.join(out_dir, commit_id, "predictions")
    if not os.path.exists(predictions_dir):
        return [], [], [], []

    all_preds_folders = [
        f for f in os.listdir(predictions_dir)
        if os.path.isdir(os.path.join(predictions_dir, f))
    ]

    md_logs = []
    predicted_patches = []
    eval_results = []

    for folder in all_preds_folders:
        # Agent transcript
        md_file = os.path.join(predictions_dir, folder, f"{entry}.md")
        if os.path.exists(md_file):
            md_logs.append(read_mdlog_file(md_file, filter=filter))

        # Result JSON
        json_file = os.path.join(predictions_dir, folder, f"{entry}.json")
        if os.path.exists(json_file):
            pred_data = load_json_file(json_file)
            predicted_patches.append("")  # No patches in cyber
            eval_results.append(str(pred_data.get("solved", False)))

    eval_logs = eval_results  # In cyber, eval_log = solved status
    return md_logs, eval_logs, predicted_patches, eval_results


def process_selfimprove_eval_logs(md_logs, eval_logs, predicted_patches, eval_results):
    md_log = md_logs[0] if md_logs else "No logs available."
    eval_log = eval_logs[0] if eval_logs else "No results available. Assume the agent failed."
    predicted_patch = ""  # Not used in cyber

    if len(md_log) > 100000:
        md_log = md_log[:100000] + "\n<log clipped>"

    eval_result = eval_results[0] if eval_results else "False"
    return md_log, eval_log, predicted_patch, eval_result


def get_diagnose_prompt_cyber(entry_id, commit, root_dir, out_dir, patch_files=[]):
    """
    Get diagnosis prompt for cyber challenges.
    Adapted from get_diagnose_prompt_swe() — removed github_issue, test_patch, predicted_patch.
    """
    if entry_id == "solve_empty_patches":
        diagnose_prompt_out = diagnose_prompt_emptypatches
    elif entry_id == "solve_stochasticity":
        diagnose_prompt_out = diagnose_prompt_stochasticity
    elif entry_id == "solve_contextlength":
        diagnose_prompt_out = diagnose_prompt_contextlength
    else:
        md_logs, eval_logs, predicted_patches, eval_results = find_selfimprove_eval_logs(
            entry_id, out_dir, commit_id=commit
        )
        md_log, eval_log, predicted_patch, eval_result = process_selfimprove_eval_logs(
            md_logs, eval_logs, predicted_patches, eval_results
        )
        diagnose_prompt_out = diagnose_prompt.format(
            md_log=md_log,
            eval_log=eval_log,
        )

    # Get system prompt with agent code
    code_files = ["cyber_agent.py", "tools/"]
    exclude_files = []
    code_text = get_current_code(
        root_dir, code_files, patch_files=patch_files, exclude_files=exclude_files
    )
    diagnose_system_message_out = coding_agent_summary + diagnose_system_message.format(
        code=code_text
    )

    return diagnose_system_message_out, diagnose_prompt_out


def get_current_code(current_dir, code_files, patch_files=None, exclude_files=None):
    """Same as HGM's get_current_code()."""
    if patch_files is None:
        patch_files = []
    if exclude_files is None:
        exclude_files = []
    exclude_set = set(exclude_files)
    code_text = []

    for file_path in code_files:
        full_path = os.path.join(current_dir, file_path)
        if file_path in exclude_set:
            continue
        if os.path.isfile(full_path):
            rel_path = os.path.relpath(full_path, current_dir)
            if rel_path not in exclude_set:
                code_text.append(f"# {rel_path}")
                code_text.append(read_file(full_path))
        elif os.path.isdir(full_path):
            for root, _, files in os.walk(full_path):
                for f in files:
                    if f.endswith(".py"):
                        file_full_path = os.path.join(root, f)
                        rel_path = os.path.relpath(file_full_path, current_dir)
                        if rel_path not in exclude_set:
                            code_text.append(f"# {rel_path}")
                            code_text.append(read_file(file_full_path))

    for i, patch_file in enumerate(patch_files):
        rel_path = os.path.relpath(patch_file, current_dir)
        if rel_path not in exclude_set:
            code_text.append(f"# Patch {i+1}: {rel_path}")
            code_text.append(read_file(patch_file))

    return "\n".join(code_text)
