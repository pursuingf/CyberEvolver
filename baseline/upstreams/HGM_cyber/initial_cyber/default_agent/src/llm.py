# This file is adapted from https://github.com/jennyzzt/dgm.

# Code adapted from https://github.com/SakanaAI/AI-Scientist/blob/main/ai_scientist/llm.py.
import json
import os
import re

import anthropic
import backoff
import openai

MAX_OUTPUT_TOKENS = 4096
AVAILABLE_LLMS = [
    "gpt-5",
    "o4-mini",
    "o3",
    "deepseek/deepseek-chat-v3.1",
    "anthropic/claude-sonnet-4",
]

# ---------------------------------------------------------------------------
# Module-level token accumulator (works inside containers — single process)
# ---------------------------------------------------------------------------
_total_prompt_tokens = 0
_total_completion_tokens = 0


def track_usage(response):
    """Extract and accumulate token usage from an API response."""
    global _total_prompt_tokens, _total_completion_tokens
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    _total_prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
    _total_completion_tokens += getattr(usage, "completion_tokens", 0) or 0


def get_token_usage():
    """Return accumulated token usage."""
    return {"prompt_tokens": _total_prompt_tokens, "completion_tokens": _total_completion_tokens}


def reset_token_usage():
    """Reset accumulated token counters."""
    global _total_prompt_tokens, _total_completion_tokens
    _total_prompt_tokens = 0
    _total_completion_tokens = 0


_MODEL_YML = None


def _load_model_yml_host():
    """Load model.yml on host side. Cached."""
    global _MODEL_YML
    if _MODEL_YML is not None:
        return _MODEL_YML
    _MODEL_YML = {}
    search = [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "configs", "model.yml"),
        os.path.join(os.getcwd(), "configs", "model.yml"),
    ]
    for p in search:
        p = os.path.abspath(p)
        if os.path.exists(p):
            try:
                import yaml
                with open(p) as f:
                    _MODEL_YML = yaml.safe_load(f) or {}
            except Exception:
                pass
            break
    return _MODEL_YML


def create_client(model: str):
    # 1. Check env vars (set inside containers by cyber_harness)
    base_url = os.getenv("OPENAI_BASE_URL", "")
    api_key = os.getenv("OPENAI_API_KEY", "")
    model_name = os.getenv("MODEL_NAME", "")

    if base_url and api_key:
        print(f"Using injected endpoint: base_url={base_url}, model={model_name or model}")
        return openai.OpenAI(base_url=base_url, api_key=api_key), model_name or model

    # 2. Check model.yml (host side — for diagnose/self-improve calls)
    yml = _load_model_yml_host()
    cfg = yml.get(model, {})
    if not cfg:
        # Case-insensitive fallback
        for k, v in yml.items():
            if k.lower() == model.lower():
                cfg = v
                break
    if cfg.get("openai_api_base"):
        actual_model = cfg.get("model", model)
        print(f"Using model.yml: {model} -> {cfg['openai_api_base']}")
        return openai.OpenAI(
            base_url=cfg["openai_api_base"],
            api_key=cfg.get("openai_api_key", "dummy"),
        ), actual_model

    # 3. Fall back to original routing
    if "gpt" in model or model.startswith("o"):
        print(f"Using OpenAI API with model {model}.")
        return openai.OpenAI(), model
    elif "vllm" in model.lower():
        print(f"Using vllm API with model {model}.")
        return (
            openai.OpenAI(base_url=f"http://{model[11:]}:8000/v1", api_key="dummy"),
            model,
        )
    else:
        print(f"Using OpenRouter API with model {model}.")
        return (
            openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=os.getenv("OpenRouter_API_KEY"),
            ),
            model,
        )


