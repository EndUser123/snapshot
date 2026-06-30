#!/usr/bin/env python3
"""Smoke-launch both snapshot router entry points with a real payload.

Why this exists (the regression it guards):
  terminal_detection.py is imported under two incompatible sys.path contexts.
  A prior fix made its line-19 import relative, which resolved in the
  PreCompact chain but crashed the SessionStart chain with
  `ImportError: attempted relative import with no known parent package`
  at import time of snapshot_SessionStart_identity_capture.py.

  Existing tests (test_router_smoke, test_handoff_hooks) verify settings
  registration and exercise child modules in-process, but none actually
  LAUNCH the router entry scripts — so they bypass the real sys.path
  bootstrap and are blind to import-chain regressions at the entry point.

  This test closes that hole: it subprocess-launches each entry point with
  a minimal payload and asserts the import chain resolves and main() runs
  to a clean exit. Pre-fix, test_sessionstart_entry_imports_clean would fail
  with the ImportError above.

Layer justification: this is a boundary/integration test (process launch +
real sys.path bootstrap), not a unit test. A unit test importing the modules
in-process would NOT catch this defect — the bug only manifests under the
entry script's own sys.path setup. Use the real smoke proof here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"

# Use a nonexistent transcript so the hook takes its "skipped / envelope
# invalid" path (exit 0). We are testing that the IMPORT CHAIN resolves and
# main() runs — not that a real snapshot is captured.
_MISSING_TRANSCRIPT = "C:/tmp/__entrypoint_smoke_does_not_exist__.jsonl"


def _run_entry(entry_script: str, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / entry_script)],
        input=__import__("json").dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _assert_import_chain_resolved(result: subprocess.CompletedProcess, entry: str) -> None:
    stderr = result.stderr or ""
    assert "ModuleNotFoundError" not in stderr, (
        f"{entry}: import chain failed (ModuleNotFoundError) in stderr:\n{stderr}"
    )
    assert "ImportError" not in stderr, (
        f"{entry}: import chain failed (ImportError) in stderr:\n{stderr}"
    )
    assert result.returncode == 0, (
        f"{entry}: exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{stderr}"
    )


def test_precompact_entry_imports_clean() -> None:
    result = _run_entry(
        "snapshot_PreCompact.py",
        {
            "session_id": "smoke_pc",
            "transcript_path": _MISSING_TRANSCRIPT,
            "cwd": "P:/",
            "hook_event_name": "PreCompact",
            "trigger": "manual",
        },
    )
    _assert_import_chain_resolved(result, "snapshot_PreCompact.py")


def test_sessionstart_entry_imports_clean() -> None:
    result = _run_entry(
        "snapshot_SessionStart.py",
        {
            "session_id": "smoke_ss",
            "transcript_path": _MISSING_TRANSCRIPT,
            "cwd": "P:/",
            "hook_event_name": "SessionStart",
            "source": "startup",
        },
    )
    _assert_import_chain_resolved(result, "snapshot_SessionStart.py")
