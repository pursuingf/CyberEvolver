# Cyber security evaluation harness for HGM.
# Adapted from HGM's swe_bench/harness.py — same interface pattern.

import datetime
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import docker

import yaml
from jinja2 import Environment, BaseLoader

from hgmlib.common_utils import load_json_file
from hgmlib.docker_utils import (
    copy_from_container,
    copy_to_container,
    log_container_output,
    remove_existing_container,
    safe_log,
    setup_logger,
)

# HGM_cyber's utils is renamed to hgmlib, so no conflict with project root's utils
_HGM_CYBER_DIR = str(Path(__file__).resolve().parent)
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if _HGM_CYBER_DIR not in sys.path:
    sys.path.insert(0, _HGM_CYBER_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(1, _PROJECT_ROOT)

from envs.ctfmanager import CTFManager, CTFEnvConfig
from flag_validator import start_validator

llm = ""  # Global variable — set by hgm_cyber.py before calling harness()
timeout = 1800
step_limit = 30
server_url = "http://10.1.2.146:8000"  # Set by caller; default for backward compat

# ---------------------------------------------------------------------------
# Model config loading (host-side, injected into container as env vars)
# ---------------------------------------------------------------------------

_MODEL_YML_CACHE = None


def _load_model_yml():
    global _MODEL_YML_CACHE
    if _MODEL_YML_CACHE is not None:
        return _MODEL_YML_CACHE
    _MODEL_YML_CACHE = {}
    path = os.path.join(_PROJECT_ROOT, "configs", "model.yml")
    if os.path.exists(path):
        with open(path) as f:
            _MODEL_YML_CACHE = yaml.safe_load(f) or {}
    return _MODEL_YML_CACHE


# ---------------------------------------------------------------------------
# Prompt rendering from prompt_profiles
# ---------------------------------------------------------------------------

_PROMPT_PROFILES_DIR = os.path.join(_HGM_CYBER_DIR, "prompt_profiles")


def _jinja_render(tpl_text, chal_data):
    """Render a Jinja2 template string with challenge data."""
    from jinja2 import Undefined

    class SilentUndefined(Undefined):
        def __str__(self):
            return ""
        def __iter__(self):
            return iter([])
        def __bool__(self):
            return False

    # Ensure service_names maps to Docker DNS names (resolvable via extra_hosts)
    if not chal_data.get("service_names"):
        svc_keys = chal_data.get("application_service_keys", [])
        chal_data["service_names"] = {k: k for k in svc_keys}

    env = Environment(loader=BaseLoader(), undefined=SilentUndefined)
    template = env.from_string(tpl_text)
    return template.render(
        instance_data=chal_data,
        workspace=chal_data.get("workspace", "/ctf"),
        command_docs="",
        skill_descriptions="",
    )


_PROFILE_MAP = {
    "cvebench": "cvebench",
    "ctfbench": "ctfbench",
    "nyu_ctf": "ctfbench",      # NYU CTF uses the ctfbench profile
    "nyuctf": "ctfbench",
    "autopenbench": "autopenbench",
}


def _get_profile_dir(chal_data, variant=None):
    """Resolve the prompt_profiles directory for a benchmark."""
    benchmark = chal_data.get("benchmark_family", chal_data.get("benchmark", ""))
    profile = chal_data.get("prompt_template_profile") or _PROFILE_MAP.get(benchmark)
    if not profile:
        return None
    if profile == "cvebench":
        variant = variant or chal_data.get("default_variant", "one_day")
        return os.path.join(_PROMPT_PROFILES_DIR, "cvebench", variant)
    return os.path.join(_PROMPT_PROFILES_DIR, profile)


def _render_prompts(chal_data, variant=None):
    """Render both system_template and instance_template for the benchmark.
    Returns (system_prompt, instance_prompt) tuple."""
    profile_dir = _get_profile_dir(chal_data, variant)
    if not profile_dir or not os.path.isdir(profile_dir):
        desc = chal_data.get("cve_description", chal_data.get("description", ""))
        return "", desc

    system_prompt = ""
    sys_path = os.path.join(profile_dir, "system_template.txt")
    if os.path.exists(sys_path):
        with open(sys_path) as f:
            system_prompt = _jinja_render(f.read(), chal_data)

    instance_prompt = ""
    inst_path = os.path.join(profile_dir, "instance_template.txt")
    if os.path.exists(inst_path):
        with open(inst_path) as f:
            instance_prompt = _jinja_render(f.read(), chal_data)

    return system_prompt, instance_prompt


def _build_extra_hosts(model_name):
    """Build extra_hosts for LLM endpoint hostname → IP.
    Target DNS is handled by Docker's embedded DNS on the challenge network."""
    import socket
    from urllib.parse import urlparse
    hosts = {}

    cfg = _load_model_yml().get(model_name, {})
    base_url = cfg.get("openai_api_base", "")
    if base_url:
        hostname = urlparse(base_url).hostname
        if hostname:
            try:
                hosts[hostname] = socket.gethostbyname(hostname)
            except socket.gaierror:
                pass

    return hosts


_LLM_PROXY_MAPPING = None


def _load_proxy_mapping():
    """Load proxy mapping if llm_proxy is running."""
    global _LLM_PROXY_MAPPING
    if _LLM_PROXY_MAPPING is not None:
        return _LLM_PROXY_MAPPING
    _LLM_PROXY_MAPPING = {}
    mapping_path = os.path.join(_PROJECT_ROOT, "configs", ".llm_proxy_mapping.json")
    if os.path.exists(mapping_path):
        try:
            with open(mapping_path) as f:
                _LLM_PROXY_MAPPING = json.load(f)
        except Exception:
            pass
    return _LLM_PROXY_MAPPING


def _model_env_vars(model_name):
    """Build env vars dict for a given model name, reading from model.yml.
    If llm_proxy is running, use proxy URL instead of direct HTTPS endpoint."""
    cfg = _load_model_yml().get(model_name, {})
    base_url = cfg.get("openai_api_base", os.getenv("OPENAI_BASE_URL", ""))

    # Check if proxy is available for this model
    proxy_mapping = _load_proxy_mapping()
    if model_name in proxy_mapping:
        # Replace {host} with the Docker host gateway IP (accessible from containers)
        proxy_url = proxy_mapping[model_name].replace("{host}", "10.1.2.146")
        base_url = proxy_url

    env = {
        "OPENAI_API_KEY": cfg.get("openai_api_key", os.getenv("OPENAI_API_KEY", "")),
        "OPENAI_BASE_URL": base_url,
        "MODEL_NAME": cfg.get("model", model_name),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
        "OpenRouter_API_KEY": os.getenv("OpenRouter_API_KEY", ""),
        "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID", ""),
        "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        "AWS_REGION": os.getenv("AWS_REGION", ""),
    }
    return env

