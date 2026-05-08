# CVE Bench Network and Scorer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Teach the runtime to return both internal and external service coordinates, attach the agent sandbox to the challenge network, and evaluate success through a benchmark-specific scorer that runs after every agent step.

**Architecture:** Extend compose launch output and `/launch` responses so `ChallengeClient` can preserve richer service metadata in `chal_data`. Update prompt rendering to select endpoints directly from `chal_data`, then add a benchmark scorer registry that is called once after every executed step, with CVE Bench using evaluator-driven completion and classic CTF benchmarks preserving submit-based success.

**Tech Stack:** Python, unittest, Jinja2, Docker Compose runtime materialization, FastAPI, Docker SDK subprocess orchestration

---

### Task 1: Enrich runtime service metadata with inner and external coordinates

**Files:**
- Modify: `bench_hub/server/launch_runtime.py`
- Modify: `bench_hub/server/challenge_server.py`
- Modify: `tests/test_launch_runtime.py`
- Modify: `tests/test_challenge_server_registry.py`

**Step 1: Write the failing test**

Add assertions that launched compose services now include both internal and external coordinates plus the legacy compatibility fields.

```python
self.assertEqual(runtime.services[0]["inner_host"], "ctf_test_runtime")
self.assertEqual(runtime.services[0]["inner_port"], 5000)
self.assertEqual(runtime.services[0]["external_host"], "127.0.0.1")
self.assertEqual(runtime.services[0]["external_port"], 41000)
self.assertEqual(runtime.services[0]["host"], "127.0.0.1")
self.assertEqual(runtime.services[0]["port"], 41000)
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_launch_runtime tests.test_challenge_server_registry -v
```

Expected: FAIL because `materialize_compose_runtime()` and `ServiceInfo` only expose `ip`, `internal_port`, and `external_port`.

**Step 3: Write minimal implementation**

Update runtime service records and API models to emit the richer shape.

```python
services.append(
    {
        "service_name": service_name,
        "alias": alias,
        "inner_host": alias,
        "inner_port": internal_port,
        "external_host": host_ip,
        "external_port": external_port,
        "host": host_ip,
        "port": external_port,
    }
)
```

**Step 4: Run test to verify it passes**

Run:

```bash
python -m unittest tests.test_launch_runtime tests.test_challenge_server_registry -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add bench_hub/server/launch_runtime.py bench_hub/server/challenge_server.py tests/test_launch_runtime.py tests/test_challenge_server_registry.py
git commit -m "feat(runtime): expose inner service endpoints"
```

### Task 2: Preserve runtime metadata and scoring config in `ChallengeClient`

**Files:**
- Modify: `common/agent_runtime/challenge_client.py`
- Modify: `benchmark_adapters/challenge_json.py`
- Modify: `tests/test_challenge_client_registry.py`
- Modify: `tests/test_target_runtime_recovery.py`

**Step 1: Write the failing test**

Add assertions that `get_challenge_data()` and refresh paths preserve full service records and attach runtime scoring metadata for CVE Bench challenges.

```python
self.assertEqual(challenge["target_info"]["target"]["inner_host"], "ctf_default_cvb_cve_2024_0001_runtime_target")
self.assertEqual(challenge["target_info"]["target"]["external_port"], 43090)
self.assertEqual(challenge["runtime"]["scoring"]["kind"], "http_poll")
self.assertEqual(challenge["runtime"]["scoring"]["path"], "/done")
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_challenge_client_registry tests.test_target_runtime_recovery -v
```

Expected: FAIL because `ChallengeClient` currently stores only flattened `host` / `port` style target info and does not attach runtime scoring metadata.

**Step 3: Write minimal implementation**

Preserve the full launch record in `target_info`, add a `runtime` block to `chal_data`, and derive scoring metadata from challenge source fields.

```python
result["target_info"] = deepcopy(record.get("services", {}))
result["runtime"] = {
    "project_name": record.get("project_name"),
    "network_name": record.get("network_name"),
    "scoring": record.get("scoring", {}),
}
```

**Step 4: Run test to verify it passes**

Run:

```bash
python -m unittest tests.test_challenge_client_registry tests.test_target_runtime_recovery -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add common/agent_runtime/challenge_client.py benchmark_adapters/challenge_json.py tests/test_challenge_client_registry.py tests/test_target_runtime_recovery.py
git commit -m "feat(challenge_client): preserve runtime scoring data"
```

### Task 3: Make the instance prompt choose endpoints from full `chal_data`

**Files:**
- Modify: `agent/agent.py`
- Modify: `gen0_root/skill_based/agent.py`
- Modify: `gen0_root/skill_based/instance_template.txt`
- Modify: `tests/test_agent_prompt_profiles.py`

**Step 1: Write the failing test**

Update prompt tests so they pass `inner_*` and `external_*` fields, then assert the rendered prompt prefers internal coordinates when available and falls back otherwise.

```python
"target_info": {
    "target": {
        "inner_host": "target",
        "inner_port": 9090,
        "external_host": "10.1.2.146",
        "external_port": 43090,
        "host": "10.1.2.146",
        "port": 43090,
    }
}

self.assertIn("target:9090", instance_prompt)
self.assertNotIn("10.1.2.146:43090", instance_prompt)
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_agent_prompt_profiles -v
```

Expected: FAIL because prompt rendering is still driven by Python-built `target_endpoints_block` using only `host` / `port`.

**Step 3: Write minimal implementation**

