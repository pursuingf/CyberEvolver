# CVE Bench Challenge JSON Design

## Goal

Generate `challenge.json` for every CVE Bench challenge under the current repo-local layout at `benchmarks/cvebench/critical/challenges/<CVE>/challenge.json`.

The generated files must:

- match the current `benchmarks/cvebench.json` index layout
- avoid absolute paths
- keep runtime launch fields needed by the existing adapter/runtime chain
- flatten prompt-facing fields so Jinja templates can consume them directly

## Scope

- Update the CVE Bench layout generator
- Update the adapter/runtime expectations that depend on removed fields
- Generate `challenge.json` files for all indexed CVE Bench challenges

## Output Schema

Each `challenge.json` keeps runtime fields at the top level:

- `adapter_kind`
- `benchmark_family`
- `task_profile`
- `name`
- `category`
- `description`
- `task`
- `files`
- `flag`
- `compose_files`
- `compose_target_services`
- `compose_dependency_services`
- `compose_env`
- `env_file_path`
- `target_ports`
- `service_ports`
- `internal_port`
- `exposure_mode`
- `eval_path`
- `metadata_path`
- `source_root`
- `challenge_source_root`
- `target`

Prompt-facing fields are also top-level and flat:

- `variant_names`
- `default_variant`
- `cve_description`
- `attacker_role`
- `application_service_keys`
- `application_port_suffixes`
- `proof_upload_service_key`
- `proof_upload_endpoint_suffix`
- `allowed_service_keys`
- `allowed_service_suffixes`
- `outbound_service_endpoint`
- `admin_username`
- `default_username`
- `default_password`
- `secret_file_path`
- `secret_table_name`
- `secret_table_column`
- `remote_code_executable_path`
- `denial_of_service_duration`
- `forbidden_actions`
- `environment_notes`

## Extraction Rules

- `name` comes from `eval.yml`
- `variant_names` come from `eval.yml.variants`
- `default_variant` prefers `zero_day`
- `task` and `description` are generated as a short generic task sentence rather than copying full prompt text
- prompt-facing credentials and file paths come from `critical/metadata/<CVE>.yml`
- `cve_description` is extracted from the one-day prompt lead-in, not copied as a full prompt variant
- `application_url` is split into:
  - `application_service_keys`
  - `application_port_suffixes`
- `proof_uploading_url` is split into:
  - `proof_upload_service_key`
  - `proof_upload_endpoint_suffix`
- `allowed_service_keys` and `allowed_service_suffixes` default to the application equivalents
- `forbidden_actions` and `environment_notes` default to empty lists so templates can use their own defaults

## Path Rules

All filesystem references in generated `challenge.json` must stay relative to the challenge directory.

This includes:

- `compose_files`
- `env_file_path`
- `eval_path`
- `metadata_path`
- `source_root`
- `challenge_source_root`
- path-like values inside `compose_env`

Absolute paths are not allowed.

## Compatibility Notes

- `metadata` and `prompt_variants` are no longer copied into `challenge.json`
- runtime scoring must derive from the new flattened proof-upload/application fields
- the generator must write to the new nested challenge directories already referenced by `benchmarks/cvebench.json`
