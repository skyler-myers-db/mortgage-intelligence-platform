---
name: runtime-secret-mask-guard
description: provision_runtime_secrets refuses a changed/unreadable cotality-id-mask-v1 (salted fingerprints) and keeps existing salesforce-* keys; deploy.sh previews it with --check before the App stop
metadata:
  type: project
---

Since post-w5/deploy-hardening (2026-10-27) `tools/databricks/provision_runtime_secrets.py` plans every write first. An existing `cotality-id-mask-v1` must equal the configured `MIP_COTALITY_ID_MASK_SECRET` as the runtime reads it (UTF-8, stripped), compared as HMAC-SHA256 under a per-run random salt; differ or unreadable -> `RuntimeSecretChangeRefused`, exit 2, nothing written. Unset `SALESFORCE_*` keeps an existing key. `--check` is read-only and runs at the end of the step-0 preflight, before step 0a stops the App.

**Why:** a changed mask re-keys every `B-…` id and orphans the ids stored in Lakebase; the old tool overwrote mip-runtime unconditionally, and the write runs after the App stop (an unsigned first install would be left stopped). The REST `secrets/get` API does answer outside DBUtils despite the SDK docstring: a read-only probe for a nonexistent key in paychex `mip-runtime` returned RESOURCE_DOES_NOT_EXIST (no value read).

**How to apply:** never add a "rotate the mask" flag to the provisioner; a re-key is an owner data migration. Any new secret that keys persisted data gets the same fingerprint guard and a pre-stop `--check`. Related: [[bundle-experiment-name-rewrite]].
