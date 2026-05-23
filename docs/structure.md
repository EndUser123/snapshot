# Handoff Package Structure

## Architecture Overview

The handoff system uses **symbolic links** to maintain a single source of truth:

- **Source of truth**: `P://packages/handoff/src/handoff/hooks/` (all source code)
- **Symbolic links in**: `P://.claude/hooks/` → point to source files

## Directory Structure

```
P://packages/handoff/
├── src/
│   └── handoff/
│       ├── hooks/                      # Source hook scripts (SOURCE OF TRUTH)
│       │   ├── PreCompact_handoff_capture.py
│       │   └── SessionStart_handoff_restore.py
│       ├── __lib/                      # Core library code
│       │   └── handoff_store.py
│       ├── tests/                      # Test suite
│       │   └── test_handoff_hooks.py
│       └── ... (other modules)
└── HANDOFF_STRUCTURE.md                # This file

P://.claude/hooks/
├── PreCompact_handoff_capture.py       → Symbolic link to package source
└── SessionStart_handoff_restore.py     → Symbolic link to package source
```

## Setup Instructions

### Prerequisites

Symbolic links on Windows require **Administrator privileges** or **Developer Mode**.

#### Option 1: Run as Administrator (Recommended)

1. **Close your current terminal/Claude Code session**
2. **Restart as Administrator**:
   - Right-click on terminal/Claude Code shortcut
   - Select "Run as administrator"
   - Click "Yes" to UAC prompt

#### Option 2: Enable Developer Mode (One-time Setup)

1. **Open Settings** → **Update & Security** → **For developers**
2. **Enable "Developer Mode"**
3. **Restart your terminal** (no admin needed after this)

### Creating the Symbolic Links

After enabling admin privileges or Developer Mode:

```bash
cd P://.claude/hooks
mklink PreCompact_handoff_capture.py ..\..\packages\handoff\src\handoff\hooks\PreCompact_handoff_capture.py
mklink SessionStart_handoff_restore.py ..\..\packages\handoff\src\handoff\hooks\SessionStart_handoff_restore.py
```

Or using PowerShell:
```powershell
cd $CLAUDE_ROOT/hooks
New-Item -ItemType SymbolicLink -Path "PreCompact_handoff_capture.py" -Value "..\..\packages\handoff\src\handoff\hooks\PreCompact_handoff_capture.py"
New-Item -ItemType SymbolicLink -Path "SessionStart_handoff_restore.py" -Value "..\..\packages\handoff\src\handoff\hooks\SessionStart_handoff_restore.py"
```

### Verification

Check that symlinks were created successfully:
```bash
cd P://.claude/hooks
ls -la PreCompact_handoff_capture.py SessionStart_handoff_restore.py
```

You should see output like:
```
PreCompact_handoff_capture.py -> ..\..\packages\handoff\src\handoff\hooks\PreCompact_handoff_capture.py
SessionStart_handoff_restore.py -> ..\..\packages\handoff\src\handoff\hooks\SessionStart_handoff_restore.py
```

## Workflow

### Making Changes to Handoff Hooks

1. **Edit source files** in `P://packages/handoff/src/handoff/hooks/`
2. **Changes are immediately reflected** in `.claude/hooks/` via symlinks
3. **Test** the changes
4. **Commit** the package repository

### Git Workflow

The handoff package has its own git repository at `https://github.com/EndUser123/P.git`.

```bash
# 1. Edit source files in package
# 2. Test changes
cd P://.claude/hooks/tests
python -m pytest test_handoff_hooks.py -v

# 3. Commit to package repository
cd P://packages/handoff
git add src/handoff/hooks/
git commit -m "feat: update handoff hooks for ..."
git push
```

## Troubleshooting

### Symlinks Don't Work

**Problem**: `mklink` command fails with "You do not have sufficient privilege"

**Solution**:
1. Make sure you're running as Administrator, OR
2. Enable Developer Mode in Windows Settings

### Symlinks Broken After Git Clone

**Problem**: Symlinks appear as regular files after cloning the repository

**Solution**: Git for Windows must be configured to create symlinks:
```bash
git config --global core.symlinks true
```

Then re-clone the repository.

### Permission Denied Errors

**Problem**: Hook scripts can't be executed via symlinks

**Solution**: Make sure source files have execute permissions:
```bash
chmod +x P://packages/handoff/src/handoff/hooks/*.py
```

## Why Symbolic Links?

### Advantages

- ✅ **Single source of truth** - All code lives in the package
- ✅ **Immediate changes** - No sync step required
- ✅ **Version control** - Package has its own git repository
- ✅ **Clear separation** - Package = development, `.claude/hooks/` = deployment
- ✅ **No drift** - Can't have out-of-sync copies

### Why Not Other Approaches?

| Approach | Problem |
|----------|---------|
| **Junctions** | Only work for directories, not files |
| **Copy/sync** | Requires manual sync step, easy to forget |
| **Hard links** | Don't work across different drives |

## Migration Notes

This structure was adopted on 2026-03-07 to replace broken symlinks that pointed to non-existent files. The new implementation:
- Requires Administrator privileges or Developer Mode (one-time setup)
- Provides reliable symlink-based architecture
- All source code is versioned in the package repository
- Clear documentation for setup and troubleshooting

## Related Files

- `P://packages/handoff/src/handoff/hooks/PreCompact_handoff_capture.py` - Capture hook
- `P://packages/handoff/src/handoff/hooks/SessionStart_handoff_restore.py` - Restore hook
- `P://.claude/hooks/tests/test_handoff_hooks.py` - Unit tests
