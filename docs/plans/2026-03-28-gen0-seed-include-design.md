# Gen0 Seed Include Design

## Goal

Allow `run_evolve_batch_skill.py` to build `gen0_root` from `gen0_root/skill_based` while only copying explicitly selected `commands/` and `skills/` entries into the root node.

## Context

Today `EvolutionOrchestrator.init_generation_zero()` copies the entire seed directory into `gen0_root/src`. That means every command and skill under `gen0_root/skill_based` is:

- copied into the node filesystem
- copied into the runtime container by `run_node_task()`
- rendered into model context via `cmd_docs` and `skill_descriptions`

For the CVE Bench workflow, the user wants tool selection to happen once at `gen0_root` creation time. After that, child nodes should inherit the filtered `src/` tree without any runtime-specific filtering.

## Approved Design

### Runner Interface

Add a repeatable CLI argument:

- `--seed-include commands/load_skill.py`
- `--seed-include commands/submit.py`
- `--seed-include skills/sql_injection`

Each value is resolved relative to `--base_seed_path`.

Supported targets:

- `commands/<file>`
- `skills/<directory>`

Unsupported or missing paths should fail fast with a clear error.

If no `--seed-include` values are passed, `gen0_root` should keep the base framework files but not copy any extra `commands/` or `skills/` entries from the seed.

### Gen0 Materialization

`EvolutionOrchestrator.init_generation_zero()` should stop doing a blind `copytree()` of the full seed directory for the root node.

Instead, it should materialize `gen0_root/src` in two phases:

1. Copy all base framework files from the seed except `commands/` and `skills/`.
2. Copy only the whitelisted `commands/` files and `skills/` directories requested by `--seed-include`.

This keeps:

- `agent.py`
- `benchmark_scorers.py`
- prompt templates
- any other non-tool base files

always present in `gen0_root`.

### Inheritance and Runtime Behavior

No runtime filtering is added.

Child nodes continue to copy their parent `src/` tree exactly as they do today. Because `run_node_task()` and prompt assembly already scan `node.commands_dir` and `node.skills_dir`, filtering at `gen0_root` creation automatically makes runtime tool availability and prompt-visible tool docs stay in sync.

## Error Handling

Fail fast when:

- a `--seed-include` path does not exist under the seed
- a `--seed-include` path is not inside `commands/` or `skills/`
- a `commands/` include points to a directory
- a `skills/` include points to a file

## Testing

Focused tests should cover:

- `gen0_root` initialization copies only selected command files and selected skill directories
- base framework files still exist when tool selection is used
- invalid `--seed-include` values fail fast
- filtered root contents produce filtered prompt-visible command docs in `run_node_task()` setup
