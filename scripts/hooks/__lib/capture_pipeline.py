"""Capture pipeline facade for the snapshot plugin.

This module exists so the router entrypoint (snapshot_PreCompact.py) can
import a stable, namespaced capture function rather than depending directly
on the implementation module. The facade keeps the router's import surface
narrow and gives us a single place to swap in a different capture strategy
later without editing the router.
"""

from __future__ import annotations

from typing import Any

from scripts.hooks import PreCompact_snapshot_capture


def capture_snapshot(input_data: dict[str, Any]) -> dict[str, Any]:
    """Run the PreCompact capture pipeline against ``input_data``.

    Thin wrapper around ``PreCompact_snapshot_capture.run`` so the router
    references ``capture_pipeline.capture_snapshot`` instead of importing
    the implementation module directly.
    """
    return PreCompact_snapshot_capture.run(input_data)
