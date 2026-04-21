---
name: ruff and pytest live in .venv/bin, not system PATH
description: Use /Users/entrada-mac/repos/mortgage-intelligence-platform/.venv/bin/ruff and .venv/bin/pytest explicitly. System python3 (3.14) does not have these packages installed.
type: reference
---

Run validation commands via the venv directly:
- `/Users/entrada-mac/repos/mortgage-intelligence-platform/.venv/bin/ruff check backend tests tools`
- `/Users/entrada-mac/repos/mortgage-intelligence-platform/.venv/bin/pytest -q`

`which ruff` returns nothing. `python3 -m ruff` fails with ModuleNotFoundError. `python3 -m pytest` may also fail. The only reliable path for these tools is the repo's `.venv/bin/` directory.

For running ad-hoc Python scripts (e.g. `python3 jobs/fred_rates_ingest.py --mode=seed --dry-run`), system python3 works fine as long as the script only uses stdlib.
