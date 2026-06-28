"""Canonical terminal_id for multi-terminal isolation.  (CANONICAL SOURCE)

Single authoritative algorithm. Identical byte-copies live in every plugin
that derives a terminal_id (plugins stay independent; no cross-plugin import).
The generator ``scripts/sync_terminal_id.py`` regenerates every copy from this
file, and ``tests/test_terminal_id_invariants.py`` auto-discovers every copy in
the monorepo and fails on hash drift — listing each path + the sync command.

DO NOT hand-edit copies. Edit THIS file, then run sync_terminal_id.py.

Algorithm priority:
    1. CLAUDE_TERMINAL_ID env var (explicit override)
    2. Per-terminal session env vars:
       WT_SESSION (Windows Terminal), ITERM_SESSION_ID (iTerm2),
       WEZTERM_SESSION_ID (WezTerm), TMUX (tmux)
    3. ConEmuServerPID (Windows ConEmu)
    4. Derived fallback: sha1(os.getppid()) — unique per Claude Code process
       (= per terminal) and stable across hook invocations within the session.

INVARIANT: this function NEVER returns a static/constant id. Every terminal,
window, or app instance gets a unique id. The fallback is derived, not default.
"""

from __future__ import annotations

import hashlib
import os

# Per-terminal session env vars, checked in priority order. Each is a UUID (or
# unique token) set by the terminal emulator and scoped to one terminal window.
_SESSION_ENV_VARS: tuple[str, ...] = (
    "WT_SESSION",        # Windows Terminal
    "ITERM_SESSION_ID",  # iTerm2
    "WEZTERM_SESSION_ID",  # WezTerm
    "TMUX",              # tmux (format: <socket>,<pid>,<session_id>)
)


def canonical_terminal_id() -> str:
    """Return the canonical terminal identifier for this process.

    Never returns a static constant. The fallback is a hash of the parent PID,
    which is unique per terminal session and stable for its lifetime.

    Returns:
        Identifier prefixed with ``console_`` for artifact-path compatibility.
        Example: ``console_081c35fc-2c20-42d8-90ee-fc271a305b8c``
    """
    return canonical_terminal_id_from_env() or _derived_fallback_id()


def canonical_terminal_id_from_env() -> str | None:
    """Env-signal detection only (no derived fallback).

    Returns the ``console_<id>`` from CLAUDE_TERMINAL_ID, a per-terminal session
    env var, or ConEmuServerPID. Returns ``None`` when no terminal env signal is
    present, so a caller can interpose its own recovery (e.g. a registry lookup
    for resumed sessions) before falling back to the derived ppid-hash.

    Like :func:`canonical_terminal_id`, this NEVER returns a static constant —
    ``None`` simply means "no env signal; caller decides the fallback."
    """
    # Priority 1: explicit env override (also used for testing)
    if env_id := os.environ.get("CLAUDE_TERMINAL_ID", "").strip():
        return env_id if env_id.startswith("console_") else f"console_{env_id}"

    # Priority 2: per-terminal session env var (each unique to one terminal)
    for var in _SESSION_ENV_VARS:
        if value := os.environ.get(var, "").strip():
            return f"console_{value}"

    # Priority 3: ConEmu (Windows) — unique per console window
    if conemu_pid := os.environ.get("ConEmuServerPID", "").strip():
        return f"console_conemu_{conemu_pid}"

    return None


def _derived_fallback_id() -> str:
    """Derived fallback — NEVER static.

    os.getppid() is the parent process (Claude Code itself for hooks), stable
    across every invocation in one session and unique per terminal window.
    """
    return f"console_{hashlib.sha1(str(os.getppid()).encode()).hexdigest()[:16]}"
