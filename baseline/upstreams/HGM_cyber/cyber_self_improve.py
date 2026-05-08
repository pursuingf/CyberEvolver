# Adapted from HGM's self_improve_step.py.
# Minimal changes: use cyber prompts instead of SWE prompts.

import json
import os

from llm import create_client, extract_json_between_markers, get_response_from_llm, get_token_usage, reset_token_usage
from prompts.cyber_prompts import get_diagnose_prompt_cyber, get_problem_description_prompt
from hgmlib.docker_utils import safe_log

dataset = None
diagnose_llm = ""
self_improve_llm = ""
timeout = 3600
n_evals = 0


def diagnose_problem(
    entry, commit, root_dir, out_dir, patch_files=[], max_attempts=2, polyglot=False
):
    reset_token_usage()  # Reset before diagnose call
    client = create_client(diagnose_llm)
    diagnose_sys_message, diagnose_prompt = get_diagnose_prompt_cyber(
        entry,
        commit,
        root_dir,
        out_dir,
        patch_files=patch_files,
    )
    try:
        try:
            response, msg_history = get_response_from_llm(
                msg=diagnose_prompt,
                client=client[0],
                model=client[1],
                system_message=diagnose_sys_message,
                print_debug=False,
                msg_history=None,
            )
        except Exception as e:
            safe_log(f"Error with get_response_from_llm: {e}")
        response_json = extract_json_between_markers(response)
        assert response_json, "empty response json"
        problem_statement = get_problem_description_prompt(response_json)
    except Exception as e:
        safe_log(f"Error while diagnosing the problem: {e}")
        if max_attempts > 0:
            return diagnose_problem(
                entry,
                commit,
                root_dir,
                out_dir,
                patch_files=patch_files,
                max_attempts=max_attempts - 1,
                polyglot=polyglot,
            )
        else:
            return None
    return problem_statement


def save_metadata(metadata, output_dir):
    metadata_file = os.path.join(output_dir, "metadata.json")
    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=4)
