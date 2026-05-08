# Cybersec Arena Operator Handbook

This handbook is for users who need to run real challenge targets, inspect target startup, consume challenge data through `ChallengeClient`, and add a new benchmark in the current repository layout.

`ChallengeClient` currently supports remote mode only. `local` mode is intentionally left unimplemented and raises `NotImplementedError`, so the normal workflow is:

1. Start a challenge server.
2. Let `ChallengeClient` call that server over HTTP.
3. Use the returned challenge data and target runtime metadata in the agent or evaluator.

## 1. Environment Checks

Run commands from the repository root:

```bash
cd <repo-root>
export PYTHONPATH=.
```

Check Docker:

```bash
docker info
docker compose version
```

Check local benchmark indexes and fixture directories:

```bash
PYTHONPATH=. python -m mini_cyberagent.cli benchmarks
```

If a benchmark layout is reported as `missing`, the repository has the `<benchmark>.json` index but not the heavy fixture directory. Place the fixture tree at:

```text
bench_hub/benchmarks/<benchmark-name>/
```

## 2. Start The Challenge Server

`ChallengeClient` calls the challenge server endpoint `/launch/{challenge_id}`. This is the recommended server for agent and evolution runs:

```bash
export CTF_HOST_IP=127.0.0.1
PYTHONPATH=. python bench_hub/server/challenge_server.py 127.0.0.1 8000
```

If the agent runs on another host or inside a container, set `CTF_HOST_IP` to an address that the agent can reach:

```bash
export CTF_HOST_IP=<agent-visible-server-ip>
PYTHONPATH=. python bench_hub/server/challenge_server.py 0.0.0.0 8000
```

The CLI wrapper is equivalent:

```bash
mini-cyber serve --host 127.0.0.1 --port 8000
```

Check that the server is reachable:

```bash
curl -s http://127.0.0.1:8000/docs >/dev/null && echo "challenge server ok"
```

## 3. Verify Target Startup

Launch a challenge directly through the server:

```bash
curl -s "http://127.0.0.1:8000/launch/ic-crypto-5?force_recreate=true" | python -m json.tool
```

Static challenges return a `static` status:

```json
{
  "status": "static",
  "chal_id": "..."
}
```

Dynamic challenges return `launched`, `reused`, or `recreated`, plus service metadata:

```json
{
  "status": "launched",
  "chal_id": "...",
  "project_name": "...",
  "services": [
    {
      "service_name": "web",
      "external_host": "127.0.0.1",
      "external_port": 12345
    }
  ]
}
```

Use this checklist for dynamic targets:

- `status` is `launched`, `reused`, or `recreated`.
- `services` is not empty.
- Each target service exposes `external_host` and `external_port`, or normalized `host` and `port`.
- The matching Docker compose project has running containers.

Stop a challenge:

```bash
curl -X DELETE "http://127.0.0.1:8000/launch/<challenge-id>"
```

If launch returns HTTP 500, inspect the server terminal logs first. Common causes are missing images, invalid compose paths, readiness timeouts, or missing fixture directories.

## 4. Optional Target Runtime Server Check

`target_runtime_server.py` is a standalone runtime manager. It also exposes `/launch/{target_id}`, but it supports namespaces and is best used for low-level target runtime checks.

Start it:

```bash
export CTF_HOST_IP=127.0.0.1
PYTHONPATH=. python bench_hub/server/target_runtime_server.py 127.0.0.1 8000
```

Launch a target:

```bash
curl -s "http://127.0.0.1:8000/launch/<target-id>?namespace=handbook-test&force_recreate=true" | python -m json.tool
```

Stop a target:

```bash
curl -X DELETE "http://127.0.0.1:8000/launch/<target-id>?namespace=handbook-test"
```

For normal agent and evolution runs, prefer `challenge_server.py` because it matches `ChallengeClient` behavior for challenge metadata, `target_scope`, `parallel_mode`, and `run_id`.

## 5. Use ChallengeClient

Start the challenge server first, then run:

```python
from common.agent_runtime.challenge_client import ChallengeClient, ChallengeClientConfig

client = ChallengeClient(
    ChallengeClientConfig(
        benchmark_root="./bench_hub/benchmarks",
        server_url="http://127.0.0.1:8000",
        use_external_access=True,
    )
)

chal = client.get_challenge_data(
    "ic-crypto-5",
    runtime_args={
        "target_scope": "per_challenge",
    },
)

print(chal["target_status"])
print(chal["target_info"])
print(chal["runtime"])

client.finish_challenge("ic-crypto-5")
client.close()
```

Important returned fields:

- `target_status`: `static`, `running`, or `stopped`.
- `target_info`: target service connection metadata. Dynamic targets usually include `host`, `port`, `url`, and `netcat`.
- `runtime`: runtime metadata, including `run_id`, `project_name`, `network_name`, `network_subnet`, `scoring`, and `debug`.
- `files`: challenge attachments.
- `flag_format`: inferred flag format, such as `picoCTF{...}`.

If the agent runs inside a container and connects to ports on the host, make sure `CTF_HOST_IP` and `ChallengeClientConfig.host_ip_for_agent` are reachable from that container. For SSH jump-host access, set `use_ssh_tunnel=True` and configure the `CHALLENGE_SSH_*` environment variables.

