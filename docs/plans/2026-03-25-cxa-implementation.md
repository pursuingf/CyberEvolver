# Cxa Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a short, installable `cxa` CLI that can record multiple Codex auth snapshots, detect the current account, and switch accounts safely.

**Architecture:** Implement `cxa` as a small Python command-line tool under `scripts/` backed by a focused helper module that manages config paths, auth snapshot normalization, account metadata, and switch safety checks. Keep all real filesystem access injectable so tests can run entirely inside temporary directories without touching the user's live `~/.codex` state.

**Tech Stack:** Python 3, standard library (`argparse`, `json`, `pathlib`, `tempfile`, `subprocess`, `hashlib`, `shutil`, `datetime`, `uuid`, `dataclasses`), `pytest`

---

### Task 1: Inspect existing Python CLI and test patterns

**Files:**
- Review: `scripts/llm_load_test.py`
- Review: `tests/test_benchmark_base_images_script.py`
- Review: `tests/test_run_evolve_batch_skill_prompts.py`

**Step 1: Read one existing script entry point for style cues**

Run:

```bash
sed -n '1,240p' scripts/llm_load_test.py
```

Expected: a lightweight Python script structure that shows how this repo handles executable scripts.

**Step 2: Read one existing script-oriented test file**

Run:

```bash
sed -n '1,260p' tests/test_benchmark_base_images_script.py
```

Expected: existing pytest conventions for script or CLI-adjacent behavior.

**Step 3: Summarize local conventions before creating new files**

Write down a short note in the working session describing:

- where reusable helpers should live
- how tests are named
- whether executable wrappers under `scripts/` already exist

**Step 4: Commit**

No commit yet. This task is context only.

### Task 2: Add failing tests for auth parsing and normalization

**Files:**
- Create: `tests/test_cxa_auth_store.py`

**Step 1: Write the failing test for extracting `tokens.account_id`**

```python
from pathlib import Path

from scripts.cxa_lib import load_auth_identity


def test_load_auth_identity_returns_account_id(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        '{"auth_mode":"chatgpt","tokens":{"account_id":"acc_123","access_token":"x"}}',
        encoding="utf-8",
    )

    identity = load_auth_identity(auth_path)

    assert identity.account_id == "acc_123"
```

**Step 2: Write the failing test for normalized snapshot hashing**

```python
from pathlib import Path

from scripts.cxa_lib import normalized_auth_hash


def test_normalized_auth_hash_ignores_refreshing_token_fields(tmp_path: Path) -> None:
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_text(
        '{"last_refresh":"1","tokens":{"account_id":"acc_123","access_token":"a","refresh_token":"b","id_token":"c"}}',
        encoding="utf-8",
    )
    right.write_text(
        '{"last_refresh":"2","tokens":{"account_id":"acc_123","access_token":"d","refresh_token":"e","id_token":"f"}}',
        encoding="utf-8",
    )

    assert normalized_auth_hash(left) == normalized_auth_hash(right)
```

**Step 3: Run tests to verify they fail**

Run:

```bash
pytest tests/test_cxa_auth_store.py -q
```

Expected: FAIL because `scripts.cxa_lib` does not exist yet.

**Step 4: Commit**

```bash
git add tests/test_cxa_auth_store.py
git commit -m "test: add cxa auth parsing coverage"
```

### Task 3: Implement auth parsing helpers

**Files:**
- Create: `scripts/cxa_lib.py`
- Test: `tests/test_cxa_auth_store.py`

**Step 1: Write minimal auth identity helpers**

Implement:

- `AuthIdentity` dataclass
- `load_json_file(path: Path) -> dict`
- `load_auth_identity(path: Path) -> AuthIdentity`
- `normalize_auth_payload(payload: dict) -> dict`
- `normalized_auth_hash(path: Path) -> str`

Normalization should strip or blank fields that are expected to refresh:

- `last_refresh`
- `tokens.access_token`
- `tokens.refresh_token`
- `tokens.id_token`

**Step 2: Run the focused tests**

Run:

```bash
pytest tests/test_cxa_auth_store.py -q
```

Expected: PASS

**Step 3: Commit**

```bash
git add scripts/cxa_lib.py tests/test_cxa_auth_store.py
git commit -m "feat: add cxa auth normalization helpers"
```

### Task 4: Add failing tests for account index persistence

**Files:**
- Modify: `tests/test_cxa_auth_store.py`

**Step 1: Add a failing test for creating the cxa storage layout**

