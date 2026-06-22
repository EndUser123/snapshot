#!/usr/bin/env python3
"""ck — Compact with explicit keep instructions for external LLM compatibility.

Usage:
    /ck                                      # Default keep items
    /ck "key decisions, file changes"        # Custom keep items

Default keep items:
    key decisions, file changes, open tasks, verbatim recent quotes

This command ensures reliable compaction when using external LLM providers
(minimax, zai/GLM) that may not handle Claude Code's auto-compaction prompts
correctly.
"""
import sys

DEFAULT_KEEP = "key decisions, file changes, open tasks, verbatim recent quotes"

def main():
    keep_items = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_KEEP
    print(f"/compact Keep: [{keep_items}]")

if __name__ == "__main__":
    main()