@backoff.on_exception(
    backoff.expo,
    (
        openai.RateLimitError,
        openai.APITimeoutError,
        anthropic.RateLimitError,
        anthropic.APIStatusError,
    ),
    max_time=120,
)
def get_json_response_from_llm(
    msg,
    client,
    model,
    system_message,
):
    new_msg_history = [{"role": "user", "content": msg}]
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_message},
            *new_msg_history,
        ],
        n=1,
        stop=None,
        seed=0,
        response_format={
            "type": "json_object",
        },
    )
    track_usage(response)
    content = response.choices[0].message.content
    import json

    content_json = json.loads(content)
    new_msg_history = new_msg_history + [{"role": "assistant", "content": content}]

    return content_json, new_msg_history


def get_response_from_llm(
    msg,
    client,
    model,
    system_message,
    print_debug=False,
    msg_history=None,
    temperature=0.7,
):
    if msg_history is None:
        msg_history = []

    if model.startswith("o"):
        new_msg_history = msg_history + [
            {"role": "user", "content": system_message + msg}
        ]
        response = client.chat.completions.create(
            model=model,
            messages=new_msg_history,
            temperature=1,
            n=1,
            seed=0,
        )
        track_usage(response)
        content = response.choices[0].message.content
        new_msg_history = new_msg_history + [{"role": "assistant", "content": content}]
    elif "gpt" in model.lower():
        new_msg_history = msg_history + [{"role": "user", "content": msg}]
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                *new_msg_history,
            ],
            n=1,
            stop=None,
            seed=0,
        )
        track_usage(response)
        content = response.choices[0].message.content
        new_msg_history = new_msg_history + [{"role": "assistant", "content": content}]
    else:
        new_msg_history = msg_history + [{"role": "user", "content": msg}]
        response = client.chat.completions.create(
            model=client.models.list().data[0].id,
            messages=[
                {"role": "system", "content": system_message},
                *new_msg_history,
            ],
            temperature=temperature,
            max_tokens=MAX_OUTPUT_TOKENS,
            n=1,
            stop=None,
        )
        track_usage(response)
        content = response.choices[0].message.content
        new_msg_history = new_msg_history + [{"role": "assistant", "content": content}]
    if print_debug:
        print()
        print("*" * 20 + " LLM START " + "*" * 20)
        print(f'User: {new_msg_history[-2]["content"]}')
        print(f'Assistant: {new_msg_history[-1]["content"]}')
        print("*" * 21 + " LLM END " + "*" * 21)
        print()
    return content, new_msg_history


def extract_json_between_markers(llm_output):
    inside_json_block = False
    json_lines = []

    # Split the output into lines and iterate
    for line in llm_output.split("\n"):
        striped_line = line.strip()

        # Check for start of JSON code block
        if striped_line.startswith("```json"):
            inside_json_block = True
            continue

        # Check for end of code block
        if inside_json_block and striped_line.startswith("```"):
            # We've reached the closing triple backticks.
            inside_json_block = False
            break

        # If we're inside the JSON block, collect the lines
        if inside_json_block:
            json_lines.append(line)

    # If we never found a JSON code block, fallback to any JSON-like content
    if not json_lines:
        # Fallback: Try a regex that finds any JSON-like object in the text
        fallback_pattern = r"\{.*?\}"
        matches = re.findall(fallback_pattern, llm_output, re.DOTALL)
        for candidate in matches:
            candidate = candidate.strip()
            if candidate:
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # Attempt to clean control characters and re-try
                    candidate_clean = re.sub(r"[\x00-\x1F\x7F]", "", candidate)
                    try:
                        return json.loads(candidate_clean)
                    except json.JSONDecodeError:
                        continue
        return None

    # Join all lines in the JSON block into a single string
    json_string = "\n".join(json_lines).strip()

    # Try to parse the collected JSON lines
    try:
        return json.loads(json_string)
    except json.JSONDecodeError:
        # Attempt to remove invalid control characters and re-parse
        json_string_clean = re.sub(r"[\x00-\x1F\x7F]", "", json_string)
        try:
            return json.loads(json_string_clean)
        except json.JSONDecodeError:
            return None
