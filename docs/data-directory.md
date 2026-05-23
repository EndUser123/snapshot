# Handoff Data Directory

This document explains handoff data stored at `.claude/handoffs/`.

## What Are Handoffs?

Handoffs are snapshots of conversation state that include:
- Task name and progress
- Next steps to continue work
- Active files being worked on
- Git branch information
- Handover notes and decisions

**Renamed from "checkpoint" to "handoff"** to avoid naming conflicts with Claude Code's built-in checkpoint system.

## File Naming Convention

```
task_name__terminal_id__version.json
```

Examples:
- `implement-auth__term_abc123__latest.json` - Symlink to most recent version
- `implement-auth__term_abc123__v1.json` - First version
- `implement-auth__term_abc123__v2.json` - Second version

## Storage Location

Configured via environment variable:
- `HANDOFF_PROJECT_ROOT` - Defaults to `P://`
- Data stored at: `$PROJECT_ROOT/.claude/handoffs/`

## Management

### List Handoffs
```bash
handoff list
```

### Clean Up Old Handoffs
```bash
# Remove handoffs older than 3 days (default)
handoff cleanup

# Remove handoffs older than 7 days
handoff cleanup --max-age 7
```

### Delete Specific Handoff
```bash
handoff delete task_name__terminal_id
```

## Environment Variables

| Old (checkpoint) | New (handoff) |
|-----------------|----------------|
| `CHECKPOINT_PROJECT_ROOT` | `HANDOFF_PROJECT_ROOT` |
| `CHECKPOINT_DIR` | `HANDOFF_DIR` |
| `.claude/checkpoints/` | `.claude/handoffs/` |

## Migration from Checkpoint

If you have existing checkpoint data, migrate using:

```bash
# Install handoff package
pip install -e P://packages/handoff/

# Rename data directory
mv P:///.claude/checkpoints P:///.claude/handoffs

# Update environment variables in shell config
# Replace CHECKPOINT_* with HANDOFF_*
```

## Architecture

```
.claude/
├── handoffs/           # Handoff data (JSON files)
│   ├── trash/            # Deleted handoffs (recoverable)
│   └── *.json            # Handoff files
└── settings.json         # Hook configuration

packages/handoff/       # Handoff package
├── src/handoff/       # Implementation
├── docs/                 # Documentation
└── README.md             # Package readme
```

## Hooks

- `SessionStart_handoff_restore.py` - Restores handoff on session start
- `PreCompact_handoff_capture.py` - Captures handoff before compaction

## Related Documentation

- **Package README:** `P://packages/handoff/README.md`
- **API Reference:** `P://packages/handoff/docs/API.md`
- **User Guide:** `P://packages/handoff/docs/user-guide.md`
