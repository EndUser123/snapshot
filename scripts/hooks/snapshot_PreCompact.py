#!/usr/bin/env python3
"""
PreCompact - Lean Router v2.0
=============================

Replaces monolithic PreCompact_handoff_router.py.
Ensures session continuity by capturing handoff and checkpoint state before compaction.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# Add child hooks to path for import
_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

# Import child hooks
import PreCompact_commitment_tracker as commitments
from __lib.capture_pipeline import capture_snapshot

_log = logging.getLogger(__name__)

# SEQUENCE of run() functions
SEQUENCE = [
    ("capture", capture_snapshot),
    ("commitments", commitments.run),
]

_REQUIRED_INPUT_FIELDS = frozenset({"session_id", "transcript_path", "cwd", "hook_event_name", "trigger"})


def main():
    raw_input = sys.stdin.read().strip()
    if not raw_input:
        sys.exit(0)

    try:
        raw_input = raw_input.lstrip("\ufeff")
        data = json.loads(raw_input)
    except json.JSONDecodeError:
        print(json.dumps({"decision": "block", "reason": "PreCompact: invalid JSON input"}))
        sys.exit(1)

    missing = _REQUIRED_INPUT_FIELDS - set(data.keys())
    if missing:
        reason = f"PreCompact: missing required fields: {', '.join(sorted(missing))}"
        _log.warning(reason)
        print(json.dumps({"decision": "block", "reason": reason}))
        sys.exit(1)

    warnings = []
    for name, run_func in SEQUENCE:
        try:
            result = run_func(data)
            if result:
                # If child hook returns a block, we honor it immediately
                if result.get("decision") == "block":
                    if "additionalContext" not in result:
                        result["additionalContext"] = ""
                    result["additionalContext"] += "\n\n💡 Compaction issue? Run /doctor to check identity health."
                    print(json.dumps(result))
                    sys.exit(1)
                
                # Otherwise, accumulate as warning/context
                warnings.append((name, result))
        except Exception as e:
            _log.error(f"PreCompact child hook '{name}' crashed: {e}", exc_info=True)
            warnings.append((name, {
                "decision": "approve",
                "reason": f"PreCompact child hook '{name}' failed but compaction continues: {e}",
                "additionalContext": (
                    f"Snapshot PreCompact child '{name}' failed but compaction continues: {e}"
                ),
            }))

    # Summarize results
    if warnings:
        # Honor the first child hook's additional context if it exists
        final_output = warnings[0][1]
        
        # Merge reasons from other warnings
        if len(warnings) > 1:
            reasons = [w[1].get("reason", w[0]) for w in warnings]
            final_output["reason"] = " + ".join(reasons)

        # ponytail: "approve" is invalid schema — on allow emit only the
        # context payload (see hooks/CLAUDE.md Hook Output Formats).
        if final_output.get("decision") == "approve":
            final_output = {
                k: v for k, v in final_output.items() if k == "additionalContext"
            }
        print(json.dumps(final_output, indent=2))
    else:
        print(json.dumps({}))

    sys.exit(0)


if __name__ == "__main__":
    main()
