#!/usr/bin/env python3
"""Lightweight Claude Code hook.

This intentionally does not run tests on every write. It prints the project's validation
checklist so the agent stays disciplined without slowing down each edit.
"""
print("MIP reminder: after this slice, run the narrowest relevant check, then `python tools/verify_scaffold.py`, `pytest -q`, and/or `npm --prefix frontend run build`.")