```python
from pathlib import Path

from scripts.cxa_lib import CxaStore


def test_store_init_creates_expected_directories(tmp_path: Path) -> None:
    store = CxaStore(tmp_path / "cxa")

    store.ensure_layout()

    assert (tmp_path / "cxa" / "accounts.json").exists()
    assert (tmp_path / "cxa" / "auth").is_dir()
    assert (tmp_path / "cxa" / "backups").is_dir()
```

**Step 2: Add a failing test for saving account metadata**

```python
from pathlib import Path

from scripts.cxa_lib import CxaStore


def test_save_account_persists_metadata_and_snapshot(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    auth_path.write_text(
        '{"auth_mode":"chatgpt","tokens":{"account_id":"acc_123","access_token":"token"}}',
        encoding="utf-8",
    )

    store = CxaStore(tmp_path / "cxa")
    record = store.save_account_from_auth(auth_path=auth_path, email="user@example.com", alias="main")

    assert record.email == "user@example.com"
    assert record.alias == "main"
    assert record.account_id == "acc_123"
    assert (tmp_path / "cxa" / "auth" / f"{record.id}.json").exists()
```

**Step 3: Run tests to verify they fail**

Run:

```bash
pytest tests/test_cxa_auth_store.py -q
```

Expected: FAIL because `CxaStore` and persistence helpers do not exist yet.

**Step 4: Commit**

```bash
git add tests/test_cxa_auth_store.py
git commit -m "test: add cxa store persistence coverage"
```

### Task 5: Implement account metadata persistence

**Files:**
- Modify: `scripts/cxa_lib.py`
- Test: `tests/test_cxa_auth_store.py`

**Step 1: Add minimal store and record models**

Implement:

- `AccountRecord` dataclass
- `CxaStore` class with:
  - `ensure_layout()`
  - `load_accounts()`
  - `write_accounts(records)`
  - `generate_account_id()`
  - `save_account_from_auth(auth_path, email, alias)`

Use atomic writes for `accounts.json` and copied auth snapshots.

**Step 2: Make id generation short but stable enough**

Generate 6-character lowercase ids from a UUID-derived alphabet such as base36 or hex. Keep retry logic so collisions are extremely unlikely but still handled safely.

**Step 3: Run focused tests**

Run:

```bash
pytest tests/test_cxa_auth_store.py -q
```

Expected: PASS

**Step 4: Commit**

```bash
git add scripts/cxa_lib.py tests/test_cxa_auth_store.py
git commit -m "feat: add cxa account storage"
```

### Task 6: Add failing tests for current-account detection and target lookup

**Files:**
- Modify: `tests/test_cxa_auth_store.py`

**Step 1: Add a failing test for current account detection by `account_id`**

```python
from pathlib import Path

from scripts.cxa_lib import CxaStore


def test_detect_current_account_matches_by_account_id(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    auth_path.write_text(
        '{"last_refresh":"old","tokens":{"account_id":"acc_123","access_token":"old"}}',
        encoding="utf-8",
    )

    store = CxaStore(tmp_path / "cxa")
    store.save_account_from_auth(auth_path=auth_path, email="user@example.com", alias="main")

    auth_path.write_text(
        '{"last_refresh":"new","tokens":{"account_id":"acc_123","access_token":"new"}}',
        encoding="utf-8",
    )

    current = store.detect_current_account(auth_path)

    assert current is not None
    assert current.email == "user@example.com"
```

**Step 2: Add a failing test for target resolution**

```python
from pathlib import Path

from scripts.cxa_lib import CxaStore


def test_resolve_target_supports_id_email_and_alias(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    auth_path.write_text(
        '{"tokens":{"account_id":"acc_123","access_token":"token"}}',
        encoding="utf-8",
    )

    store = CxaStore(tmp_path / "cxa")
    record = store.save_account_from_auth(auth_path=auth_path, email="user@example.com", alias="main")

    assert store.resolve_target(record.id).id == record.id
    assert store.resolve_target("user@example.com").id == record.id
    assert store.resolve_target("main").id == record.id
```

**Step 3: Run tests to verify failure**

Run:

```bash
pytest tests/test_cxa_auth_store.py -q
```

Expected: FAIL because detection and lookup methods do not exist yet.

**Step 4: Commit**

```bash
git add tests/test_cxa_auth_store.py
git commit -m "test: add cxa current account resolution coverage"
```

### Task 7: Implement current-account detection and lookup

**Files:**
- Modify: `scripts/cxa_lib.py`
- Test: `tests/test_cxa_auth_store.py`

