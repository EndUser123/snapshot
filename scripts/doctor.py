"""Snapshot plugin runtime doctor.

Diagnoses whether the snapshot plugin is correctly wired into the Claude Code
session lifecycle. Exits 0 when all checks pass, 1 otherwise.

Checks:
  - settings_loaded: P:/.claude/settings.json is readable and parses as JSON.
  - precompact_registered: settings.json registers snapshot_PreCompact.py.
  - sessionstart_registered: snapshot SessionStart router or global HookImporter.
  - userpromptsubmit_registered: same dual acceptance for UPS.
  - artifact_root_writable: P:/.claude/.artifacts/ exists and is writable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS_PATH = Path("P:/.claude/settings.json")
DEFAULT_PROJECT_ROOT = Path("P:")


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_load_error": f"{type(exc).__name__}: {exc}"}


def find_hook_command(settings: dict[str, Any], event_name: str, filename: str) -> bool:
    for matcher in settings.get("hooks", {}).get(event_name, []):
        for hook in matcher.get("hooks", []):
            command = hook.get("command", "")
            if isinstance(command, str) and filename in command.replace("\\", "/"):
                return True
    return False


def _settings_have_execute_hook(settings: dict[str, Any], event_name: str) -> bool:
    snippet = f"execute_hook('{event_name}'"
    for matcher in settings.get("hooks", {}).get(event_name, []):
        for hook in matcher.get("hooks", []):
            command = hook.get("command", "")
            if isinstance(command, str) and snippet in command:
                return True
    return False


def _check_settings_loaded(settings: dict[str, Any]) -> dict[str, Any]:
    if "_load_error" in settings:
        return {"ok": False, "message": settings["_load_error"]}
    return {"ok": True, "message": "settings loaded"}


def _check_writable(path: Path) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".snapshot_doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"ok": True, "message": f"writable: {path}"}
    except Exception as exc:
        return {"ok": False, "message": f"not writable: {path}: {exc}"}


def run_checks(
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    project_root: Path = DEFAULT_PROJECT_ROOT,
) -> dict[str, Any]:
    settings = load_json(settings_path)
    state_root = project_root / ".claude" / ".artifacts"
    checks = {
        "settings_loaded": _check_settings_loaded(settings),
        "precompact_registered": {
            "ok": find_hook_command(settings, "PreCompact", "snapshot_PreCompact.py"),
            "message": "expected PreCompact command containing snapshot_PreCompact.py",
        },
        "sessionstart_registered": {
            "ok": (
                find_hook_command(settings, "SessionStart", "snapshot_SessionStart.py")
                or _settings_have_execute_hook(settings, "SessionStart")
            ),
            "message": "expected SessionStart snapshot router or global importer",
        },
        "userpromptsubmit_registered": {
            "ok": (
                find_hook_command(settings, "UserPromptSubmit", "snapshot_UserPromptSubmit.py")
                or _settings_have_execute_hook(settings, "UserPromptSubmit")
            ),
            "message": "expected UserPromptSubmit snapshot router or global importer",
        },
        "artifact_root_writable": _check_writable(state_root),
    }
    return {
        "ok": all(item["ok"] for item in checks.values()),
        "checks": checks,
    }


def main() -> int:
    report = run_checks()
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
