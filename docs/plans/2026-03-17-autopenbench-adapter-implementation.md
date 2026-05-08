# AutoPenBench Adapter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a reusable benchmark adapter architecture and use it to run an initial AutoPenBench subset end-to-end in the current framework.

**Architecture:** Introduce a benchmark adapter registry that normalizes challenge metadata and emits a generic `LaunchSpec`. Refactor `ChallengeClient` and `challenge_server` to consume the registry/spec instead of hard-coding the legacy challenge-json layout, then add an AutoPenBench adapter and a `pentest_remote` prompt profile for `gen0_root/skill_based`.

**Tech Stack:** Python, `unittest`, Jinja2 templates, Docker Compose, existing `common/agent_runtime/challenge_client.py`, `bench_hub/server/challenge_server.py`, `agent/agent.py`

---

### Task 1: Add benchmark adapter primitives and legacy adapter coverage

**Files:**
- Create: `benchmark_adapters/__init__.py`
- Create: `benchmark_adapters/base.py`
- Create: `benchmark_adapters/registry.py`
- Create: `benchmark_adapters/challenge_json.py`
- Create: `tests/test_benchmark_adapters.py`

**Step 1: Write the failing test**

Add tests that:

- build a registry with the legacy challenge-json adapter
- load a small benchmark source rooted at `./benchmarks`
- assert the normalized record includes:
  - `benchmark_name`
  - `adapter_kind == "challenge_json"`
  - `task_profile == "ctf_local"`
  - `source_fields` preserving benchmark-native keys such as `box` or `internal_port`

Use assertions like:

```python
self.assertEqual(challenge["adapter_kind"], "challenge_json")
self.assertEqual(challenge["task_profile"], "ctf_local")
self.assertIn("source_fields", challenge)
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_benchmark_adapters -v`
Expected: FAIL because `benchmark_adapters` does not exist yet.

**Step 3: Write minimal implementation**

Implement the first adapter layer with:

- a `NormalizedChallenge` dataclass or typed dict in `benchmark_adapters/base.py`
- a `BenchmarkAdapter` protocol/base class
- a `BenchmarkAdapterRegistry` that stores adapters and source definitions
- a `ChallengeJsonAdapter` that:
  - reads `*.json` catalog files under a configured root
  - resolves per-entry `challenge.json`
  - preserves source benchmark fields under `source_fields`
  - sets `task_profile="ctf_local"`

Keep the first registry API small and explicit, for example:

```python
registry = BenchmarkAdapterRegistry()
registry.register(adapter)
registry.discover_all(sources)
```

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_benchmark_adapters -v`
Expected: PASS

**Step 5: Commit**

```bash
git add benchmark_adapters/__init__.py benchmark_adapters/base.py benchmark_adapters/registry.py benchmark_adapters/challenge_json.py tests/test_benchmark_adapters.py docs/plans/2026-03-17-autopenbench-adapter-design.md docs/plans/2026-03-17-autopenbench-adapter-implementation.md
git commit -m "feat(benchmark-adapter): add registry primitives"
```

### Task 2: Migrate `ChallengeClient` metadata loading to the registry

**Files:**
- Modify: `common/agent_runtime/challenge_client.py`
- Create: `benchmark_adapters/source_config.py`
- Create: `tests/test_challenge_client_registry.py`

**Step 1: Write the failing test**

Add tests that:

- build a `ChallengeClient` with a source configuration that points to the legacy `./benchmarks` root
- assert `get_challenge_data()` still returns the old fields needed by the rest of the framework
- assert the loaded challenge now also contains:
  - `adapter_kind`
  - `task_profile`
  - `source_fields`

Example assertions:

```python
self.assertEqual(data["adapter_kind"], "challenge_json")
self.assertIn("flag_format", data)
self.assertIn("source_fields", data)
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_challenge_client_registry -v`
Expected: FAIL because `ChallengeClient._load_metadata()` still reads benchmark JSON directly.

**Step 3: Write minimal implementation**

Refactor `common/agent_runtime/challenge_client.py` to:

- replace direct catalog scanning in `_load_metadata()` with adapter registry discovery
- add a generic source configuration loader in `benchmark_adapters/source_config.py`
- support a config shape like:

```python
benchmark_sources = [
    {"adapter_kind": "challenge_json", "root": "./benchmarks"},
]
```

Do not change runtime behavior yet. This task is only about replacing metadata discovery, while keeping:

- existing `flag_format`
- existing `full_path`
- existing `target_status` / `target_info` update flow

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_challenge_client_registry -v`
Expected: PASS

**Step 5: Commit**

```bash
git add common/agent_runtime/challenge_client.py benchmark_adapters/source_config.py tests/test_challenge_client_registry.py
git commit -m "refactor(challenge_client): load benchmarks via registry"
```

### Task 3: Introduce `LaunchSpec` execution in `challenge_server` for legacy challenges