**Step 1: Implement account resolution helpers**

Add:

- `detect_current_account(auth_path)`
- `resolve_target(query)`

Detection rules:

- match `account_id` first
- if needed, compare normalized auth hashes
- return `None` when unmanaged

Resolution rules:

- exact id match wins
- otherwise collect exact email and alias matches
- raise a controlled error for zero matches or ambiguous matches

**Step 2: Run tests**

Run:

```bash
pytest tests/test_cxa_auth_store.py -q
```

Expected: PASS

**Step 3: Commit**

```bash
git add scripts/cxa_lib.py tests/test_cxa_auth_store.py
git commit -m "feat: add cxa account lookup"
```

### Task 8: Add failing tests for switch, backup, rename, and delete

**Files:**
- Modify: `tests/test_cxa_auth_store.py`

**Step 1: Add a failing test for safe switch behavior**

```python
from pathlib import Path

from scripts.cxa_lib import CxaStore


def test_switch_account_backs_up_and_replaces_auth(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    auth_path.write_text(
        '{"tokens":{"account_id":"acc_111","access_token":"one"}}',
        encoding="utf-8",
    )

    store = CxaStore(tmp_path / "cxa")
    store.save_account_from_auth(auth_path=auth_path, email="one@example.com", alias="one")

    auth_path.write_text(
        '{"tokens":{"account_id":"acc_222","access_token":"two"}}',
        encoding="utf-8",
    )
    record = store.save_account_from_auth(auth_path=auth_path, email="two@example.com", alias="two")

    store.switch_account(target=record.id, auth_path=auth_path)

    current = store.detect_current_account(auth_path)
    assert current is not None
    assert current.id == record.id
    assert any((tmp_path / "cxa" / "backups").iterdir())
```

**Step 2: Add a failing test for rename and delete**

```python
from pathlib import Path

from scripts.cxa_lib import CxaStore


def test_rename_and_delete_account_update_metadata(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    auth_path.write_text(
        '{"tokens":{"account_id":"acc_123","access_token":"token"}}',
        encoding="utf-8",
    )

    store = CxaStore(tmp_path / "cxa")
    record = store.save_account_from_auth(auth_path=auth_path, email="user@example.com", alias="main")

    renamed = store.rename_account(record.id, "work")
    assert renamed.alias == "work"

    store.delete_account(record.id, current_auth_path=tmp_path / "other.json", allow_current=True)
    assert store.load_accounts() == []
```

**Step 3: Run tests to verify failure**

Run:

```bash
pytest tests/test_cxa_auth_store.py -q
```

Expected: FAIL because switch and mutation helpers do not exist yet.

**Step 4: Commit**

```bash
git add tests/test_cxa_auth_store.py
git commit -m "test: add cxa switch and mutation coverage"
```

### Task 9: Implement switch, backup, rename, delete, and doctor primitives

**Files:**
- Modify: `scripts/cxa_lib.py`
- Test: `tests/test_cxa_auth_store.py`

**Step 1: Implement mutation helpers**

Add:

- `backup_current_auth(auth_path)`
- `switch_account(target, auth_path)`
- `rename_account(target, new_alias)`
- `delete_account(target, current_auth_path, allow_current=False)`
- `doctor(codex_auth_path)`

Delete rules:

- reject deleting a current managed account unless explicitly allowed
- remove both metadata and cached auth snapshot when present

Doctor rules:

- report missing `accounts.json`
- report missing auth snapshots referenced by metadata
- report whether current auth is managed or unmanaged

**Step 2: Run tests**

Run:

```bash
pytest tests/test_cxa_auth_store.py -q
```

Expected: PASS

**Step 3: Commit**

```bash
git add scripts/cxa_lib.py tests/test_cxa_auth_store.py
git commit -m "feat: add cxa switching and maintenance helpers"
```

### Task 10: Add failing CLI tests for command output and argument flow

**Files:**
- Create: `tests/test_cxa_cli.py`

**Step 1: Add a failing test for `current` output**

```python
from pathlib import Path

from scripts.cxa import main


def test_current_command_prints_managed_account(tmp_path: Path, capsys, monkeypatch) -> None:
    cxa_home = tmp_path / "cxa"
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    auth_path.write_text(
        '{"tokens":{"account_id":"acc_123","access_token":"token"}}',
        encoding="utf-8",
    )

    monkeypatch.setenv("CXA_HOME", str(cxa_home))
    monkeypatch.setenv("CODEX_AUTH_PATH", str(auth_path))

    exit_code = main(["save", "main", "--email", "user@example.com"])
    assert exit_code == 0

    exit_code = main(["current"])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "user@example.com" in captured.out
    assert "managed" in captured.out
```

