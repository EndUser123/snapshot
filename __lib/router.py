#!/usr/bin/env python3
"""snapshot router — dispatches to plugin hooks based on event type.

Registered in settings.json. Works around GitHub issue #16288
(plugin hooks.json not loaded from external files).

Usage:
    python router.py <EventName>
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = PLUGIN_ROOT / "scripts/hooks"

PHASE_DIR = {'PreCompact': '', 'SessionStart': '', 'SessionEnd': '', 'UserPromptSubmit': ''}

PRECOMPACT_HOOKS = ['snapshot_PreCompact.py']
SESSIONSTART_HOOKS = ['snapshot_SessionStart.py', 'snapshot_SessionStart_identity_capture.py']
SESSIONEND_HOOKS = ['snapshot_SessionEnd_tldr.py']
USERPROMPTSUBMIT_HOOKS = ['snapshot_UserPromptSubmit.py']

DISPATCH = {
    "PreCompact": PRECOMPACT_HOOKS,
    "SessionStart": SESSIONSTART_HOOKS,
    "SessionEnd": SESSIONEND_HOOKS,
    "UserPromptSubmit": USERPROMPTSUBMIT_HOOKS
}


def _emit_block(out: str, hook_name: str, child_stderr: str = "") -> None:
    """Emit a block on both channels, then exit(2).

    The harness surfaces ONLY stderr for exit-2 blocks; stdout JSON is ignored
    by the harness UI. Without the stderr line the user sees a bare
    "Blocked by hook" with no reason (see blocking_stderr_standard). We still
    print the JSON to stdout for any downstream consumer / logging.
    """
    reason = ""
    if out:
        print(out)
        try:
            parsed = json.loads(out)
            if isinstance(parsed, dict):
                reason = str(parsed.get("reason") or parsed.get("systemMessage") or "").strip()
        except json.JSONDecodeError:
            reason = out.strip()
    if not reason:
        reason = child_stderr.strip() or f"Blocked by {hook_name}"
        if not out:
            print(json.dumps({"decision": "block", "reason": reason}))
    msg = f"BLOCKED [{hook_name}]: {reason}\n"
    try:
        sys.stderr.write(msg)
    except UnicodeEncodeError:
        sys.stderr.buffer.write(msg.encode("utf-8", "replace"))
    sys.exit(2)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(0)

    event = sys.argv[1]
    hooks = DISPATCH.get(event)
    if not hooks:
        sys.exit(0)

    phase = PHASE_DIR.get(event, "")
    input_data = sys.stdin.buffer.read()

    for hook_name in hooks:
        hook_path = HOOKS_DIR / phase / hook_name if phase else HOOKS_DIR / hook_name
        if not hook_path.exists():
            continue

        try:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            result = subprocess.run(
                [sys.executable, str(hook_path)],
                input=input_data,
                capture_output=True,
                timeout=10,
                creationflags=flags,
            )

            if result.returncode == 2:
                out = result.stdout.decode(errors="replace").strip()
                child_stderr = result.stderr.decode(errors="replace")
                _emit_block(out, hook_name, child_stderr)

            out = result.stdout.decode(errors="replace").strip()
            if out:
                try:
                    parsed = json.loads(out)
                    if isinstance(parsed, dict) and parsed.get("decision") == "block":
                        _emit_block(out, hook_name)
                except json.JSONDecodeError:
                    pass
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    print(json.dumps({"decision": "approve"}))


if __name__ == "__main__":
    main()
