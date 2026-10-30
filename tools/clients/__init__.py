"""Client profiles: one typed, validated description of a governed install.

A client profile names one install of the Mortgage Intelligence Platform in one
Databricks workspace. The repository carries only the schema (``schema``), the
input resolver (``environment``), the derived views (``profile``), the log
redaction helpers (``redact``) and a placeholder template
(``deploy/clients/example.env``). Concrete values live outside the tree: in CI
as GitHub Environment variables (public fields) and secrets (identifying and
secret fields), and locally in ``.env.local``.

``scripts/deploy.sh -t client`` resolves the profile before any other step
(the ``client-target`` view) and ``live_checks`` proves the workspace and
the deployer against it; tools/databricks/bundle_env.py takes the bundle's
resource names from it. Later slices wire in the rest.

``bootstrap_account`` is the human-run account bootstrap that creates or
adopts a client's identities before its first deploy (docs/clients/bootstrap.md).
"""