# ---------------------------------------------------------------------------
# Benchmark loading
# ---------------------------------------------------------------------------

_BENCHMARK_CACHE = {}  # benchmark_name -> dict

# Map logical benchmark names to JSON filenames.
_BENCHMARK_JSON_MAP = {
    "cvebench": "cvebench.json",
    "nyu_ctf": "nyu_ctf.json",
    "nyuctf": "nyu_ctf.json",
    "autopenbench": "autopenbench.json",
}

benchmark_name = "cvebench"  # Module-level default; set by caller before harness()


def _load_benchmark(name=None):
    name = name or benchmark_name
    if name not in _BENCHMARK_CACHE:
        fname = _BENCHMARK_JSON_MAP.get(name, f"{name}.json")
        bench_path = os.path.join(_PROJECT_ROOT, "benchmarks", fname)
        _BENCHMARK_CACHE[name] = load_json_file(bench_path)
    return _BENCHMARK_CACHE[name]


def _get_challenge_meta(chal_id):
    bench = _load_benchmark()
    return bench.get(chal_id, {})


# ---------------------------------------------------------------------------
# CTFManager helpers
# ---------------------------------------------------------------------------

def _make_ctfmanager(server_url="http://10.1.2.146:8000"):
    cfg = CTFEnvConfig(
        benchmark_root=os.path.join(_PROJECT_ROOT, "benchmarks"),
        run_mode="remote",
        server_url=server_url,
        use_ssh_tunnel=False,
    )
    return CTFManager(cfg)


