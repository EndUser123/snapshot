"""Snapshot plugin runtime hook contract.

Single source of truth for which scripts/hooks/<event>*.py files are the
authoritative runtime entrypoints for this plugin, and the exact command string
that P:/.claude/settings.json must register to invoke each one.

The contract is consumed by:

- `scripts/doctor.py` — verify live settings.json registration.
- `scripts/tests/test_router_smoke.py` — assert the expected command appears in
  the live P:/.claude/settings.json.
- `docs/router-runtime-contract.md` — keep the documented command in sync with
  the real path.

If the package is ever relocated, update PACKAGE_ROOT and ACTIVE_SNAPSHOT_HOOKS
in this file; tests, doctor, and docs will follow.
"""

from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path("P:/packages/.claude-marketplace/plugins/snapshot")


# Filenames only — they live under PACKAGE_ROOT / "scripts/hooks/".
ACTIVE_SNAPSHOT_HOOKS: dict[str, str] = {
    "PreCompact": "scripts/hooks/snapshot_PreCompact.py",
    "SessionStart": "scripts/hooks/snapshot_SessionStart.py",
    "UserPromptSubmit": "scripts/hooks/snapshot_UserPromptSubmit.py",
}


def hook_names() -> tuple[str, ...]:
    """Return the canonical ordered tuple of active hook event names."""
    return tuple(ACTIVE_SNAPSHOT_HOOKS.keys())


def hook_path(hook_name: str) -> Path:
    """Return the absolute Path to the runtime hook script for ``hook_name``.

    Raises:
        ValueError: if ``hook_name`` is not a recognized snapshot hook event.
    """
    try:
        relative = ACTIVE_SNAPSHOT_HOOKS[hook_name]
    except KeyError as exc:
        raise ValueError(f"unknown snapshot hook: {hook_name}") from exc
    return PACKAGE_ROOT / relative


def expected_settings_command(hook_name: str) -> str:
    """Return the explicit ``python "<absolute path>"`` command string that
    should appear in P:/.claude/settings.json for this hook.

    Backslashes are normalized to forward slashes so the command can be matched
    against Windows-style and POSIX-style path representations in tests.
    """
    path = str(hook_path(hook_name)).replace("\\", "/")
    return f'python "{path}"'
