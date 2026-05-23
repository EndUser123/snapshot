---
name: track
description: Track work-in-progress across terminals and sessions. Never lose your place — surface what you were doing, how far you got, and what's next. Each terminal is fully isolated and reads only its own state.
version: "1.0.0"
status: stable
category: workflow
triggers:
  - /track
---

# /track — Work Thread Tracker

**Problem solved:** "I have 10 terminals open and after a few sessions I get lost on what was happening where."

## Core Concepts

- **Work Thread**: A named unit of work scoped to a single terminal. Can be as small as "fix JWT refresh bug" or as large as "rewrite auth system."
- **Thread ID**: Unique per thread, derived from intent text SHA256 hash.
- **Thread storage**: `~/.claude/track/threads_<terminal-id>/<thread-id>.json` — each terminal has its own isolated thread storage.
- **Current thread pointer**: `~/.claude/track/current_<terminal-id>.txt` — terminal-scoped, ensures multi-terminal isolation.
- **Reconstruction**: If no thread is active, `/track` reads only this terminal's `/term` context file to rebuild state.

## Commands

### `/track` (no args)
Show catch-up brief for the current work thread — "Last time you worked on this, you were X. Got as far as Y. Next was Z. Blocker: W."

If no thread is active, reconstruct from session history (last session's goals/intent) or prompt to start a new thread.

### `/track brief`
Same as `/track` — catch-up brief for current thread.

### `/track "working on <intent>"`
Start or update a work thread. Creates thread if new intent, merges with existing if same thread.

### `/track next "<next step>"`
Update the next-step field of the current thread.

### `/track done "<checkpoint>"`
Update the checkpoint — what was accomplished.

### `/track blocker "<blocker>"`
Record what's blocking progress.

### `/track list`
Show all work threads for this terminal, most recently active first. Each thread shows: intent, last activity, checkpoint summary.

### `/track info`
Full detail for current thread: intent, checkpoint, next, blocker, terminal, files modified, timestamps.

### `/track done`
Mark current thread as complete (archives it).

### `/track archive`
Alias for `/track done` — mark current thread as complete.

## Storage

**Thread storage**: `~/.claude/track/threads_<terminal-id>/` (per-terminal, fully isolated)
**Current thread pointer**: `~/.claude/track/current_<terminal-id>.txt` (terminal-scoped)

**Thread file** (`~/.claude/track/threads_<terminal-id>/<thread-id>.json`):
```json
{
  "thread_id": "abc123",
  "intent": "implement JWT refresh",
  "checkpoint": "token refresh logic written, not tested",
  "next_step": "write tests for refresh token",
  "blocker": "need test fixtures",
  "terminal_id": "console_abc123",
  "last_activity": 1742659200,
  "created_at": 1742650000,
  "files_modified": ["auth.py", "token.py"],
  "archived": false
}
```

## Session Registry Integration

The session registry (`P://.claude/.artifacts/session_registry.jsonl`) provides cross-terminal session history from PreCompact captures.

### `/track sessions`
Show recent compaction history across all terminals:

```bash
python -c "
import sys, os
sys.path.insert(0, os.path.join(os.environ.get('CLAUDE_PLUGIN_ROOT', ''), 'scripts', 'hooks', '__lib'))
from scripts.hooks.__lib.session_registry import query_registry
entries = query_registry(limit=20)
if not entries:
    sys.exit(0)
for e in entries:
    ts = e.get('ts','?')[:19].replace('T',' ')
    tid = e.get('terminal_id','?')[-8:]
    goal = e.get('goal','')[:50]
    pct = e.get('progress_percent', '?')
    print(f'{ts}  [{tid}]  {goal}  ({pct}%)')
"
```

Entries are appended on each compaction. `handoff_path` is a hint (file may have been cleaned up by retention policy). Always check file existence before reading.

### `/track sessions --terminal <id>`
Filter to a specific terminal. Pass `terminal_id` to `query_registry()`:

```bash
python -c "
import sys, os
sys.path.insert(0, os.path.join(os.environ.get('CLAUDE_PLUGIN_ROOT', ''), 'scripts', 'hooks', '__lib'))
from scripts.hooks.__lib.session_registry import query_registry
tid = sys.argv[1] if len(sys.argv) > 1 else f'console_{os.environ.get(\"WT_SESSION\",\"\")}'
entries = query_registry(terminal_id=tid, limit=20)
if not entries:
    sys.exit(0)
for e in entries:
    ts = e.get('ts','?')[:19].replace('T',' ')
    goal = e.get('goal','')[:50]
    pct = e.get('progress_percent', '?')
    print(f'{ts}  {goal}  ({pct}%)')
" "<terminal_id>"
```

## Reconstruction Logic (when no thread is active)

1. Read `~/.claude/terminals/<terminal-id>.json` from `/term` skill (this terminal only)
2. If nothing found, query session registry via `query_registry(terminal_id=tid, limit=1)` for this terminal's last entry
3. If still nothing, prompt user to start a thread with `/track "working on..."`

## Multi-Terminal Isolation

- **Thread storage is terminal-scoped**: `threads_<terminal-id>/` — each terminal has its own isolated thread storage, invisible to all other terminals
- **Current thread pointer is terminal-scoped**: `current_<terminal-id>.txt` — each terminal has its own active thread, never overwritten by other terminals
- **Reconstruction is terminal-scoped**: only reads the current terminal's `/term` file, never shared session state
- Thread files named by intent SHA256 hash — same intent text = same thread within a terminal
