# Prompt Profile Overlay Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add benchmark-family prompt overlays that materialize into the evolution root node at initialization time, while keeping `gen0_root/skill_based` as the default prompt baseline.

**Architecture:** Introduce a small prompt-profile resolver that starts from `gen0_root/skill_based`, overlays any files found under `benchmarks/prompt_profiles/<family>/`, and writes the effective prompt templates into `gen0_root`'s node `src/` directory as `system_prompt.txt`, `instance_prompt.txt`, `observation_template.txt`, and `output_parse_error_template.txt`. Keep prompt rendering thin by passing raw `chal_data` plus a few shared variables into Jinja and removing benchmark-specific prose assembly from Python.

**Tech Stack:** Python 3, `pathlib`, Jinja2, `unittest`, existing evolution/node orchestration code

---

### Task 1: Add Prompt Profile Fixtures And Failing Overlay Tests

**Files:**
- Create: `benchmarks/prompt_profiles/autopenbench/system_template.txt`
- Create: `benchmarks/prompt_profiles/autopenbench/instance_template.txt`
- Create: `benchmarks/prompt_profiles/cvebench/system_template.txt`
- Create: `benchmarks/prompt_profiles/cvebench/instance_template.txt`
- Create: `benchmarks/prompt_profiles/ctfbench/system_template.txt`
- Create: `benchmarks/prompt_profiles/ctfbench/instance_template.txt`
- Create: `tests/test_prompt_profile_overlay.py`
- Modify: `tests/test_agent_prompt_profiles.py`

**Step 1: Write the failing overlay tests**

```python
class PromptProfileOverlayTests(unittest.TestCase):
    def test_resolve_prompt_sources_uses_defaults_when_family_missing(self) -> None:
        selected = resolve_prompt_profile_sources(
            project_root=PROJECT_ROOT,
            benchmark_family="missing-family",
        )
        self.assertEqual(
            selected["system_prompt.txt"].name,
            "system_template.txt",
        )
        self.assertEqual(
            selected["instance_prompt.txt"].name,
            "instance_template.txt",
        )

    def test_resolve_prompt_sources_overrides_only_present_family_files(self) -> None:
        selected = resolve_prompt_profile_sources(
            project_root=PROJECT_ROOT,
            benchmark_family="cvebench",
        )
        self.assertIn("benchmarks/prompt_profiles/cvebench", str(selected["system_prompt.txt"]))
        self.assertIn("benchmarks/prompt_profiles/cvebench", str(selected["instance_prompt.txt"]))
        self.assertIn("gen0_root/skill_based", str(selected["observation_template.txt"]))

    def test_materialize_prompt_templates_writes_node_prompt_files(self) -> None:
        materialize_prompt_templates(
            destination_dir=self.tempdir_path,
            project_root=PROJECT_ROOT,
            benchmark_family="cvebench",
        )
        self.assertTrue((self.tempdir_path / "system_prompt.txt").exists())
        self.assertTrue((self.tempdir_path / "instance_prompt.txt").exists())
        self.assertTrue((self.tempdir_path / "observation_template.txt").exists())
        self.assertTrue((self.tempdir_path / "output_parse_error_template.txt").exists())
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_prompt_profile_overlay -v
```

Expected: FAIL with `ImportError` or missing `resolve_prompt_profile_sources` / `materialize_prompt_templates`.

**Step 3: Add prompt-profile fixture files from the current benchmark designs**

Use the existing family prompt content as the starting point:

- `benchmarks/prompt_profiles/autopenbench/*.txt`
- `benchmarks/prompt_profiles/cvebench/*.txt`
- `benchmarks/prompt_profiles/ctfbench/*.txt`

These files should stay Jinja-based and should read from `chal_data` rather than Python-prebuilt prose blocks.

**Step 4: Update prompt rendering tests to target the new raw-context contract**

Adjust `tests/test_agent_prompt_profiles.py` so the expectations come from:

- `chal_data`
- `workspace`
- `cwd`
- `command_docs`
- `skill_descriptions`

and not from Python-generated helper strings such as `selected_prompt_variant_line` or `target_endpoints_block`.

**Step 5: Commit**

```bash
git add benchmarks/prompt_profiles tests/test_prompt_profile_overlay.py tests/test_agent_prompt_profiles.py
git commit -m "test(prompt): add overlay coverage"
```

### Task 2: Implement The Prompt Profile Resolver And Root Materialization

