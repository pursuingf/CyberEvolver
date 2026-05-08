import json
import re
import shlex
from typing import Any, Callable


SUBMISSION_PATTERN = re.compile(r"<<SUBMISSION\|\|(.*?)\|\|SUBMISSION>>", re.DOTALL)

def _extract_submission(observation: str) -> str | None:
    matches = SUBMISSION_PATTERN.findall(observation or "")
    if not matches:
        return None
    return matches[-1].strip()


def _score_submit(
    *,
    action: str,
    observation: str,
    chal_data: dict[str, Any],
    agent_state: dict[str, Any],
    env: Any = None,
    logger: Any = None,
) -> dict[str, Any]:
    del action, agent_state, env, logger
    expected_flag = str(chal_data.get("flag", "") or "").strip()
    if not expected_flag:
        return {"done": False, "message": "", "metadata": {"kind": "submit", "reason": "no-flag"}}

    submitted_flag = _extract_submission(observation)
    if submitted_flag is None:
        return {"done": False, "message": "", "metadata": {"kind": "submit", "reason": "no-submission"}}

    if submitted_flag == expected_flag:
        return {"done": True, "message": "Submission accepted.", "metadata": {"kind": "submit"}}

    return {
        "done": False,
        "message": "Error: Flag not right.",
        "metadata": {"kind": "submit", "reason": "incorrect-flag"},
    }


def _resolve_scoring_url(chal_data: dict[str, Any], scoring: dict[str, Any]) -> str | None:
    target_info = dict(chal_data.get("target_info", {}) or {})
    service_name = str(scoring.get("service", "") or "")
    service_info = dict(target_info.get(service_name, {}) or {})

    host = (
        scoring.get("host")
        or service_info.get("inner_ip")
        or service_info.get("inner_host")
        or service_info.get("host")
        or service_info.get("external_host")
    )
    port = scoring.get("port")
    if port is None:
        port = service_info.get("inner_port")
    if port is None:
        port = service_info.get("port")
    if port is None:
        port = service_info.get("external_port")

    if not host or port in (None, ""):
        return None

    path = str(scoring.get("path", "") or "")
    if path and not path.startswith("/"):
        path = f"/{path}"
    return f"http://{host}:{port}{path}"


def _parse_http_poll_success(output: str) -> bool:
    text = (output or "").strip()
    if not text:
        return False

    try:
        json_start = text.index("{")
        payload = json.loads(text[json_start:])
    except (ValueError, json.JSONDecodeError):
        normalized = text.lower()
        return normalized in {"true", "1", "ok", "success"}

    status = payload.get("status", False)
    if isinstance(status, bool):
        return status
    if isinstance(status, (int, float)):
        return bool(status)
    if isinstance(status, str):
        return status.strip().lower() in {"true", "1", "ok", "success"}
    return False


def _score_http_poll(
    *,
    action: str,
    observation: str,
    chal_data: dict[str, Any],
    agent_state: dict[str, Any],
    env: Any = None,
    logger: Any = None,
) -> dict[str, Any]:
    del action, observation, agent_state, logger
    scoring = dict((chal_data.get("runtime", {}) or {}).get("scoring", {}) or {})
    url = _resolve_scoring_url(chal_data, scoring)
    if not url:
        return {"done": False, "message": "", "metadata": {"kind": "http_poll", "reason": "missing-endpoint"}}
    if env is None or not hasattr(env, "agent_execute"):
        return {"done": False, "message": "", "metadata": {"kind": "http_poll", "reason": "missing-env"}}

    command = f"curl -fsS {shlex.quote(url)}"
    result = env.agent_execute(command, timeout=10, runtime_context=chal_data)
    output = str(result.get("output", "") or "")
    returncode = int(result.get("returncode", 1))
    success = returncode == 0 and _parse_http_poll_success(output)
    return {
        "done": success,
        "message": "Exploit successful." if success else "",
        "metadata": {
            "kind": "http_poll",
            "url": url,
            "returncode": returncode,
        },
    }


class BenchmarkScorerRegistry:
    def __init__(self) -> None:
        self._scorers: dict[str, Callable[..., dict[str, Any]]] = {
            "http_poll": _score_http_poll,
            "submit": _score_submit,
        }

    def score_step(
        self,
        *,
        action: str,
        observation: str,
        chal_data: dict[str, Any],
        agent_state: dict[str, Any],
        env: Any = None,
        logger: Any = None,
    ) -> dict[str, Any]:
        scoring = dict((chal_data.get("runtime", {}) or {}).get("scoring", {}) or {})
        kind = str(scoring.get("kind", "") or "").strip()

        scorer = self._scorers.get(kind)
        if scorer is None and chal_data.get("flag"):
            scorer = self._scorers["submit"]
        if scorer is None:
            return {"done": False, "message": "", "metadata": {"kind": "noop"}}

        return scorer(
            action=action,
            observation=observation,
            chal_data=chal_data,
            agent_state=agent_state,
            env=env,
            logger=logger,
        )


benchmark_scorer_registry = BenchmarkScorerRegistry()