**Files:**
- Modify: `benchmark_adapters/base.py`
- Create: `bench_hub/server/launch_runtime.py`
- Modify: `bench_hub/server/challenge_server.py`
- Modify: `bench_hub/server/test_challenge_server.py`

**Step 1: Write the failing test**

Add tests that:

- build a minimal legacy `LaunchSpec` for a compose-backed challenge
- assert the server path can:
  - identify target services
  - materialize runtime patches
  - reuse previously assigned exposed ports on recreate
- keep the static-challenge path working

Example `LaunchSpec` structure for the test:

```python
LaunchSpec(
    mode="compose",
    compose_files=[compose_path],
    target_services=["cookie-injection"],
    dependency_services=[],
    exposure_mode="host_ports",
)
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest bench_hub/server.test_challenge_server -v`
Expected: FAIL because the server still derives runtime behavior directly from `challenge.json` and the local compose file.

**Step 3: Write minimal implementation**

Introduce a generic server execution path:

- define `LaunchSpec` in `benchmark_adapters/base.py`
- move compose materialization helpers into `bench_hub/server/launch_runtime.py`
- change `bench_hub/server/challenge_server.py` to:
  - load normalized challenges from the registry
  - request a `LaunchSpec` from the challenge's adapter
  - execute that spec
  - return a uniform runtime record

Preserve existing responses:

- `status`
- `project_name`
- `services`

Do not add AutoPenBench logic in this task. The goal is to make the existing challenge-json benchmark path run through the new abstraction first.

**Step 4: Run test to verify it passes**

Run: `python -m unittest bench_hub/server.test_challenge_server -v`
Expected: PASS

**Step 5: Commit**

```bash
git add benchmark_adapters/base.py bench_hub/server/launch_runtime.py bench_hub/server/challenge_server.py bench_hub/server/test_challenge_server.py
git commit -m "refactor(challenge-server): execute launch specs"
```

### Task 4: Implement the AutoPenBench adapter and first launch heuristics

**Files:**
- Create: `benchmark_adapters/autopenbench.py`
- Create: `tests/fixtures/autopenbench_minimal/data/games.json`
- Create: `tests/fixtures/autopenbench_minimal/benchmark/machines/docker-compose.yml`
- Create: `tests/fixtures/autopenbench_minimal/benchmark/machines/in-vitro/access_control/docker-compose.yml`
- Create: `tests/fixtures/autopenbench_minimal/benchmark/machines/in-vitro/access_control/vm0/Dockerfile`
- Create: `tests/fixtures/autopenbench_minimal/benchmark/milestones/command_milestones/in-vitro/access_control/vm0.txt`
- Create: `tests/fixtures/autopenbench_minimal/benchmark/milestones/stage_milestones/in-vitro/access_control/vm0.txt`
- Create: `tests/fixtures/autopenbench_minimal/benchmark/solutions/in-vitro/access_control/vm0.txt`
- Create: `tests/test_autopenbench_adapter.py`

**Step 1: Write the failing test**

Add tests against the fixture benchmark that:

- discover an AutoPenBench challenge from `games.json`
- preserve native fields like `task`, `target`, `vulnerability`
- emit:
  - `adapter_kind == "autopenbench"`
  - `task_profile == "pentest_remote"`
- build a `LaunchSpec` that:
  - includes the base compose file
  - includes the category compose file
  - targets the service declared in `games.json`
  - infers `22/tcp` exposure for `access_control`

Example assertions:

```python
self.assertEqual(challenge["task_profile"], "pentest_remote")
self.assertEqual(spec.target_services, ["in-vitro_access_control_vm0"])
self.assertEqual(spec.exposure_mode, "host_ports")
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_autopenbench_adapter -v`
Expected: FAIL because no AutoPenBench adapter exists.

**Step 3: Write minimal implementation**

Implement `benchmark_adapters/autopenbench.py` so it:

- reads `data/games.json`
- creates stable challenge ids, for example `apb-in-vitro-access_control-vm0`
- stores native fields in `source_fields`
- maps:
  - `description` to the original `task`
  - `name` to a stable human-readable identifier
  - `task_profile` to `pentest_remote`
- emits compose-based `LaunchSpec`

For the first milestone, implement exposure heuristics only for:

- `access_control -> 22/tcp`
- `web_security -> 80/tcp`
- `cryptography -> 8080/tcp`
- `real-world/cve -> prefer declared compose ports, then Dockerfile `EXPOSE``