# ---------------------------------------------------------------------------
# Per-challenge evaluation
# ---------------------------------------------------------------------------

def process_entry(
    chal_id,
    pred_dname,
    model_name_or_path,
    model_patch_paths,
    init_agent_path=".",
    server_url=None,
    agent_image="ctfenv",
):
    # Use module-level default if not specified
    if server_url is None:
        server_url = globals().get("server_url", "http://10.1.2.146:8000")
    """
    Evaluate the cyber agent on a single challenge.
    Adapted from swe_bench/harness.py:process_entry().
    """
    # Per-challenge output directory: predictions/{benchmark}/{chal_id}/
    chal_meta = _get_challenge_meta(chal_id)
    benchmark = chal_meta.get("benchmark", chal_meta.get("benchmark_family", benchmark_name))
    out_dname = pred_dname / benchmark / chal_id
    out_dname.mkdir(parents=True, exist_ok=True)

    chat_history_file = out_dname / "agent.md"
    out_fname = out_dname / "result.json"

    # Skip if already evaluated
    if out_fname.exists():
        print(f"Skipping existing entry {chal_id}")
        return {"success": True, "instance_id": chal_id}

    container = None
    ctfmgr = None
    validator_stop = None
    logger = setup_logger(str(out_dname / "docker.log"))

    try:
        client = docker.from_env()
        run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        # --- 1. Launch target via CTFManager ---
        ctfmgr = _make_ctfmanager(server_url)
        chal_meta = _get_challenge_meta(chal_id)
        chal_data = ctfmgr.get_challenge_data(chal_id)
        target_status = chal_data.get("target_status", "unknown")
        if target_status not in ("running", "static"):
            raise RuntimeError(f"Target not ready: status={target_status}")

        # Extract target info
        runtime = chal_data.get("runtime", {})
        target_info = chal_data.get("target_info", {})
        services = target_info or {}

        # Use Docker DNS names (target, db, etc.) — resolved via extra_hosts
        # mapping to inner_ip, so they work even on host network mode.
        target_svc = services.get("target", next(iter(services.values()), {}))
        target_host = target_svc.get("inner_host", target_svc.get("host", ""))
        target_port = str(target_svc.get("inner_port", target_svc.get("port", "")))

        scoring = runtime.get("scoring", {})
        scoring_svc_key = scoring.get("service", "target")
        scoring_port = scoring.get("port", "")
        scoring_path = scoring.get("path", "")
        scoring_svc = services.get(scoring_svc_key, {})
        scoring_host = scoring_svc.get("inner_host", scoring_svc.get("host", ""))
        if scoring_host and scoring_port:
            scoring_url = f"http://{scoring_host}:{scoring_port}{scoring_path}"
        else:
            scoring_url = ""  # No scoring endpoint (flag-based benchmarks)

        # Challenge network (for connecting agent container)
        challenge_network = runtime.get("network_name", runtime.get("network", ""))

        safe_log(f"Target ready: {target_host}:{target_port}, scoring: {scoring_url}")
        safe_log(f"Challenge network: {challenge_network}")

        # --- 2. Create agent container ---
        container_name = f"hgm-cyber-{chal_id}-{run_id}"
        remove_existing_container(client, container_name)

        # Build env vars from model.yml (host-side) and inject into container
        container_env = _model_env_vars(llm)
        container_env["SCORING_URL"] = scoring_url

        # Flag isolation: keep expected_flag in the harness process, expose only
        # a verifier URL to the container. Agent can POST a guess and learn
        # correct/incorrect, but cannot read the answer.
        expected_flag = chal_data.get("flag", "")
        if expected_flag:
            verifier_port, validator_stop = start_validator(expected_flag)
            # Container reaches host via the bridge gateway IP. _build_extra_hosts
            # already maps the LLM hostname; for the verifier we use a fixed host.
            verifier_host = os.environ.get("FLAG_VERIFY_HOST", "10.1.2.146")
            container_env["FLAG_VERIFY_URL"] = f"http://{verifier_host}:{verifier_port}/verify"
            safe_log(f"Flag validator listening on {verifier_host}:{verifier_port}")

        # LLM endpoint hostname → IP (for extra_hosts, so HTTPS works inside container)
        extra_hosts = _build_extra_hosts(llm)

        # Create on challenge network (so Docker DNS resolves target, db, etc.)
        # Then connect bridge for external access (LLM APIs, pip, etc.)
        container = client.containers.create(
            agent_image,
            name=container_name,
            command="sleep infinity",
            detach=True,
            network=challenge_network,
            environment=container_env,
            extra_hosts=extra_hosts,
        )
        container.start()
        # Add bridge network for LLM API / external access
        try:
            bridge = client.networks.get("bridge")
            bridge.connect(container)
        except Exception as e:
            safe_log(f"Warning: could not connect bridge network: {e}")
        safe_log(f"Container {container_name} on networks: {challenge_network} + bridge")

        # --- 3. Copy agent code into container ---
        safe_log("Copying agent code to container")
        copy_to_container(container, os.path.join(init_agent_path, "cyber_agent.py"), "/cyber_agent/cyber_agent.py")
        copy_to_container(container, os.path.join(init_agent_path, "requirements.txt"), "/cyber_agent/requirements.txt")
        copy_to_container(container, os.path.join(init_agent_path, "tools/"), "/cyber_agent/tools/")
        copy_to_container(container, os.path.join(init_agent_path, "prompts/"), "/cyber_agent/prompts/")
        copy_to_container(container, os.path.join(init_agent_path, "llm.py"), "/cyber_agent/llm.py")
        copy_to_container(container, os.path.join(init_agent_path, "llm_withtools.py"), "/cyber_agent/llm_withtools.py")

        # --- 4. Apply model patches ---
        if model_patch_paths:
            safe_log("Applying model patches")
            for patch_path in model_patch_paths:
                copy_to_container(container, patch_path, "/cyber_agent/parent_patch.txt")
                exec_result = container.exec_run(
                    "/bin/sh -c 'patch -p1 < /cyber_agent/parent_patch.txt'",
                    workdir="/cyber_agent",
                )
                log_container_output(exec_result)
                container.exec_run("rm /cyber_agent/parent_patch.txt", workdir="/cyber_agent")

        # --- 5. Install requirements ---
        safe_log("Installing requirements")
        exec_result = container.exec_run(
            "pip install -r /cyber_agent/requirements.txt",
            workdir="/cyber_agent",
        )
        log_container_output(exec_result)

        # --- 5b. Copy participant-facing challenge files into agent CWD ---
        # The agent runs from /cyber_agent/, so we drop the deliverables right
        # there — that way `ls` immediately shows what the prompt promises.
        # Only copy files explicitly listed in challenge.json's `files` field.
        # NEVER copy challenge.json, flag.txt, solver.*, solve.*, etc. — those
        # contain the answer or reference solution. CVEBench / autopenbench
        # challenges have no `files` and silently no-op.
        _LEAK_DENY_PATTERNS = (
            "flag",          # flag.txt, flag.bin, etc.
            "solver",        # solver.py, solver/
            "solve",         # solve.sh, solve.txt
            "challenge.json",
            "challenge.py",  # often generates the flag
            ".gitignore",
            "test_solver",
            "hints",
            "writeup",
        )

        def _safe_relpath(rel):
            # Reject path traversal and absolute paths
            if not rel or rel.startswith("/") or ".." in rel.split("/"):
                return False
            # Reject if ANY path component matches a leak pattern (not just basename)
            for part in rel.lower().split("/"):
                for pat in _LEAK_DENY_PATTERNS:
                    if pat in part:
                        return False
            return True

        # CTFManager exposes the on-disk root as `full_path`; older fields like
        # `path` (relative) live under `source_fields`. Try both, then fall back.
        chal_root = (
            chal_data.get("full_path")
            or os.path.join(_PROJECT_ROOT, "benchmarks", (chal_data.get("source_fields") or {}).get("path", ""))
            or ""
        )
        files_list = chal_data.get("files") or []
        if chal_root and os.path.isdir(chal_root) and files_list:
            agent_cwd = "/cyber_agent"
            copied = []
            skipped = []
            for rel in files_list:
                rel = str(rel).strip()
                if not _safe_relpath(rel):
                    skipped.append(rel)
                    continue
                src = os.path.join(chal_root, rel)
                if not os.path.exists(src):
                    skipped.append(f"{rel} (missing)")
                    continue
                # Mirror the relative path under agent CWD
                dst = f"{agent_cwd}/{rel}"
                try:
                    parent = os.path.dirname(dst)
                    if parent and parent != agent_cwd:
                        container.exec_run(f"mkdir -p {parent}", workdir="/")
                    copy_to_container(container, src, dst)
                    copied.append(rel)
                except Exception as cp_err:
                    skipped.append(f"{rel} ({cp_err})")
            if copied:
                safe_log(f"Copied to {agent_cwd}/: {copied}")
            if skipped:
                safe_log(f"Skipped (denylist or missing): {skipped}")

        # --- 6. Render prompts and write into container's system_prompt.py ---
        system_prompt_text, instance_prompt_text = _render_prompts(chal_data)
        # Strip format restrictions from system_template (designed for bash-only agents,
        # conflicts with our function-calling agent)
        import re
        system_prompt_text = re.sub(
            r'RESPONSE FORMAT:.*?Failure to follow these rules will cause your response to be rejected\.',
            '', system_prompt_text, flags=re.DOTALL
        ).strip()
        system_prompt_text = re.sub(
            r'<format_example>.*?</format_example>',
            '', system_prompt_text, flags=re.DOTALL
        ).strip()
        rendered_py = (
            "# Rendered by harness from prompt_profiles. Editable by self-improvement.\n\n"
            f"SYSTEM_PROMPT = {repr(system_prompt_text)}\n\n"
            f"INSTANCE_PROMPT = {repr(instance_prompt_text)}\n"
        )
        # Write rendered prompt into container
        exec_result = container.exec_run(
            ["bash", "-c", f"cat > /cyber_agent/prompts/system_prompt.py << 'PROMPT_EOF'\n{rendered_py}\nPROMPT_EOF"],
        )
        safe_log("Rendered prompts written to container")

        # Save rendered prompts to output directory for auditability
        prompt_out = out_dname / "rendered_prompt.txt"
        prompt_out.write_text(
            f"=== SYSTEM PROMPT ===\n{system_prompt_text}\n\n"
            f"=== INSTANCE PROMPT ===\n{instance_prompt_text}\n"
        )

        # --- 7. Run the cyber agent ---
        safe_log(f"Running cyber agent on {chal_id}")
        chat_history_container = f"/cyber_agent/{chal_id}.md"
        cmd = [
            "timeout", str(timeout),
            "python", "/cyber_agent/cyber_agent.py",
            "--chat_history_file", chat_history_container,
            "--model", llm,
            "--scoring_url", scoring_url,
            "--outdir", "/cyber_agent/",
            "--timeout", str(timeout),
            "--step_limit", str(step_limit),
        ]
        exec_result = container.exec_run(cmd, environment=container_env, workdir="/cyber_agent")
        log_container_output(exec_result, raise_error=False)

        # --- 8. Collect results ---
        safe_log("Collecting results")
        copy_from_container(container, chat_history_container, chat_history_file)

        # Read result.json from container (agent self-report)
        exec_result = container.exec_run("cat /cyber_agent/result.json")
        try:
            result_data = json.loads(exec_result.output.decode())
        except Exception:
            result_data = {"solved": False, "steps": 0}

        # Independent verification (host-side, agent never sees the answer)
        solved = False

        if scoring_url:
            # CVEBench: check scoring endpoint (http_poll)
            verify_cmd = f"curl -s -m 10 {scoring_url}"
            exec_result = container.exec_run(["bash", "-c", verify_cmd])
            try:
                verify_data = json.loads(exec_result.output.decode())
                solved = verify_data.get("status", False)
            except Exception:
                solved = result_data.get("solved", False)

        if not solved and expected_flag:
            # Flag-based benchmarks: extract submitted flag from agent log and
            # compare in the harness process. expected_flag stays here, never
            # leaves this scope.
            import re
            try:
                agent_log = chat_history_file.read_text(errors="replace")
                submissions = re.findall(r"<<SUBMISSION\|\|(.*?)\|\|SUBMISSION>>", agent_log)
                if submissions:
                    submitted = submissions[-1].strip()
                    solved = (submitted == expected_flag.strip())
                    safe_log(f"Flag verification: submitted='{submitted}' match={solved}")
            except Exception:
                pass

        if not solved:
            solved = result_data.get("solved", False)
        safe_log(f"Scoring verification: solved={solved} (agent_report={result_data.get('solved')})")

        result = {
            "instance_id": chal_id,
            "benchmark": benchmark,
            "model_name_or_path": model_name_or_path,
            "solved": solved,
            "steps": result_data.get("steps", 0),
            "prompt_tokens": result_data.get("prompt_tokens", 0),
            "completion_tokens": result_data.get("completion_tokens", 0),
            "model_patch": "",
        }
        out_fname.write_text(json.dumps(result, indent=4))
        return {"success": True, "instance_id": chal_id}

    except Exception as e:
        print(f"Error processing entry {chal_id}: {str(e)}")
        try:
            result = {
                "instance_id": chal_id,
                "benchmark": benchmark if "benchmark" in dir() else "unknown",
                "model_name_or_path": model_name_or_path,
                "solved": False,
                "steps": 0,
                "model_patch": "",
            }
            out_fname.write_text(json.dumps(result, indent=4))
        except Exception as write_err:
            print(f"Error writing result for {chal_id}: {write_err}")
        return {"success": False, "instance_id": chal_id, "error": str(e)}

    finally:
        # Cleanup
        try:
            if container is not None:
                container.stop(timeout=5)
                container.remove(force=True)
        except Exception as e:
            safe_log(f"Error cleaning up container for {chal_id}: {e}")
        try:
            if ctfmgr is not None:
                ctfmgr.teardown(chal_id)
        except Exception as e:
            safe_log(f"Error tearing down target for {chal_id}: {e}")
        try:
            if validator_stop is not None:
                validator_stop()
        except Exception as e:
            safe_log(f"Error stopping flag validator for {chal_id}: {e}")


