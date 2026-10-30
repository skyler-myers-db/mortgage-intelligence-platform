"""The install policy a client profile decides (M1-PR3).

Every per-install policy the deploy applies comes from the profile's own
field, never from a bundle target name or from the install class standing in
for one:

* ``app_env``: ``MIP_APP_ENV`` (``sandbox`` or ``prod``);
* ``demo_first_party_feeds``: ``MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS``, off when
  unset (the App payload view has always read unset as off);
* ``presenter_mode``: ``MIP_PRESENTER_MODE``, off when unset;
* ``scheduled_refresh``: ``MIP_SCHEDULED_REFRESH``; the weekly
  ``mip_scheduled_refresh`` job is UNPAUSED only when it is ``on``, and then
  mails its failures to ``MIP_SCHEDULED_REFRESH_RECIPIENTS``. A refresh that
  is off mails nobody.

The profile's rules (tools/clients/rules.py) already keep the demo flags off
a customer install and require recipients (email addresses, never an
application id) when the refresh is on. ``install_policy`` checks the demo
flags again, so a policy can never be built that a profile would refuse.
"""

from __future__ import annotations

from dataclasses import dataclass

from tools.clients.errors import ProfileRefusal
from tools.clients.schema import ClientProfile

PAUSED = "PAUSED"
UNPAUSED = "UNPAUSED"


@dataclass(frozen=True)
class InstallPolicy:
    install_class: str
    app_env: str
    demo_first_party_feeds: bool
    presenter_mode: bool
    scheduled_refresh: bool
    refresh_recipients: tuple[str, ...]

    @property
    def pause_status(self) -> str:
        """``mip_scheduled_refresh``'s schedule: UNPAUSED only when the refresh is on."""

        return UNPAUSED if self.scheduled_refresh else PAUSED


def install_policy(profile: ClientProfile) -> InstallPolicy:
    """The profile's install policy; a demo flag on a customer install refuses."""

    policy = InstallPolicy(
        install_class=profile.install_class,
        app_env=profile.app_env,
        demo_first_party_feeds=profile.demo_first_party_feeds == "1",
        presenter_mode=profile.presenter_mode == "1",
        scheduled_refresh=profile.scheduled_refresh == "on",
        refresh_recipients=(
            profile.scheduled_refresh_recipients if profile.scheduled_refresh == "on" else ()
        ),
    )
    lines = []
    if policy.install_class == "customer":
        if policy.demo_first_party_feeds:
            lines.append("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS: is demo-only and refused for a customer install")
        if policy.presenter_mode:
            lines.append("MIP_PRESENTER_MODE: is demo-only and refused for a customer install")
    if policy.scheduled_refresh and not policy.refresh_recipients:
        lines.append("MIP_SCHEDULED_REFRESH_RECIPIENTS: is required when MIP_SCHEDULED_REFRESH=on")
    if lines:
        raise ProfileRefusal(lines)
    return policy


def policy_controls(policy: InstallPolicy) -> dict[str, str]:
    """The policy as the closed values scripts/deploy.sh reads (no identifying value)."""

    return {
        "MIP_APP_ENV": policy.app_env,
        "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS": "1" if policy.demo_first_party_feeds else "0",
        "MIP_PRESENTER_MODE": "1" if policy.presenter_mode else "0",
        "MIP_SCHEDULED_REFRESH": "on" if policy.scheduled_refresh else "off",
    }
