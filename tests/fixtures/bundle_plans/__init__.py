"""``bundle plan -o json`` documents rebuilt from the shapes spikes S9 and S10 measured.

Both spikes ran CLI v1.10.0 (direct engine) on throwaway bundle roots. None of
these files is a capture: each is rebuilt synthetically, and its values are
placeholders. What follows the measured documents is the structure: the
fields present and absent, their JSON types, list lengths, map keys (all
databricks.yml's), ``depends_on`` nodes and labels, ``new_state.vars`` paths
and references, change paths and reasons, and the all-caps API enum tokens.

* ``fresh_root_create.json``: S10, the whole bundle planned on a fresh root,
  as the first-client cutover's 10B-1 parses it first. 18 creates over the 8
  declared kinds, no ``lineage`` and no ``serial``, 22 ``depends_on`` edges
  on 11 entries (label ``${<node>.<field>}``), ``vars`` on 10 entries (71
  paths, 8 references: the reference string itself in a string field, ``0``
  in an int field). Every other string is ``x``, every other int 1, every
  bool false; names are databricks.yml's literals or ``synthetic-*``, and the
  dashboards' parent is the fixture deployer's root (bundle_plan_fixtures).
* ``experiment_*.json``: S9, one experiment key. ``create`` on a fresh root;
  ``skip`` with ``remote_state`` and skipped changes (reasons
  ``backend_default`` and ``terraform_compat``, the indexed path
  ``tags[0].value``, no ``new_state``); ``update`` of the name with no reason
  (bound while the live name was the development-mode form); ``recreate``
  (reason ``immutable``: a declared ``artifact_location``); and ``unbound``
  (``create`` with the state's lineage and serial kept).

No id is real, no run of ten digits appears, and gitleaks finds nothing in
them. A raw live plan is never committed: it embeds the deployer's root path,
resolved names and remote state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).resolve().parent
NAMES = (
    "fresh_root_create",
    "experiment_create",
    "experiment_skip",
    "experiment_update",
    "experiment_recreate",
    "experiment_unbound",
)


def text(name: str) -> str:
    """The document as ``bundle plan -o json`` prints it."""

    return (FOLDER / f"{name}.json").read_text(encoding="utf-8")


def load(name: str) -> dict[str, Any]:
    return json.loads(text(name))
