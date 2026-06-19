"""Restore pipeline facade for the snapshot plugin.

This module exists so the router entrypoint (snapshot_SessionStart.py) can
import a stable, namespaced restore function rather than depending directly
on the implementation module. The facade keeps the router's import surface
narrow and gives us a single place to swap in a different restore strategy
later without editing the router.
"""

from __future__ import annotations

from typing import Any

import SessionStart_snapshot_restore


def restore_snapshot(input_data: dict[str, Any]) -> dict[str, Any]:
    """Run the SessionStart restore pipeline against ``input_data``.

    Thin wrapper around ``SessionStart_snapshot_restore.run`` so the router
    references ``restore_pipeline.restore_snapshot`` instead of importing
    the implementation module directly.
    """
    return SessionStart_snapshot_restore.run(input_data)
