import json
import logging
import os
import sys
from pathlib import Path

# Add current directory to path for imports
_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

# Import child hooks
import SessionStart_tldr as tldr
import snapshot_SessionStart_identity_capture as identity_capture
from scripts.hooks.__lib.restore_pipeline import restore_snapshot

_log = logging.getLogger(__name__)

# SEQUENCE of run() functions
SEQUENCE = [
    ("restore", restore_snapshot),
    ("tldr", tldr.run),
    ("identity_capture", identity_capture.run),
]

def main():
    raw_input = sys.stdin.read().strip()
    if not raw_input:
        sys.exit(0)

    try:
        data = json.loads(raw_input.lstrip("\ufeff"))
    except json.JSONDecodeError:
        # Log malformed JSON before exiting to avoid blocking session start
        print("[snapshot_SessionStart] Malformed session JSON, skipping restore", file=sys.stderr)
        sys.exit(0)

    results = []
    for name, run_func in SEQUENCE:
        try:
            result = run_func(data)
            if result:
                if result.get("decision") in ("deny", "error"):
                    if "additionalContext" not in result:
                        result["additionalContext"] = ""
                    result["additionalContext"] += "\n\n💡 Problems starting session? Run /doctor"
                results.append(result)
        except Exception as e:
            _log.error(f"SessionStart child hook '{name}' crashed: {e}", exc_info=True)

    # Summarize results
    if results:
        # Take the first non-None result (usually the restore message)
        # Note: SessionStart_tldr returns None because it prints directly to stdout
        print(json.dumps(results[0], indent=2))
    
    sys.exit(0)

if __name__ == "__main__":
    main()