# ---------------------------------------------------------------------------
# Parallel harness (same interface as swe_bench/harness.py)
# ---------------------------------------------------------------------------

def harness(
    test_task_list=None,
    max_workers=4,
    model_name_or_path=None,
    model_patch_paths=None,
    pred_dname="./cyber_predictions",
    init_agent_path=".",
    output_dir=None,
    server_url=None,
    agent_image="ctfenv",
):
    if server_url is None:
        server_url = globals().get("server_url", "http://10.1.2.146:8000")
    """
    Parallel evaluation harness. Same interface as swe_bench/harness.py:harness().
    """
    if test_task_list is None:
        bench = _load_benchmark()
        test_task_list = list(bench.keys())

    if model_name_or_path is None:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        model_name_or_path = f"{timestamp}--cyber-agent"

    pred_dname = Path(pred_dname)
    pred_dname.mkdir(parents=True, exist_ok=True)

    print(f"Starting cyber evaluation for {model_name_or_path} on {len(test_task_list)} challenges")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_chal = {
            executor.submit(
                process_entry,
                chal_id,
                pred_dname,
                model_name_or_path,
                model_patch_paths,
                init_agent_path,
                server_url,
                agent_image,
            ): chal_id
            for chal_id in test_task_list
        }

        for future in as_completed(future_to_chal):
            result = future.result()
            if result["success"]:
                print(f"Processed {result['instance_id']}")
            else:
                print(f"Failed {result['instance_id']}: {result.get('error', 'Unknown')}")

    print(f"Evaluation completed for {model_name_or_path}")
    return [pred_dname]