**Files:**
- Create: `utils/prompt_profiles.py`
- Modify: `evolve/orchestrator.py`
- Modify: `evolve/node.py`
- Test: `tests/test_prompt_profile_overlay.py`

**Step 1: Write the failing implementation-facing test for root initialization**

Add a root-node integration test like this:

```python
def test_init_generation_zero_materializes_family_prompt_templates(self) -> None:
    orchestrator = EvolutionOrchestrator(
        root_dir=str(self.run_root),
        base_seed_path=str(PROJECT_ROOT / "gen0_root" / "skill_based"),
        llm=FakeLLM(),
        logger=logging.getLogger("tests.prompt_overlay"),
    )

    chal_data = {"benchmark_family": "cvebench", "workspace": "/tmp/work"}
    nodes = orchestrator.init_generation_zero(chal_data=chal_data)

    node_src = nodes[0].src_path
    self.assertTrue((node_src / "system_prompt.txt").exists())
    self.assertTrue((node_src / "instance_prompt.txt").exists())
    self.assertFalse((node_src / "system_prompt.txt").read_text(encoding="utf-8").startswith("You are now working on "))
```

**Step 2: Run tests to verify they fail**

Run:

```bash
python -m unittest tests.test_prompt_profile_overlay -v
```

Expected: FAIL because root initialization still only copies the seed and does not materialize benchmark-aware prompt files.

**Step 3: Write minimal implementation**

Create a helper module with a single source of truth for prompt filenames:

```python
PROMPT_FILE_MAP = {
    "system_prompt.txt": "system_template.txt",
    "instance_prompt.txt": "instance_template.txt",
    "observation_template.txt": "observation_template.txt",
    "output_parse_error_template.txt": "output_parse_error_template.txt",
}

def resolve_prompt_profile_sources(project_root: Path, benchmark_family: str | None) -> dict[str, Path]:
    ...

def materialize_prompt_templates(destination_dir: Path, project_root: Path, benchmark_family: str | None) -> dict[str, Path]:
    ...
```

Then wire it into `EvolutionOrchestrator.init_generation_zero(...)`:

```python
node.copy_from(str(self.base_seed_path))
materialize_prompt_templates(
    destination_dir=node.src_path,
    project_root=Path(__file__).resolve().parents[1],
    benchmark_family=str((chal_data or {}).get("benchmark_family") or "").strip().lower(),
)
```

Update `EvolutionNode.prompt_templates` to:

- prefer `system_prompt.txt` over legacy `system_template.txt`
- prefer `instance_prompt.txt` over legacy `instance_template.txt`
- keep returning all four prompt contents as one dictionary

Remove the dead YAML-era `_prerender_instance_prompt(...)` flow from `evolve/orchestrator.py`.

**Step 4: Run tests to verify they pass**

Run:

```bash
python -m unittest tests.test_prompt_profile_overlay -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add utils/prompt_profiles.py evolve/orchestrator.py evolve/node.py tests/test_prompt_profile_overlay.py
git commit -m "feat(prompt): materialize benchmark prompt overlays"
```

### Task 3: Slim Prompt Rendering To Raw Context And Node-Local Templates

**Files:**
- Modify: `gen0_root/skill_based/agent.py`
- Modify: `agent/agent.py`
- Modify: `gen0_root/skill_based/system_template.txt`
- Modify: `gen0_root/skill_based/instance_template.txt`
- Modify: `tests/test_agent_prompt_profiles.py`

**Step 1: Write the failing render-contract tests**

Add or update tests like:

```python
def test_agent_renders_default_templates_from_raw_chal_data(self) -> None:
    agent = self.build_agent(
        {
            "workspace": "/tmp/cvb",
            "benchmark_family": "cvebench",
            "task": "Reach the target and satisfy the benchmark goal.",
            "target_info": {
                "target": {
                    "inner_host": "cvb_cve_2023_37999_target",
                    "inner_port": 9090,
                    "external_host": "10.1.2.146",
                    "external_port": 52141,
                }
            },
        }
    )

    system_prompt = agent.memory[0]["content"]
    instance_prompt = agent.memory[1]["content"]

    self.assertIn("Reach the target and satisfy the benchmark goal.", instance_prompt)
    self.assertIn("cvb_cve_2023_37999_target", instance_prompt)
    self.assertNotIn("selected_prompt_variant_line", instance_prompt)
    self.assertNotIn("challenge_mode_label", system_prompt)
```