It is acceptable for the first adapter version to reject unsupported categories explicitly instead of pretending they are runnable.

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_autopenbench_adapter -v`
Expected: PASS

**Step 5: Commit**

```bash
git add benchmark_adapters/autopenbench.py tests/fixtures/autopenbench_minimal tests/test_autopenbench_adapter.py
git commit -m "feat(benchmark-adapter): add autopenbench loader"
```

### Task 5: Add the `pentest_remote` prompt profile for `gen0_root/skill_based`

**Files:**
- Modify: `agent/agent.py`
- Modify: `gen0_root/skill_based/system_template.txt`
- Modify: `gen0_root/skill_based/instance_template.txt`
- Create: `tests/test_agent_prompt_profiles.py`
- Create: `configs/autopenbench.yaml`

**Step 1: Write the failing test**

Add prompt rendering tests that:

- render the `gen0_root/skill_based` templates with `task_profile="pentest_remote"`
- assert the instance prompt includes:
  - the original AutoPenBench task text
  - reachable endpoint information from `target_info`
- assert the prompt does not inject milestone or solution paths

Example assertions:

```python
self.assertIn("Discover, infiltrate, and escalate", rendered)
self.assertIn("10.1.2.146:22022", rendered)
self.assertNotIn("command_milestones", rendered)
```

**Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_agent_prompt_profiles -v`
Expected: FAIL because `agent/agent.py` does not pass profile-aware render context and templates are still CTF-local only.

**Step 3: Write minimal implementation**

Update `agent/agent.py` to pass profile-aware render context into Jinja, for example:

```python
system_prompt = Template(template).render(
    command_docs=cmd_docs,
    skill_descriptions=skill_descriptions,
    task_profile=self.chal_data.get("task_profile", "ctf_local"),
)
```

Then update `gen0_root/skill_based` templates so that:

- `ctf_local` keeps the current behavior
- `pentest_remote`:
  - frames the challenge as remote exploitation
  - uses the original task text as the main instance body
  - lists reachable endpoint(s)
  - keeps the current `submit` and formatting rules

Create `configs/autopenbench.yaml` as a runnable sample config that points `challenge_client` to:

- the legacy benchmark source
- the AutoPenBench source path

**Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_agent_prompt_profiles -v`
Expected: PASS

Then run a narrow smoke check for config loading:

Run: `python - <<'PY'
from common.agent_runtime.challenge_client import ChallengeClient, ChallengeClientConfig
cfg = ChallengeClientConfig(benchmark_sources=[
    {"adapter_kind": "challenge_json", "root": "./benchmarks"},
    {"adapter_kind": "autopenbench", "root": "/home/pgroup/pxd-team/workspace/fyh/pentest/references/pentest_benchmarks/auto-pen-bench"},
])
mgr = ChallengeClient(config=cfg)
print(any(v.get("adapter_kind") == "autopenbench" for v in mgr.challenges.values()))
PY`
Expected: prints `True`

**Step 5: Commit**

```bash
git add agent/agent.py gen0_root/skill_based/system_template.txt gen0_root/skill_based/instance_template.txt tests/test_agent_prompt_profiles.py configs/autopenbench.yaml
git commit -m "feat(prompt): add pentest remote profile"
```

### Task 6: Verify one AutoPenBench challenge runs end-to-end

**Files:**
- Modify: `bench_hub/server/test_challenge_server.py`
- Modify: `tests/test_challenge_client_registry.py`

**Step 1: Write the failing test**

Add one integration-style test that:

- loads an AutoPenBench fixture challenge through the registry
- asks for its `LaunchSpec`
- verifies the target service is exposed as a reachable runtime endpoint in `target_info`

If containerized fixture startup is too heavy for unit tests, keep this as a narrow launch-spec-to-runtime-record test with mocked Docker calls.

**Step 2: Run test to verify it fails**

Run: `python -m unittest bench_hub/server.test_challenge_server tests.test_challenge_client_registry -v`
Expected: FAIL until the full adapter + launch-spec path is wired together.

**Step 3: Write minimal implementation**

Finish any remaining glue so that:

- manager discovery
- server launch
- runtime target mapping
- prompt-facing `target_info`

all work together for at least one AutoPenBench category.

Prefer `in-vitro/access_control/vm0` as the first green path because it maps cleanly to SSH exposure.

**Step 4: Run test to verify it passes**

Run: `python -m unittest bench_hub/server.test_challenge_server tests.test_challenge_client_registry tests.test_autopenbench_adapter tests.test_agent_prompt_profiles -v`
Expected: PASS

Then run one manual smoke flow against the configured AutoPenBench root:

Run: `python - <<'PY'
from common.agent_runtime.challenge_client import ChallengeClient, ChallengeClientConfig
cfg = ChallengeClientConfig(benchmark_sources=[
    {"adapter_kind": "challenge_json", "root": "./benchmarks"},
    {"adapter_kind": "autopenbench", "root": "/home/pgroup/pxd-team/workspace/fyh/pentest/references/pentest_benchmarks/auto-pen-bench"},
])
mgr = ChallengeClient(config=cfg)
cid = next(k for k,v in mgr.challenges.items() if v.get("adapter_kind") == "autopenbench")
data = mgr.get_challenge_data(cid)
print(cid)
print(data["task_profile"])
print(data["target_info"])
mgr.close()
PY`
Expected: prints an AutoPenBench challenge id, `pentest_remote`, and a non-empty `target_info`

**Step 5: Commit**

```bash
git add bench_hub/server/test_challenge_server.py tests/test_challenge_client_registry.py
git commit -m "feat(runtime): run autopenbench subset"
```
