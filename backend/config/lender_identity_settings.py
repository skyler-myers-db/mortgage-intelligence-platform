"""The App's lender identity settings: ``mip_lender_*`` and ``mip_tenant_id``.

``Settings`` (``backend/config/settings.py``) inherits these fields, so they
read the ``MIP_LENDER_NAME`` / ``MIP_LENDER_NMLS_ID`` / ``MIP_TENANT_ID``
deploy controls of the same names. The validator below resolves them, with a
digest-pinned customer record (``MIP_LENDER_IDENTITY_JSON`` and its
``MIP_LENDER_IDENTITY_SHA256`` pin, which are not fields), through
``backend/schemas/lender_identity_record.py`` and nothing else.

Readers: the disclosure and outreach gates (``effective_tenant_id``), the
lender reference map and the verified-boot audit (``reviewed_lender_identity``)
and, through the provider ``settings.py`` registers, the tenant validators
(``mip_lender_name``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel, ModelWrapValidatorHandler, PrivateAttr, model_validator

from backend.schemas.lender_identity import effective_public_tenant_id
from backend.schemas.lender_identity_record import (
    LENDER_NAME_ENV,
    LENDER_NMLS_ENV,
    TENANT_ID_ENV,
    ReviewedLenderIdentity,
    pinned_inputs,
    reviewed_lender_identity_from_env,
)


class LenderIdentitySettings(BaseModel):
    """The one reviewed lender identity this install serves."""

    mip_lender_name: str = "Summit Mortgage"
    mip_lender_nmls_id: str = ""
    mip_tenant_id: str | None = None
    _reviewed_lender_identity: ReviewedLenderIdentity | None = PrivateAttr(default=None)

    def effective_tenant_id(self) -> str:
        """Return the Lakebase disclosure namespace for this deployment."""

        return effective_public_tenant_id(
            self.mip_tenant_id,
            lender_name=self.mip_lender_name,
        )

    @model_validator(mode="wrap")
    @classmethod
    def _validate_public_lender_identity(
        cls, data: Any, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        """Resolve the reviewed lender identity (lender_identity_record.py).

        A legacy field goes in only when configured, so an unset name is the
        Summit default and never conflicts with a pinned record. The record
        and its pin are not fields, but come from the same sources: the
        environment first, then this instance's dotenv files, which
        pydantic-settings hands this validator as extra keys of ``data``.
        """

        settings = handler(data)
        loaded = data if isinstance(data, Mapping) else {}
        settings._resolve_lender_identity(pinned_inputs(os.environ, loaded))
        return settings

    def _resolve_lender_identity(self, pinned: Mapping[str, str]) -> None:
        inputs: dict[str, object] = dict(pinned)
        for field_name, env_name in (
            ("mip_lender_name", LENDER_NAME_ENV),
            ("mip_lender_nmls_id", LENDER_NMLS_ENV),
            ("mip_tenant_id", TENANT_ID_ENV),
        ):
            if field_name in self.model_fields_set:
                inputs[env_name] = getattr(self, field_name)
        identity = reviewed_lender_identity_from_env(inputs)
        self.mip_lender_name, self.mip_lender_nmls_id = identity.lender_name, identity.nmls_id
        if self.mip_tenant_id is not None or identity.pinned:
            self.mip_tenant_id = identity.tenant_id
        self._reviewed_lender_identity = identity

    def reviewed_lender_identity(self) -> ReviewedLenderIdentity:
        """The identity the validator resolved: its digest is ``None`` unless pinned."""

        identity = self._reviewed_lender_identity
        if identity is None:  # model_construct() skips validation
            raise RuntimeError("settings were constructed without lender identity validation")
        return identity