**Step 2: Run tests to verify they fail**

Run:

```bash
python -m unittest tests.test_agent_prompt_profiles -v
```

Expected: FAIL because the current templates and agents still rely on Python-side helper fields and runtime profile resolution.

**Step 3: Write minimal implementation**

Update both agent implementations so prompt rendering only receives:

```python
prompt_context = {
    "chal_data": self.chal_data,
    "command_docs": cmd_docs,
    "skill_descriptions": skill_descriptions,
    "workspace": self.cwd,
    "cwd": self.cwd,
}
```

In both `gen0_root/skill_based/agent.py` and `agent/agent.py`:

- remove benchmark-family prompt lookup from runtime rendering
- remove Python-side helper-string assembly for prompt text
- render prompt templates directly from the node-local files passed in through `PromptTemplates`

Update the default `skill_based` templates so they read from `chal_data`, for example:

```jinja2
Mission: {{ chal_data.get("task") or chal_data.get("description") or "" }}
{% for service_name, info in (chal_data.get("target_info") or {}).items() %}
- {{ service_name }}: {{ info.get("inner_host") or info.get("host") }}:{{ info.get("inner_port") or info.get("port") }}
{% endfor %}
```

Keep the templates as templates. Do not pre-render them into fixed text at root initialization time.

**Step 4: Run tests to verify they pass**

Run:

```bash
python -m unittest tests.test_agent_prompt_profiles -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add gen0_root/skill_based/agent.py agent/agent.py gen0_root/skill_based/system_template.txt gen0_root/skill_based/instance_template.txt tests/test_agent_prompt_profiles.py
git commit -m "feat(prompt): render from raw challenge data"
```

### Task 4: Run Full Prompt-Focused Regression And Clean Compatibility Edges

**Files:**
- Modify: `tests/test_run_evolve_batch_skill_scheduler.py`
- Modify: `tests/test_run_evolve_batch_skill_guards.py`
- Modify: `docs/plans/2026-03-20-prompt-profile-overlay-design.md` if implementation drift requires a note
- Test: `tests/test_prompt_profile_overlay.py`
- Test: `tests/test_agent_prompt_profiles.py`
- Test: `tests/test_benchmark_scorers.py`
- Test: `tests/test_agent_sandbox.py`
- Test: `tests/test_run_evolve_batch_skill_scheduler.py`

**Step 1: Add one regression test that exercises the actual gen0 path**

Example:

```python
def test_gen0_root_contains_effective_prompt_files_after_init(self) -> None:
    nodes = orchestrator.init_generation_zero(chal_data={"benchmark_family": "cvebench"})
    node_src = nodes[0].src_path
    self.assertTrue((node_src / "system_prompt.txt").exists())
    self.assertTrue((node_src / "instance_prompt.txt").exists())
```

**Step 2: Run the focused regression suite**

Run:

```bash
python -m unittest \
  tests.test_prompt_profile_overlay \
  tests.test_agent_prompt_profiles \
  tests.test_benchmark_scorers \
  tests.test_agent_sandbox \
  tests.test_run_evolve_batch_skill_scheduler \
  -v
```

Expected: PASS.

**Step 3: Run the broader regression suite**

Run:

```bash
python -m unittest \
  tests.test_autopenbench_adapter \
  tests.test_autopenbench_layout \
  tests.test_cvebench_layout \
  tests.test_benchmark_adapters \
  tests.test_launch_runtime \
  tests.test_challenge_server_registry \
  tests.test_challenge_client_registry \
  tests.test_agent_prompt_profiles \
  tests.test_benchmark_scorers \
  tests.test_agent_sandbox \
  tests.test_run_evolve_batch_skill_scheduler \
  tests.test_target_runtime_recovery \
  tests.test_challenge_server_runtime_locking \
  tests.test_prompt_profile_overlay \
  -v
```

Expected: PASS.

**Step 4: Update docs only if implementation required a compatibility note**

If the final implementation keeps a legacy fallback from `system_template.txt` to `system_prompt.txt`, record that in the design doc so future cleanup is intentional rather than accidental.

**Step 5: Commit**

```bash
git add tests/test_run_evolve_batch_skill_scheduler.py tests/test_run_evolve_batch_skill_guards.py docs/plans/2026-03-20-prompt-profile-overlay-design.md
git commit -m "test(prompt): cover gen0 prompt materialization"
```
