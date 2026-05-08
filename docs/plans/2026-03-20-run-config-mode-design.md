# Run Config Mode Design

## Goal

Add a required CLI argument to `run_evolve_batch_skill.py` so the caller must explicitly choose between `RAW_CONFIG` and `EVO_CONFIG`.

## Why

The current entrypoint always runs with `EVO_CONFIG`, while `RAW_CONFIG` exists in the file but is not selectable from the CLI. That makes runs ambiguous and easy to misconfigure.

The new behavior should make the choice explicit:

- `--config-mode evo`
- `--config-mode raw`

There should be no default.

## Recommended Approach

Use a single required argument:

- `--config-mode {evo,raw}`

and resolve it to a copied config profile at runtime.

### Why this approach

- The CLI is explicit and easy to read.
- `argparse` can enforce the allowed values and the required nature of the argument.
- Using a copied profile avoids mutating module-level globals, which is safer for tests and future refactors.

## Rejected Alternatives

### Two boolean flags

Example:

- `--use-evo-config`
- `--use-raw-config`

This is more awkward, and it introduces unnecessary mutual-exclusion logic.

### Optional argument with manual runtime validation

This works, but `argparse` already solves the requirement cleanly. A required choice is simpler and harder to misuse.

## Design

### CLI

Add:

- `--config-mode`

Behavior:

- required
- choices: `evo`, `raw`
- no default

### Runtime config selection

Add a small helper that:

1. selects `EVO_CONFIG` or `RAW_CONFIG`
2. copies the selected dict
3. applies `--model` override to that copied dict only
4. returns the resulting config

This helper should be the only place that resolves the run profile.

### Main flow updates

Replace direct usage of `EVO_CONFIG` in `main()` with the resolved selected config:

- run metadata
- model kwargs construction
- `submit_challenge(...)`
- any other `evo_config=...` callsites in the main flow

### Compatibility

This is intentionally a breaking CLI change:

- existing invocations must now pass `--config-mode evo` or `--config-mode raw`

That is the desired behavior.

## Testing

Add focused tests for:

1. `parse_args(...)` rejects missing `--config-mode`
2. `parse_args(...)` accepts `evo` and `raw`
3. the resolver returns a copy of the selected profile
4. `--model` override only changes the returned copy, not the module-level constants
