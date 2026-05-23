# ADR 001: Brownfield Conversion - Python Library to Claude Code Plugin

**Status:** Accepted
**Date:** 2026-03-09
**Context:** handoff package - Session state capture and restoration system

## Context

The handoff package was originally implemented as a **Python library** with:
- `src/handoff/` source layout
- `pyproject.toml` for pip installation
- `pip install -e packages/handoff/` for development
- Manual hook file symlinks to `~/.claude/hooks/`

We needed to decide between:
1. **Keep as Python library** - Maintain pip installation, add hooks manually
2. **Convert to Claude Code Plugin** - Migrate to plugin architecture with auto-discovery

## Decision

We chose **brownfield conversion to Claude Code Plugin**.

### Rationale

| Factor | Python Library | Claude Code Plugin |
|--------|----------------|-------------------|
| **Installation** | `pip install -e` | `/plugin` or junction |
| **Hook registration** | Manual symlinks | Auto via hooks.json |
| **Path references** | Hardcoded or sys.path | `CLAUDE_PLUGIN_ROOT` |
| **Discovery** | Not discoverable | Auto-discovered |
| **Updates** | Requires reinstall | Live editing with junction |
| **Distribution** | PyPI only | Marketplace + GitHub |
| **Dependencies** | pip manages | Plugin manages (no pip) |

### Implementation

**Directory Structure Migration:**
```
# BEFORE (Python library)
src/handoff/
├── __init__.py
├── hooks/
│   ├── __lib/
│   └── *.py
pyproject.toml

# AFTER (Claude Code Plugin)
core/
├── __init__.py
├── hooks/
│   ├── __lib/
│   └── *.py
.claude-plugin/
├── plugin.json
hooks/
└── hooks.json
```

**Import Path Changes:**
```python
# BEFORE
from handoff.hooks.__lib import handoff_store

# AFTER
from core.hooks.__lib import handoff_store
```

**Hook Configuration (hooks/hooks.json):**
```json
{
  "PreCompact": [{
    "matcher": ".*",
    "hooks": [{
      "type": "command",
      "command": "python \"$CLAUDE_PLUGIN_ROOT/core/hooks/PreCompact_handoff_capture.py\""
    }]
  }],
  "SessionStart": [{
    "matcher": ".*",
    "hooks": [{
      "type": "command",
      "command": "python \"$CLAUDE_PLUGIN_ROOT/core/hooks/SessionStart_handoff_restore.py\""
    }]
  }]
}
```

**Local Development Setup:**
```powershell
# Windows - Create junction once
New-Item -ItemType Junction -Path "C:\Users\brsth\.claude\plugins\handoff" -Target "P://packages/handoff"

# Reload Claude Code
/reload
```

## Migration Steps

1. **Backup existing structure** → `.backup/` directory
2. **Migrate source code** → `src/handoff/` → `core/`
3. **Create plugin metadata** → `.claude-plugin/plugin.json`
4. **Configure hooks** → `hooks/hooks.json` with `CLAUDE_PLUGIN_ROOT`
5. **Update all imports** → `from handoff.` → `from core.`
6. **Fix path references** → Remove hardcoded paths, use `CLAUDE_PLUGIN_ROOT`
7. **Update tests** → Change import paths and test fixtures
8. **Update documentation** → README with plugin installation instructions
9. **Create local dev junction** → Link to `~/.claude/plugins/local/`
10. **Verify** → Run `/reload` and test hook execution

## Trade-offs

### Pros
- **Auto-discovery** - Claude Code finds plugin automatically
- **Portable paths** - `CLAUDE_PLUGIN_ROOT` works across installations
- **Live editing** - Code changes take effect immediately
- **Better DX** - No manual hook management or pip installs
- **Future-proof** - Aligned with Claude Code plugin ecosystem
- **Simpler distribution** - No pip/PyPI complexity

### Cons
- **Migration cost** - Required restructuring and import updates
- **Not pip-installable** - Can't use standard Python packaging
- **New pattern** - Plugin architecture less familiar than pip
- **Platform-specific setup** - Junctions/symlinks for local dev

## Breaking Changes

### For Users
- **Installation method changed:**
  ```bash
  # OLD
  pip install -e packages/handoff/

  # NEW
  /plugin P://packages/handoff
  # OR (local dev)
  New-Item -ItemType Junction -Path "~/.claude/plugins/handoff" -Target "P://packages/handoff"
  ```

- **Import paths changed in dependent code:**
  ```python
  # OLD
  from handoff import HandoffStore

  # NEW
  from core import HandoffStore
  ```

### For Developers
- **Removed files:** `pyproject.toml`, `src/` directory
- **New files:** `.claude-plugin/plugin.json`, `hooks/hooks.json`
- **Changed paths:** All imports updated from `handoff.` to `core.`

## Verification

**Pre-migration:**
- ✅ 180 tests collecting with old structure
- ✅ Manual hook symlinks to `~/.claude/hooks/`

**Post-migration:**
- ✅ 180 tests collecting with new structure
- ✅ Plugin discovered via junction
- ✅ Hooks auto-registered via `hooks.json`
- ✅ `CLAUDE_PLUGIN_ROOT` set correctly
- ✅ No import errors in tests

## Rollback

Backup preserved at `.backup/` with original structure:
```bash
# Rollback if needed
cp -r .backup/* .
rm -rf core/ .claude-plugin/ hooks/hooks.json
```

## Related Decisions

- ADR 001 (package skill): Plugin Local Development via Junctions/Symlinks
- ADR 002 (handoff): Task Tracker for Multi-Terminal Isolation (pending)

## References

- Brownfield Conversion Guide: `references/brownfield-conversion.md`
- Plugin Architecture: `.claude-plugin/plugin.json`
- Hook Configuration: `hooks/hooks.json`
- Migration Evidence: `.backup/` directory with preserved original structure