**Step 2: Add a failing test for `list` and `switch`**

```python
from pathlib import Path

from scripts.cxa import main


def test_list_and_switch_commands_work(tmp_path: Path, capsys, monkeypatch) -> None:
    cxa_home = tmp_path / "cxa"
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"

    monkeypatch.setenv("CXA_HOME", str(cxa_home))
    monkeypatch.setenv("CODEX_AUTH_PATH", str(auth_path))

    auth_path.write_text('{"tokens":{"account_id":"acc_111","access_token":"one"}}', encoding="utf-8")
    assert main(["save", "one", "--email", "one@example.com"]) == 0

    auth_path.write_text('{"tokens":{"account_id":"acc_222","access_token":"two"}}', encoding="utf-8")
    assert main(["save", "two", "--email", "two@example.com"]) == 0

    assert main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "one@example.com" in listed
    assert "two@example.com" in listed

    assert main(["switch", "one@example.com"]) == 0
```

**Step 3: Run tests to verify failure**

Run:

```bash
pytest tests/test_cxa_cli.py -q
```

Expected: FAIL because the CLI entry point does not exist yet.

**Step 4: Commit**

```bash
git add tests/test_cxa_cli.py
git commit -m "test: add cxa cli coverage"
```

### Task 11: Implement the `cxa` CLI entry point

**Files:**
- Create: `scripts/cxa`
- Create: `scripts/cxa.py`
- Modify: `scripts/cxa_lib.py`
- Test: `tests/test_cxa_cli.py`

**Step 1: Build the CLI parser**

Implement `main(argv=None) -> int` using `argparse`.

Commands:

- `login`
- `save`
- `list`
- `current`
- `switch`
- `delete`
- `rename`
- `doctor`

Environment overrides for testability:

- `CXA_HOME`
- `CODEX_AUTH_PATH`

Use default paths in production:

- `~/.config/cxa`
- `~/.codex/auth.json`

**Step 2: Implement command handlers**

Handlers should:

- print compact user-facing output
- map controlled exceptions to non-zero exit codes
- avoid stack traces for expected user errors

For `save`, support:

- positional optional alias
- required `--email` in non-interactive mode

**Step 3: Add an executable wrapper**

Create `scripts/cxa` with:

```python
#!/usr/bin/env python3
from scripts.cxa import main

raise SystemExit(main())
```

Mark it executable.

**Step 4: Run CLI tests**

Run:

```bash
pytest tests/test_cxa_cli.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add scripts/cxa scripts/cxa.py scripts/cxa_lib.py tests/test_cxa_cli.py
git commit -m "feat: add cxa command line interface"
```

### Task 12: Add failing tests for the guided `login` flow

**Files:**
- Modify: `tests/test_cxa_cli.py`

**Step 1: Add a failing test for successful guided login capture**

```python
from pathlib import Path

from scripts.cxa import main


def test_login_command_runs_codex_login_and_saves_account(tmp_path: Path, monkeypatch, capsys) -> None:
    cxa_home = tmp_path / "cxa"
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    auth_path.write_text('{"tokens":{"account_id":"acc_old","access_token":"old"}}', encoding="utf-8")

    monkeypatch.setenv("CXA_HOME", str(cxa_home))
    monkeypatch.setenv("CODEX_AUTH_PATH", str(auth_path))
    monkeypatch.setattr("builtins.input", lambda prompt="": "user@example.com" if "Email" in prompt else "")

    def fake_run_login(*args, **kwargs):
        auth_path.write_text('{"tokens":{"account_id":"acc_new","access_token":"new"}}', encoding="utf-8")
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr("scripts.cxa.subprocess.run", fake_run_login)

    exit_code = main(["login"])

    assert exit_code == 0
    assert "Login captured successfully." in capsys.readouterr().out
```

**Step 2: Add a failing test for unchanged auth during login**

