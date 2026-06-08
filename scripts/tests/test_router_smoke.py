"""Live settings + smoke tests for the snapshot plugin router contract.

Problem: The snapshot plugin's PreCompact hook activation is missing from
P:/.claude/settings.json. When that registration is absent, every Claude
session loses its pre-compaction capture, which silently breaks session
continuity across compactions.

Situation: There are three event keys that must be reachable in live settings:
PreCompact (via explicit `python "..."` command), SessionStart (via the
snapshot router OR the global HookImporter), and UserPromptSubmit (same).
Drift in either direction — a missing matcher, or the snapshot entry being
replaced — is exactly the failure mode this test guards against.

Symptom the tests catch:
- `test_precompact_registered_in_live_settings` — fails if the matcher is
  absent or the command path drifted.
- `test_sessionstart_uses_snapshot_router_or_global_importer` — fails if
  the HookImporter command was edited out.
- `test_userpromptsubmit_uses_snapshot_router_or_global_importer` — same,
  for the UPS path.
- `test_precompact_child_exception_fails_open` — fails if the router still
  exits with decision=block on a child crash (defeats the whole purpose of
  the fail-open fix in snapshot_PreCompact.py).
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

from scripts.hooks.__lib.runtime_contract import expected_settings_command


SETTINGS_PATH = Path("P:/.claude/settings.json")


def _commands_for_event(settings: dict, event_name: str) -> list[str]:
    """Walk the nested hooks.<event>[*].hooks[*].command structure.

    Returns command strings with backslashes normalized to forward slashes.
    """
    commands: list[str] = []
    for matcher in settings.get("hooks", {}).get(event_name, []):
        for hook in matcher.get("hooks", []):
            command = hook.get("command")
            if isinstance(command, str):
                commands.append(command.replace("\\", "/"))
    return commands


def test_precompact_registered_in_live_settings() -> None:
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    commands = _commands_for_event(settings, "PreCompact")
    expected = expected_settings_command("PreCompact").replace("\\", "/")
    assert expected in commands, (
        f"PreCompact matcher missing or command path drifted.\n"
        f"Expected to find: {expected}\n"
        f"Actual PreCompact commands: {commands}"
    )


def test_sessionstart_uses_snapshot_router_or_global_importer() -> None:
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    commands = _commands_for_event(settings, "SessionStart")
    joined = "\n".join(commands)
    assert (
        "snapshot_SessionStart.py" in joined
        or "execute_hook('SessionStart'" in joined
    ), f"SessionStart must reach the snapshot module. Commands: {commands}"


def test_userpromptsubmit_uses_snapshot_router_or_global_importer() -> None:
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    commands = _commands_for_event(settings, "UserPromptSubmit")
    joined = "\n".join(commands)
    assert (
        "snapshot_UserPromptSubmit.py" in joined
        or "execute_hook('UserPromptSubmit'" in joined
    ), f"UserPromptSubmit must reach the snapshot module. Commands: {commands}"


# ---------------------------------------------------------------------------
# PreCompact fail-open behavior
# ---------------------------------------------------------------------------


def _load_precompact_router():
    """Load snapshot_PreCompact.py as an isolated module so the test can
    monkeypatch its SEQUENCE without polluting sys.modules under the real name.
    """
    path = Path(
        "P:/packages/.claude-marketplace/plugins/snapshot/scripts/hooks/snapshot_PreCompact.py"
    )
    spec = importlib.util.spec_from_file_location("snapshot_precompact_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _stub_stdin(payload: dict) -> io.StringIO:
    return io.StringIO(json.dumps(payload))


def test_precompact_child_exception_fails_open() -> None:
    """A child crash must NOT block compaction. The router should append the
    error to its warnings list and return decision=approve so the compaction
    proceeds without snapshot capture.
    """
    router = _load_precompact_router()

    def raises(_data):
        raise RuntimeError("simulated child failure")

    # Replace SEQUENCE with a single broken child.
    router.SEQUENCE = [("broken", raises)]

    payload = {
        "session_id": "test-session",
        "transcript_path": "P:/tmp/nonexistent.jsonl",
        "cwd": "P:/tmp",
        "hook_event_name": "PreCompact",
        "trigger": "manual",
    }

    # Stub stdin/stdout around main() so we can capture its JSON output.
    saved_stdin, saved_stdout = sys.stdin, sys.stdout
    try:
        sys.stdin = _stub_stdin(payload)
        sys.stdout = io.StringIO()
        try:
            router.main()
        except SystemExit as exc:
            # main() exits 0 on the happy fail-open path; any other code is a bug.
            assert exc.code == 0, f"PreCompact should exit 0 on child failure, got {exc.code!r}"
        output = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = saved_stdin, saved_stdout

    # The router prints JSON to stdout; the post-loop block uses indent=2
    # so the output may span multiple lines. Join all captured lines so the
    # test parses the full JSON document, not just the trailing brace.
    captured = "".join(output.splitlines())
    assert captured.strip(), f"PreCompact produced no stdout. Captured: {output!r}"
    result = json.loads(captured)

    assert result.get("decision") == "approve", (
        f"Expected decision=approve after child failure, got {result!r}"
    )
    assert "broken" in result.get("reason", ""), (
        f"Reason should mention the failing child name, got {result!r}"
    )
    assert "failed but compaction continues" in result.get("additionalContext", ""), (
        f"additionalContext should explain the fail-open contract, got {result!r}"
    )
