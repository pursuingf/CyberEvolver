# CVE Bench Instance Template Design

## Goal

Rewrite the CVE Bench `zero_day` and `one_day` instance prompt templates as real Jinja templates that preserve the upstream prompt style while supporting runtime-provided service aliases and multi-service challenges.

## Scope

- Touch only:
  - `benchmarks/prompt_profiles/cvebench/zero_day/instance_template.txt`
  - `benchmarks/prompt_profiles/cvebench/one_day/instance_template.txt`
- Do not modify `gen0_root/skill_based/agent.py`.
- Keep the prompt wording as close as practical to the original `eval.yml` prompt text.

## Data Contract

Templates will render from `instance_data`, matching:

```python
Template(template_text).render(instance_data=chal_data)
```

The templates expect these shallow fields:

- `service_names`
- `application_service_keys`
- `application_port_suffixes`
- `proof_upload_service_key`
- `proof_upload_endpoint_suffix`
- `allowed_service_keys`
- `allowed_service_suffixes`
- `outbound_service_endpoint`
- `attacker_role`
- `cve_description`
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

`service_names` is runtime data supplied by `ChallengeClient`, not static challenge metadata.

## Template Behavior

- Build application targets from `application_service_keys` plus `application_port_suffixes`.
- Build proof-upload target from `proof_upload_service_key` plus `proof_upload_endpoint_suffix`.
- Fall back from `allowed_*` fields to `application_*` fields when `allowed_*` is missing.
- Fall back from runtime alias to the canonical service key if `service_names[key]` is missing.
- Keep `outbound_service_endpoint` literal and do not remap it through `service_names`.

## Prompt Style

- Preserve the original CVE Bench structure:
  - `# MISSION BRIEF`
  - benchmark-style task paragraph
  - 8 attack objectives
  - forbidden actions
  - environment notes
  - `# OPERATIONAL GUIDANCE`
- `one_day` differs from `zero_day` only by inserting the vulnerability description and the "exploit this vulnerability" wording.

## Multi-Service Handling

Challenges like `CVE-2024-30542` use more than one service, for example `target:9090,mailhog:8025`.

The templates will join multiple rendered endpoints with commas so the prompt remains close to the upstream wording while still using runtime aliases such as `target-uuid:9090,mailhog-uuid:8025`.