## 6. Model Configuration

`ChallengeClient` does not load LLM models. It only discovers challenge metadata, starts targets, and returns runtime information.

LLM model configuration lives at:

```text
common/configs/model.yml
```

If it does not exist, copy the template:

```bash
cp common/configs/model.yml.example common/configs/model.yml
```

Then fill in the real provider, API key, API base, and model name. To inspect configured models:

```bash
mini-cyber models
```

## 7. Add A Benchmark

The default path uses the `challenge_json` adapter. It scans:

```text
bench_hub/benchmarks/*.json
```

Each index entry must point to a directory containing `challenge.json`.

Recommended layout:

```text
bench_hub/benchmarks/
  mybench.json
  mybench/
    web/
      login-001/
        challenge.json
        docker-compose.yml
        Dockerfile
        files...
```

Example `bench_hub/benchmarks/mybench.json`:

```json
{
  "mybench-web-login-001": {
    "benchmark": "mybench",
    "benchmark_family": "mybench",
    "category": "web",
    "challenge": "login-001",
    "path": "mybench/web/login-001"
  }
}
```

Example static `challenge.json`:

```json
{
  "name": "login-001",
  "category": "web",
  "description": "Analyze the provided source files and recover the flag.",
  "task": "Find the flag from the provided files.",
  "files": ["app.py", "README.md"],
  "flag": "flag{example}",
  "task_profile": "ctf_local"
}
```

Example dynamic `challenge.json`:

```json
{
  "name": "login-001",
  "category": "web",
  "description": "Exploit the web service and recover the flag.",
  "task": "Exploit the target web service.",
  "files": [],
  "flag": "flag{example}",
  "task_profile": "pentest_remote",
  "compose_files": ["docker-compose.yml"],
  "compose_target_services": ["web"],
  "compose_dependency_services": ["db"],
  "target_ports": {
    "web": 8080
  },
  "target_port_protocols": {
    "web": "http"
  },
  "exposure_mode": "host_ports"
}
```

Rules for a new benchmark:

- Challenge ids must be globally unique.
- `path` is relative to `bench_hub/benchmarks/`.
- Every challenge directory must contain `challenge.json`.
- A challenge without compose files is treated as static.
- A challenge with `docker-compose.yml` is treated as dynamic.
- If the compose file has another name, declare it in `compose_files`.
- Prefer explicit `compose_target_services` and `target_ports` to avoid failed inference.
- Restart the challenge server after benchmark changes so it does not use stale metadata.

Metadata check:

```bash
PYTHONPATH=. python - <<'PY'
from bench_hub.adapters.source_config import build_default_registry

sources = [{"adapter_kind": "challenge_json", "root": "bench_hub/benchmarks"}]
challenges = build_default_registry().discover_all(sources)

cid = "mybench-web-login-001"
print(challenges[cid]["name"])
print(challenges[cid]["full_path"])
print(challenges[cid]["task_profile"])
PY
```

Launch check:

```bash
export CTF_HOST_IP=127.0.0.1
PYTHONPATH=. python bench_hub/server/challenge_server.py 127.0.0.1 8000

curl -s "http://127.0.0.1:8000/launch/mybench-web-login-001?force_recreate=true" | python -m json.tool
curl -X DELETE "http://127.0.0.1:8000/launch/mybench-web-login-001"
```

If the new benchmark is not an index-json plus `challenge.json` layout, add a custom adapter. The adapter must implement:

- `discover(source)`: convert raw benchmark data into normalized challenge metadata.
- `build_launch_spec(challenge)`: describe how the server should launch compose/runtime resources.

The interface is defined in:

```text
bench_hub/adapters/base.py
```

Adapters are registered in:

```text
bench_hub/adapters/source_config.py
```

## 8. Run run_evolve_batch.py

Start the challenge server:

```bash
export CTF_HOST_IP=127.0.0.1
PYTHONPATH=. python bench_hub/server/challenge_server.py 127.0.0.1 8000
```

Confirm model configuration exists:

```bash
test -f common/configs/model.yml
```

Minimal single-challenge command:

```bash
PYTHONPATH=. python run_evolve_batch.py \
  --config cyber_evolver/configs/evolve.yaml \
  --config-mode raw \
  --challenge-server-url http://127.0.0.1:8000 \
  --benchmark intercode_ctf \
  --challenge-id ic-crypto-5 \
  --model DeepSeek-V3.1 \
  --max-workers 1 \
  --task_workers 1 \
  --run-id handbook-smoke
```

If the default seed directory `cyber_evolver/gen0_root/skill_based` is absent, pass the active seed template directory explicitly:

```bash
--base_seed_path cyber_evolver/seed_agent_templates/skill_based
```

Run output is written under:

```text
logs/evolution_data/
```

Common failure points:

- `common/configs/model.yml` does not exist.
- `--challenge-server-url` is omitted and `CHALLENGE_SERVER_URL` is empty.
- The challenge server is not running.
- Benchmark fixture directories are missing.
- The default seed directory is missing and `--base_seed_path` was not provided.
- The LLM provider API key or API base is invalid.
