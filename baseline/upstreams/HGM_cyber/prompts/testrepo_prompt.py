# Adapted from HGM's prompts/testrepo_prompt.py.
# Simplified for cyber agent — no SWE-bench eval scripts.


def get_test_description(eval_script="", swerepo=False, polyglot=False):
    # For cyber agent self-improvement, testing is about verifying tools work
    description = "The tests in the repository can be run with the bash command `cd /hgm/ && pytest -rA <specific test files>`. If no specific test files are provided, all tests will be run. The given command-line options must be used EXACTLY as specified. Do not use any other command-line options. ONLY test tools and utils. NEVER try to test or run agentic_system.forward()."
    return description.strip()
