"""The App's optional resource bindings, which the client profile switches on (spike S2b).

``databricks.yml`` declares every resource binding the App can use, the three
Salesforce connector secrets among them, and stays their one definition. A
governed release applies a declared binding only when the profile says the
App runs on it: the Salesforce ones only when ``MIP_SALESFORCE_BINDINGS`` is
``on``. Unset is ``off``, so an install whose profile never names the
connector gains no binding it does not use, and client #1's live set (no
Salesforce binding) is the default.

Why it matters (spikes S2 and S2b, measured on throwaway Apps):

* every App update that carries resources grants CREATE ON DATABASE to the
  App's Lakebase role again, so three bindings an App never reads would cost
  a WR1 window and the migration job's revoke for nothing;
* a binding to a secret key the scope does not hold refuses the whole update
  atomically;
* an ``app.yaml``/payload ``valueFrom`` that names an unbound resource does
  NOT fail the deployment: the App starts with that env var unset. So the App
  payload never names a Salesforce binding the switch leaves off
  (``tools/databricks/app_deploy_payload.py``).

Every reader takes the same answer: the binding payload the release applies
(``app_resource_bindings build --salesforce-bindings``), the adopt phase's
reviewed bindings, and the App payload's Salesforce env.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

SWITCH_ENV = "MIP_SALESFORCE_BINDINGS"
SWITCH_VALUES = ("off", "on")
# The bundle's binding names for the three Salesforce connector secrets.
SALESFORCE_BINDINGS = frozenset(
    {"salesforce_client_secret", "salesforce_password", "salesforce_security_token"}
)


def salesforce_bindings_on(value: str | None) -> bool:
    """The switch: unset or ``off`` is off, ``on`` is on; anything else refuses."""

    text = (value or "").strip()
    if text in {"", "off"}:
        return False
    if text == "on":
        return True
    raise ValueError(f"{SWITCH_ENV} must be off or on")


def salesforce_bindings_from_env(environ: Mapping[str, str] | None = None) -> bool:
    """The switch as the deploy exported it (the profile's field; unset is off)."""

    return salesforce_bindings_on((os.environ if environ is None else environ).get(SWITCH_ENV))


def select_bindings(bindings: list[Any], *, salesforce: bool) -> list[Any]:
    """The declared bindings the profile applies: the Salesforce ones only when switched on.

    A bundle that declares only part of the reviewed Salesforce set refuses
    either way, so a switch never applies a partial connector.
    """

    names = {_name(item) for item in bindings}
    declared = names & SALESFORCE_BINDINGS
    if declared and declared != SALESFORCE_BINDINGS:
        raise ValueError("the bundle declares only part of the reviewed Salesforce bindings")
    if salesforce and not declared:
        raise ValueError(f"{SWITCH_ENV}=on but the bundle declares no Salesforce binding")
    if salesforce:
        return list(bindings)
    return [item for item in bindings if _name(item) not in SALESFORCE_BINDINGS]


def _name(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or "")
    return str(getattr(item, "name", "") or "")
