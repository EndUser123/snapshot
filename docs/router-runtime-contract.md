# Snapshot Router Runtime Contract

This document is the **live registration contract** for the snapshot plugin in
this workspace. It supersedes any prior "install via `hooks/hooks.json`" or
"P://packages/handoff/core" guidance.

## Authoritative Paths

- Runtime hook registration: `P:/.claude/settings.json`
- Local hook router / importer: `P:/.claude/hooks/__lib/hook_importer.py`
- Snapshot package source: `P:/packages/.claude-marketplace/plugins/snapshot/scripts/hooks`
- Snapshot package tests: `P:/packages/.claude-marketplace/plugins/snapshot/scripts/tests` and `P:/packages/.claude-marketplace/plugins/snapshot/tests`
- Snapshot package docs: `P:/packages/.claude-marketplace/plugins/snapshot/README.md`, `AGENTS.md`, and `docs/`

These are **not** runtime authority and should not be edited as if they were:

- `P:/packages/.claude-marketplace/plugins/snapshot/hooks/hooks.json` (and `.disabled` if present)
- Root-level `hooks.json` files
- Stale `handoff/core` or `src/handoff` references in any doc

## Active Runtime Entrypoints

| Event             | Path                                                                              | Activation                                                    |
| ----------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `PreCompact`      | `scripts/hooks/snapshot_PreCompact.py`                                            | Explicit `python "..."` command in `settings.json`            |
| `SessionStart`    | `scripts/hooks/snapshot_SessionStart.py`                                          | Either explicit command OR via the global `HookImporter`      |
| `UserPromptSubmit`| `scripts/hooks/snapshot_UserPromptSubmit.py` (`handoff_task_injector` registered) | Global UPS router only — **not** a `settings.json` entry       |

## Required `PreCompact` Matcher

`P:/.claude/settings.json` must include a `PreCompact` matcher that runs:

```json
{
  "type": "command",
  "command": "python \"P:/packages/.claude-marketplace/plugins/snapshot/scripts/hooks/snapshot_PreCompact.py\"",
  "timeout": 45
}
```

## Required `SessionStart` / `UserPromptSubmit` Registration

`SessionStart` and `UserPromptSubmit` are currently reached via the global
`HookImporter` (`P:/.claude/hooks/__lib/hook_importer.py`), which is invoked
from `settings.json` via inline `python -c "..."` commands that call
`importer.execute_hook('SessionStart', timeout=45.0)` and
`importer.execute_hook('UserPromptSubmit', timeout=15.0)`. The snapshot
modules are loaded by the importer when those commands run.

`UserPromptSubmit` is wired as a hook **module** (not a router). Its
`handoff_task_injector_hook` function is registered via the
`@register_hook("handoff_task_injector", priority=1.0)` decorator in
`P:/packages/.claude-marketplace/plugins/snapshot/scripts/hooks/snapshot_UserPromptSubmit.py`,
and the global UPS router in `P:/.claude/hooks/UserPromptSubmit_router.py`
dispatches it via `UserPromptSubmit_modules/registry.py`. There is no
`settings.json` entry that points at `snapshot_UserPromptSubmit.py`
directly; adding one would risk double-firing the injector on every prompt.

## Fail-Open Contract (PreCompact)

`snapshot_PreCompact.py` is fail-open by design. A child hook crash logs the
error and appends a warning result with `decision: approve` to the warnings
list. The router continues to the next child and ultimately prints
`decision: approve` so compaction proceeds even if snapshot capture or
commitment tracking fails. Compaction is **never blocked** by a snapshot
child failure.

## Shared Restore Output

`SessionStart` and `UserPromptSubmit` both build their restoration context
through the same renderer:

- `SessionStart` reaches it via the `restore_pipeline.restore_snapshot`
  facade, which delegates to `SessionStart_snapshot_restore.run`.
- `UserPromptSubmit` reaches it directly via
  `importlib.import_module("scripts.hooks.__lib.snapshot_v2").build_restore_message_compact`.

Both paths produce the same `<compact-restore>` block, so the user's
recovery experience is consistent across session start and first-prompt-
after-compaction.

## Verification

```powershell
python P:\packages\.claude-marketplace\plugins\snapshot\scripts\doctor.py
python -m pytest P:\packages\.claude-marketplace\plugins\snapshot\scripts\tests\test_router_smoke.py -q -p no:cacheprovider
python -m pytest P:\packages\.claude-marketplace\plugins\snapshot\scripts\tests\test_doctor.py -q -p no:cacheprovider
python -m pytest P:\packages\.claude-marketplace\plugins\snapshot\scripts\tests\test_runtime_contract.py -q -p no:cacheprovider
```

If `doctor.py` returns `"ok": true` and the three pytest files are green
(except the `test_precompact_registered_in_live_settings` RED that documents
the missing matcher when the registration edit is deferred), the contract
is satisfied.
