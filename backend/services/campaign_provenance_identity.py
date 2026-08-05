"""Canonical identity material bound into campaign copy provenance."""

from __future__ import annotations

import hashlib
import json

CAMPAIGN_PROVENANCE_VERSION = 3
CAMPAIGN_TEMPLATE_ID = "benefit_guidance_v1"


def campaign_copy_hash(
    subject: object,
    body: object,
    *,
    variant_name: object,
    channel: object,
    template_id: object = CAMPAIGN_TEMPLATE_ID,
) -> str:
    """Bind exact template, arm, channel, subject, and body into one digest."""

    material = json.dumps(
        {
            "body": str(body or "").strip(),
            "channel": str(channel or "").strip(),
            "subject": str(subject or "").strip(),
            "template_id": str(template_id or "").strip(),
            "variant_name": str(variant_name or "").strip(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
