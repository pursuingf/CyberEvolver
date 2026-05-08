# Prompt Profile Overlay Design

**Goal:** Make prompt selection benchmark-aware without pushing benchmark-specific branching into Python prompt assembly.

**Context:** The current agent still resolves prompt profiles dynamically at runtime and precomputes many benchmark-specific text blocks in Python. That makes prompt behavior harder to inspect, harder to override per benchmark family, and harder to keep aligned with the benchmark metadata model.

## Design Summary

The prompt system will use `gen0_root/skill_based` as the default template source. During root-node initialization, the framework will look for benchmark-family-specific prompt files under `benchmarks/prompt_profiles/<family>/` and overlay any matching files onto the default set. The selected files will then be materialized into the node directory as the effective prompt templates for that run.

After that point, the agent will only read prompt files from the node directory. It will not perform another prompt-profile lookup at runtime.

## Template Overlay Model

The overlay operates at the file level, not the profile level.

Default template source:

- `gen0_root/skill_based/system_template.txt`
- `gen0_root/skill_based/instance_template.txt`
- `gen0_root/skill_based/observation_template.txt`
- `gen0_root/skill_based/output_parse_error_template.txt`

Optional benchmark-family overrides:

- `benchmarks/prompt_profiles/<benchmark_family>/system_template.txt`
- `benchmarks/prompt_profiles/<benchmark_family>/instance_template.txt`
- `benchmarks/prompt_profiles/<benchmark_family>/observation_template.txt`
- `benchmarks/prompt_profiles/<benchmark_family>/output_parse_error_template.txt`

Selection rules:

1. Always start from the `skill_based` default files.
2. If `benchmarks/prompt_profiles/<family>/` exists, replace only the files that are present there.
3. If a file is missing in the family directory, keep the default version.
4. The merged result becomes the effective prompt template set for the node.

This keeps the default prompt behavior intact while letting each benchmark family override only the pieces it actually needs.

## Root Node Materialization

Prompt selection should happen when the evolution root node is initialized.

At that moment, the framework will:

1. Read `chal_data["benchmark_family"]`.
2. Resolve the effective prompt template set using the overlay rules above.
3. Write the effective templates into the root node directory.

The root node directory should contain:

- `system_prompt.txt`
- `instance_prompt.txt`
- `observation_template.txt`
- `output_parse_error_template.txt`

These files remain Jinja templates. They are not pre-rendered into final prompt text at root initialization time.

This means:

- `system_prompt.txt` in the node directory is still a template.
- The node directory becomes the single source of truth for which prompt files were chosen for that run.
- Child nodes can inherit the same prompt baseline without repeating benchmark-family resolution.

## Rendering Boundary

Python should stop pre-assembling benchmark-specific prompt prose. Instead, Python should pass raw data into the template and let the template decide how to use it.

The render context should be kept minimal:

- `chal_data`
- `command_docs`
- `skill_descriptions`
- `workspace`
- `cwd`

Notably, Python should no longer precompute prompt-specific helper blocks such as:

- endpoint summary strings
- selected prompt variant strings
- task summary strings
- benchmark-family-specific description blocks

If a benchmark family wants to present `prompt_variants`, `default_variant`, `target_info`, `runtime.scoring`, or any other field differently, that logic belongs in the corresponding Jinja template under `benchmarks/prompt_profiles/<family>/`.

## Runtime Behavior

Once the root node has materialized the effective templates:

- agent startup reads prompt files from the node directory only
- runtime prompt resolution no longer scans fallback directories
- the same loading rule applies to system, instance, observation, and parse-error templates

This reduces runtime branching and makes the active prompt set visible directly in the node filesystem.

## Error Handling

The overlay logic should stay conservative:

- Missing benchmark-family prompt directory is not an error; use defaults.
- Missing individual override files are not errors; use the default file for that template.
- Missing default `skill_based` files are configuration errors and should fail fast.

## Testing Strategy

The implementation should be validated at four levels:

1. Overlay unit tests
   - default-only benchmark family uses only `skill_based` files
   - family-specific files override matching defaults
   - missing family files fall back file-by-file

2. Root initialization tests
   - root node creation writes effective prompt templates into the node directory
   - written files remain Jinja templates rather than rendered prompt text

3. Agent prompt loading tests
   - agent reads prompt templates from the node directory rather than benchmark profile directories
   - rendering receives `chal_data` and the small shared context only

4. Benchmark-family regression tests
   - CVE Bench can supply its own `system_template.txt` and `instance_template.txt`
   - AutoPenBench and default CTF flows continue to work when only some files are overridden

## Expected Outcome

After this change:

- `skill_based` remains the universal fallback prompt set
- benchmark families can override only the prompt files they care about
- prompt selection becomes deterministic and inspectable from the root node directory
- Python prompt orchestration becomes thinner and less benchmark-specific
