"""Client profiles: one typed, validated description of a governed install.

A client profile names one install of the Mortgage Intelligence Platform in one
Databricks workspace. The repository carries only the schema (``schema``), the
input resolver (``environment``), the derived views (``profile``), the log
redaction helpers (``redact``) and a placeholder template
(``deploy/clients/example.env``). Concrete values live outside the tree: in CI
as GitHub Environment variables (public fields) and secrets (identifying and
secret fields), and locally in ``.env.local``.

Nothing in ``scripts/deploy.sh`` reads a profile yet; later slices wire it in.

``bootstrap_account`` is the human-run account bootstrap that creates or
adopts a client's identities before its first deploy (docs/clients/bootstrap.md).
``github_environment`` creates and fills the client's GitHub Environments,
and ``github_sink`` is the bootstrap's credential sink into them
(docs/clients/github-environment.md).
"""
