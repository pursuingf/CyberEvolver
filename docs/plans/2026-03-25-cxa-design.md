# Cxa Design

**Date:** 2026-03-25

## Goal

Build a lightweight command-line tool named `cxa` to manage multiple Codex authentication snapshots stored in `~/.codex/auth.json`. The tool should let the user log in multiple Codex accounts, save each account locally, identify which account is currently active, and switch between accounts safely.

## User Experience

The tool is designed for frequent command-line use, so the command name must stay short and easy to type. The primary commands are:

```bash
cxa login
cxa save [alias]
cxa list
cxa current
cxa switch <id-or-email-or-alias>
cxa delete <id-or-email-or-alias>
cxa rename <id-or-email-or-alias> <new-alias>
cxa doctor
```

The normal flow starts with `cxa login`, which guides the user through adding an account. The user enters an email address, optionally enters an alias, completes `codex login`, and `cxa` captures the refreshed `~/.codex/auth.json` into its own cache. After a successful capture, `cxa` asks whether the user wants to record another account.

The tool should mainly display `email` and `alias`, while keeping a short stable internal id for precise lookups and disambiguation.

## Why This Approach

Three approaches were considered:

1. A Bash-only wrapper that copies `auth.json` files around.
2. A Python CLI that manages metadata plus auth snapshots.
3. A larger profile manager built around multiple `CODEX_HOME` directories.

The recommended approach is the Python CLI. It matches the user's validated workflow, stays simple to install, gives us reliable matching logic, and keeps room for future extensions without changing the user-facing commands.

## Login Flow

`cxa login` should act as a guided wrapper around `codex login`, not as a replacement for Codex authentication.

Recommended interaction:

```text
$ cxa login

Email: 1783698464@qq.com
Alias (optional): main

Opening `codex login`...
Please complete login in Codex.

Waiting for ~/.codex/auth.json to change...
Login captured successfully.

Saved account:
  id: k3m9qx
  email: 1783698464@qq.com
  alias: main

Add another account? [y/N]:
```

If the email already exists, the tool should offer a safe update path such as overwrite, alias-only update, or cancel.

`cxa save [alias]` remains available as an advanced command for cases where the user already ran `codex login` manually and only wants to import the current auth state into `cxa`.

## Storage Layout

The tool should manage its own directory under `~/.config/cxa/`:

```text
~/.config/cxa/
  accounts.json
  auth/
    k3m9qx.json
    p8v2dt.json
  backups/
    auth.2026-03-25T10-22-31.json
```

`accounts.json` stores metadata only. Each account keeps a complete auth snapshot in `auth/<id>.json`. The `backups/` directory stores timestamped copies of the current `~/.codex/auth.json` before any switch.

Suggested metadata shape:

```json
{
  "version": 1,
  "accounts": [
    {
      "id": "k3m9qx",
      "email": "1783698464@qq.com",
      "alias": "main",
      "account_id": "acc_xxx",
      "auth_path": "auth/k3m9qx.json",
      "created_at": "2026-03-25T10:20:00+08:00",
      "last_used_at": "2026-03-25T10:21:00+08:00"
    }
  ]
}
```

The user-facing identity is `email + alias`, while the stable internal identifier is the short `id`.

## Current Account Detection

The tool should not rely on raw full-file equality because fields such as refresh timestamps and tokens can change over time even for the same account.

Detection should use this order:

1. Read `tokens.account_id` from the current `~/.codex/auth.json`.
2. Match it against cached account metadata in `accounts.json`.
3. If no metadata match exists, compute a normalized compare/hash as a fallback.
4. If still unmatched, report that the current auth is unmanaged by `cxa`.

This creates two useful concepts:

- `identity match`: same logical account, based on `tokens.account_id`
- `snapshot match`: same exact cached auth snapshot, based on normalized content

This allows `cxa current` to recognize the active account even after Codex refreshes tokens.

## Switching Behavior

`cxa switch <target>` should:

1. Resolve the target by id, email, or alias.
2. Refuse ambiguous matches and instruct the user to use the stable id.
3. Back up the current `~/.codex/auth.json` into `~/.config/cxa/backups/`.
4. Copy the selected cached auth snapshot into `~/.codex/auth.json`.
5. Re-run current-account detection to confirm the switch succeeded.
6. Update `last_used_at`.

The tool should make the backup step explicit in output so the user understands that switching is safe and reversible.

## Command Semantics

- `login`
  Guided account capture flow built around `codex login`.
- `save [alias]`
  Import the currently active `~/.codex/auth.json` without running login.
- `list`
  Show all known accounts in a compact table, clearly marking the current one.
- `current`
  Show the current active account or indicate that the current auth is unmanaged.
- `switch <target>`
  Switch to a saved account by id, email, or alias.
- `delete <target>`
  Remove the cached account record. Should include guardrails for deleting the currently active or only known account.
- `rename <target> <new-alias>`
  Update the alias only.
- `doctor`
  Validate key paths and metadata consistency to help troubleshoot issues.

## Output Style

The output should stay short and dense. For example:

```text
$ cxa list

* k3m9qx  main   1783698464@qq.com   managed/current
  p8v2dt  work   3615191049@qq.com   managed
```

And:

```text
$ cxa current
Current account: main (1783698464@qq.com)
id: k3m9qx
status: managed
```

If the current auth is unknown, the tool should say so clearly instead of guessing.

## Safety and Edge Cases

The tool should handle these cases explicitly:

- `~/.codex/auth.json` missing
- invalid JSON in auth file
- login flow completes without any auth change
- duplicate email on login or save
- ambiguous alias or email matches
- deleting an account that is currently active
- switching to a cached auth file that is missing from disk
- corrupted or missing `accounts.json`

Failure messages should be direct and actionable. The tool should avoid partial writes by using atomic file replacement where possible.

## Testing Strategy

The first implementation should include automated tests for:

- auth parsing and `tokens.account_id` extraction
- stable id generation and metadata persistence
- `login` capture flow with changed and unchanged auth snapshots
- `current` detection when tokens have refreshed
- target resolution by id, email, and alias
- `switch` backup plus overwrite plus verification
- rename and delete behavior
- `doctor` checks for missing or inconsistent files

Tests should use temporary directories so the real `~/.codex` state is never touched.

## Implementation Notes

The tool should be packaged as a simple Python CLI script and installed into a user bin directory with a symlink or executable wrapper, for example:

```bash
ln -sf /data/pxd-team/workspace/fyh/evolve_ctf_agent/scripts/cxa ~/.local/bin/cxa
```

The command name should remain `cxa` because short typing ergonomics matter for repeated daily use.