Pass full `instance_data` into the template and let Jinja select the endpoint.

```jinja2
{%- set host = info.inner_host or info.host or info.external_host -%}
{%- set port = info.inner_port or info.port or info.external_port -%}
- {{ service_name }}: `{{ host }}:{{ port }}`
```

**Step 4: Run test to verify it passes**

Run:

```bash
python -m unittest tests.test_agent_prompt_profiles -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add agent/agent.py gen0_root/skill_based/agent.py gen0_root/skill_based/instance_template.txt tests/test_agent_prompt_profiles.py
git commit -m "feat(prompt): choose runtime endpoints in jinja"
```

### Task 4: Add a unified step-end benchmark scorer

**Files:**
- Create: `utils/benchmark_scorers.py`
- Modify: `agent/agent.py`
- Modify: `gen0_root/skill_based/agent.py`
- Modify: `tests/test_agent_sandbox.py`
- Create: `tests/test_benchmark_scorers.py`

**Step 1: Write the failing test**

Create scorer unit tests for both CTF-style submit completion and CVE Bench evaluator completion, then add an agent test that verifies the scorer runs after each successful step.

```python
result = scorer(
    action="submit flag{ok}",
    observation="<<SUBMISSION||flag{ok}||SUBMISSION>>",
    chal_data={"benchmark_family": "nyu_ctf", "flag": "flag{ok}"},
    agent_state={},
)
self.assertTrue(result["done"])
```

```python
result = scorer(
    action="curl target:9090",
    observation="HTTP/1.1 200 OK",
    chal_data={"benchmark_family": "cvebench", "runtime": {"scoring": {"kind": "http_poll", "endpoint": "http://target:9091/done"}}},
    agent_state={},
)
self.assertFalse(result["done"])
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_benchmark_scorers tests.test_agent_sandbox -v
```

Expected: FAIL because no scorer registry exists yet and the agent loop still hard-codes submit-only success handling.

**Step 3: Write minimal implementation**

Create a registry that dispatches by `benchmark_family` and call it after every executed step.

```python
score_result = benchmark_scorer_registry.score_step(
    action=parse_result["command"],
    observation=observation,
    chal_data=self.chal_data,
    agent_state={"step_num": step_num},
)
if score_result["done"]:
    solved = True
    break
```

**Step 4: Run test to verify it passes**

Run:

```bash
python -m unittest tests.test_benchmark_scorers tests.test_agent_sandbox -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add utils/benchmark_scorers.py agent/agent.py gen0_root/skill_based/agent.py tests/test_agent_sandbox.py tests/test_benchmark_scorers.py
git commit -m "feat(agent): add benchmark step scorers"
```

### Task 5: Attach the agent sandbox to the challenge network and run focused verification

**Files:**
- Modify: `run_evolve_batch_skill.py`
- Modify: `common/agent_runtime/docker_env.py`
- Modify: `tests/test_run_evolve_batch_skill_scheduler.py`
- Modify: `tests/test_agent_sandbox.py`
- Modify: `tests/test_challenge_server_registry.py`
- Modify: `tests/test_challenge_client_registry.py`

**Step 1: Write the failing test**

Add coverage that the agent-side Docker environment receives the launched challenge network from `chal_data["runtime"]["network_name"]` and keeps using the richer runtime context during agent execution.

```python
self.assertEqual(docker_manager.config.network_name, "ctfnet_default_demo_runtime")
self.assertEqual(agent.chal_data["runtime"]["network_name"], "ctfnet_default_demo_runtime")
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_run_evolve_batch_skill_scheduler tests.test_agent_sandbox tests.test_challenge_server_registry tests.test_challenge_client_registry -v
```

Expected: FAIL because the agent container is created before challenge-local network metadata is wired into its runtime configuration.

**Step 3: Write minimal implementation**

Refresh the sandbox network attachment after challenge initialization and preserve runtime context all the way into `agent_execute()`.

```python
runtime_network = chal_data_runtime.get("runtime", {}).get("network_name")
if runtime_network:
    docker_manager.env._connect_to_network(runtime_network)
    docker_manager.env.config.network_name = runtime_network
```

**Step 4: Run focused verification**

Run:

```bash
python -m unittest tests.test_launch_runtime tests.test_challenge_server_registry tests.test_challenge_client_registry tests.test_agent_prompt_profiles tests.test_benchmark_scorers tests.test_agent_sandbox tests.test_run_evolve_batch_skill_scheduler tests.test_target_runtime_recovery -v
```

Expected: PASS

**Step 5: Run broader regression**

Run:

```bash
python -m unittest tests.test_autopenbench_adapter tests.test_autopenbench_layout tests.test_cvebench_layout tests.test_benchmark_adapters tests.test_launch_runtime tests.test_challenge_server_registry tests.test_challenge_client_registry tests.test_agent_prompt_profiles tests.test_benchmark_scorers tests.test_agent_sandbox tests.test_run_evolve_batch_skill_scheduler tests.test_target_runtime_recovery tests.test_challenge_server_runtime_locking -v
```

Expected: PASS

**Step 6: Commit**

```bash
git add run_evolve_batch_skill.py common/agent_runtime/docker_env.py tests/test_run_evolve_batch_skill_scheduler.py tests/test_agent_sandbox.py tests/test_challenge_server_registry.py tests/test_challenge_client_registry.py
git commit -m "feat(runtime): join sandbox to challenge network"
```
