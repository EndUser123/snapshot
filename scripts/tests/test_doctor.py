"""Tests for scripts/doctor.py."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import doctor


def test_find_hook_command_detects_registered_command() -> None:
    settings = {
        "hooks": {
            "PreCompact": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                'python "P:/packages/.claude-marketplace/plugins/'
                                'snapshot/scripts/hooks/snapshot_PreCompact.py"'
                            ),
                        }
                    ],
                }
            ]
        }
    }
    assert doctor.find_hook_command(settings, "PreCompact", "snapshot_PreCompact.py")


def test_find_hook_command_accepts_backslash_paths() -> None:
    # Use a regular string with doubled backslashes so Python preserves them
    # verbatim. The doctor normalizes command strings to forward slashes
    # before matching, so this is what it should accept.
    backslash_command = (
        "python \"P:\packages\.claude-marketplace\plugins\\"
        "snapshot\scripts\hooks\snapshot_PreCompact.py\""
    )
    settings = {
        "hooks": {
            "PreCompact": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {"type": "command", "command": backslash_command},
                    ],
                }
            ]
        }
    }
    assert doctor.find_hook_command(settings, "PreCompact", "snapshot_PreCompact.py")


def test_find_hook_command_returns_false_when_missing() -> None:
    assert not doctor.find_hook_command({"hooks": {}}, "PreCompact", "snapshot_PreCompact.py")


def test_find_hook_command_returns_false_for_wrong_event() -> None:
    settings = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {"type": "command", "command": "python snapshot_SessionStart.py"},
                    ],
                }
            ]
        }
    }
    assert not doctor.find_hook_command(settings, "PreCompact", "snapshot_PreCompact.py")


def test_settings_have_execute_hook_detects_importer_snippet() -> None:
    settings = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                "python -c \"from __lib.hook_importer import "
                                "HookImporter; HookImporter(...).execute_hook("
                                "'SessionStart', timeout=45.0)\""
                            ),
                        }
                    ],
                }
            ]
        }
    }
    assert doctor._settings_have_execute_hook(settings, "SessionStart")
    assert not doctor._settings_have_execute_hook(settings, "PreCompact")


def test_run_checks_reports_missing_precompact(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"hooks": {}}), encoding="utf-8")

    report = doctor.run_checks(settings_path=settings_path, project_root=tmp_path)

    assert report["checks"]["precompact_registered"]["ok"] is False
    assert "snapshot_PreCompact.py" in report["checks"]["precompact_registered"]["message"]
    assert report["checks"]["sessionstart_registered"]["ok"] is False
    assert report["checks"]["userpromptsubmit_registered"]["ok"] is False


def test_run_checks_reports_missing_settings_gracefully(tmp_path: Path) -> None:
    settings_path = tmp_path / "nonexistent.json"

    report = doctor.run_checks(settings_path=settings_path, project_root=tmp_path)

    assert report["checks"]["settings_loaded"]["ok"] is False
    assert report["checks"]["precompact_registered"]["ok"] is False
    assert report["checks"]["sessionstart_registered"]["ok"] is False
    assert report["checks"]["userpromptsubmit_registered"]["ok"] is False
    assert report["checks"]["artifact_root_writable"]["ok"] is True


def test_run_checks_aggregates_ok_correctly(tmp_path: Path) -> None:
    good_settings = {
        "hooks": {
            "PreCompact": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                'python "P:/packages/.claude-marketplace/plugins/'
                                'snapshot/scripts/hooks/snapshot_PreCompact.py"'
                            ),
                        }
                    ],
                }
            ],
            "SessionStart": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "from hook_importer import execute_hook('SessionStart'",
                        }
                    ],
                }
            ],
            "UserPromptSubmit": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "from hook_importer import execute_hook('UserPromptSubmit'",
                        }
                    ],
                }
            ],
        }
    }
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(good_settings), encoding="utf-8")

    report = doctor.run_checks(settings_path=settings_path, project_root=tmp_path)

    assert report["checks"]["settings_loaded"]["ok"] is True
    assert report["checks"]["precompact_registered"]["ok"] is True
    assert report["checks"]["sessionstart_registered"]["ok"] is True
    assert report["checks"]["userpromptsubmit_registered"]["ok"] is True
    assert report["checks"]["artifact_root_writable"]["ok"] is True
    assert report["ok"] is True