```python
from pathlib import Path

from scripts.cxa import main


def test_login_command_fails_when_auth_does_not_change(tmp_path: Path, monkeypatch) -> None:
    cxa_home = tmp_path / "cxa"
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    auth_path.write_text('{"tokens":{"account_id":"acc_old","access_token":"old"}}', encoding="utf-8")

    monkeypatch.setenv("CXA_HOME", str(cxa_home))
    monkeypatch.setenv("CODEX_AUTH_PATH", str(auth_path))
    monkeypatch.setattr("builtins.input", lambda prompt="": "user@example.com" if "Email" in prompt else "")

    def fake_run_login(*args, **kwargs):
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr("scripts.cxa.subprocess.run", fake_run_login)

    assert main(["login"]) == 1
```

**Step 3: Run tests to verify failure**

Run:

```bash
pytest tests/test_cxa_cli.py -q
```

Expected: FAIL because the guided login flow is not implemented yet.

**Step 4: Commit**

```bash
git add tests/test_cxa_cli.py
git commit -m "test: add cxa login flow coverage"
```

### Task 13: Implement the guided `login` workflow

**Files:**
- Modify: `scripts/cxa.py`
- Modify: `scripts/cxa_lib.py`
- Test: `tests/test_cxa_cli.py`

**Step 1: Implement interactive login prompts**

Prompt for:

- email
- optional alias

After a successful capture, ask:

- `Add another account? [y/N]:`

Loop only when the user answers yes.

**Step 2: Implement login capture flow**

Algorithm:

1. snapshot the current auth hash or raw bytes if the file exists
2. run `codex login`
3. if login subprocess returns non-zero, stop and return failure
4. re-read auth
5. if unchanged, print an actionable failure message
6. save the account into cxa storage

For duplicate email:

- prompt to overwrite, update alias only, or cancel

**Step 3: Run tests**

Run:

```bash
pytest tests/test_cxa_cli.py -q
```

Expected: PASS

**Step 4: Commit**

```bash
git add scripts/cxa.py scripts/cxa_lib.py tests/test_cxa_cli.py
git commit -m "feat: add cxa guided login"
```

### Task 14: Add doctor and edge-case CLI coverage

**Files:**
- Modify: `tests/test_cxa_cli.py`

**Step 1: Add a failing test for `doctor` with missing files**

```python
from scripts.cxa import main


def test_doctor_reports_missing_auth_file(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CXA_HOME", str(tmp_path / "cxa"))
    monkeypatch.setenv("CODEX_AUTH_PATH", str(tmp_path / "missing-auth.json"))

    assert main(["doctor"]) == 0
    assert "missing" in capsys.readouterr().out.lower()
```

**Step 2: Add a failing test for unmanaged current auth**

```python
from pathlib import Path

from scripts.cxa import main


def test_current_reports_unmanaged_auth(tmp_path: Path, monkeypatch, capsys) -> None:
    cxa_home = tmp_path / "cxa"
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    auth_path.write_text('{"tokens":{"account_id":"acc_unmanaged","access_token":"token"}}', encoding="utf-8")

    monkeypatch.setenv("CXA_HOME", str(cxa_home))
    monkeypatch.setenv("CODEX_AUTH_PATH", str(auth_path))

    assert main(["current"]) == 0
    assert "unmanaged" in capsys.readouterr().out.lower()
```

**Step 3: Run tests to verify failure**

Run:

```bash
pytest tests/test_cxa_cli.py -q
```

Expected: FAIL if doctor or unmanaged reporting is incomplete.

**Step 4: Commit**

```bash
git add tests/test_cxa_cli.py
git commit -m "test: add cxa doctor coverage"
```

### Task 15: Finish CLI edge handling and verify the full test suite

**Files:**
- Modify: `scripts/cxa.py`
- Modify: `scripts/cxa_lib.py`
- Test: `tests/test_cxa_auth_store.py`
- Test: `tests/test_cxa_cli.py`

**Step 1: Implement any missing doctor and unmanaged output behavior**

Tighten output wording so the CLI remains compact and clear.

**Step 2: Run targeted tests**

Run:

```bash
pytest tests/test_cxa_auth_store.py tests/test_cxa_cli.py -q
```

Expected: PASS

**Step 3: Run a broader safety check**

Run:

```bash
python3 scripts/cxa --help
```

Expected: usage output listing the supported subcommands.

**Step 4: Dry-run install shape**

Run:

```bash
test -x scripts/cxa
```

Expected: zero exit status

**Step 5: Commit**

```bash
git add scripts/cxa scripts/cxa.py scripts/cxa_lib.py tests/test_cxa_auth_store.py tests/test_cxa_cli.py docs/plans/2026-03-25-cxa-design.md docs/plans/2026-03-25-cxa-implementation.md
git commit -m "feat: add cxa codex auth manager"
```
