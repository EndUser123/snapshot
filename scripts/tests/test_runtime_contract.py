"""Tests for scripts/hooks/__lib/runtime_contract.py.

Problem: If the runtime contract drifts from the live P:/.claude/settings.json
matcher or from the package layout, snapshot hook activation silently breaks.

Situation: Three call sites (doctor, test_router_smoke, the contract doc) all
import from this module. Drift would show as doctor.ok=False and
test_router_smoke FAIL.

Symptom these tests guard against: contract path pointing at the wrong file,
unexpected hook names, or a command string that reintroduces $CLAUDE_PLUGIN_ROOT
or `hooks.json` (both of which are not the activation model for this package).
"""

from __future__ import annotations

from scripts.hooks.__lib.runtime_contract import (
    ACTIVE_SNAPSHOT_HOOKS,
    expected_settings_command,
    hook_names,
    hook_path,
)


def test_active_snapshot_hooks_are_router_entrypoints() -> None:
    assert ACTIVE_SNAPSHOT_HOOKS["PreCompact"].endswith(
        "scripts/hooks/snapshot_PreCompact.py"
    )
    assert ACTIVE_SNAPSHOT_HOOKS["SessionStart"].endswith(
        "scripts/hooks/snapshot_SessionStart.py"
    )
    assert ACTIVE_SNAPSHOT_HOOKS["UserPromptSubmit"].endswith(
        "scripts/hooks/snapshot_UserPromptSubmit.py"
    )


def test_hook_names_are_stable() -> None:
    assert hook_names() == ("PreCompact", "SessionStart", "UserPromptSubmit")


def test_hook_path_resolves_under_package_root() -> None:
    p = hook_path("PreCompact")
    # Must be absolute and live under the snapshot marketplace plugin.
    assert p.is_absolute()
    assert str(p).replace("\\", "/").startswith(
        "P:/packages/.claude-marketplace/plugins/snapshot/"
    )


def test_expected_settings_command_uses_absolute_package_path() -> None:
    command = expected_settings_command("PreCompact")
    assert (
        "P:/packages/.claude-marketplace/plugins/snapshot/scripts/hooks/snapshot_PreCompact.py"
        in command
    )
    assert "$CLAUDE_PLUGIN_ROOT" not in command
    assert "hooks.json" not in command


def test_expected_settings_command_normalizes_backslashes() -> None:
    command = expected_settings_command("SessionStart")
    # No backslashes — we normalize to forward slashes for cross-platform match.
    assert "\\" not in command
    assert command.startswith('python "P:/')


def test_unknown_hook_name_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown snapshot hook"):
        hook_path("NotARealEvent")
