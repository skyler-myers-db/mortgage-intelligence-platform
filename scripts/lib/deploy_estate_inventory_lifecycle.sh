# shellcheck shell=bash
# The owner's estate inventory (M4-PR1): the agentic tier's foreign UC audit
# relies on it instead of deployer metastore ownership, and it retires
# MIP_UC_FOREIGN_CATALOG_BINDING_POLICY (tools/databricks/estate/inventory.py,
# docs/deployment.md "Estate inventory"). The core tier runs no foreign UC
# audit and needs no inventory: neither function prints or reads anything
# there unless the retired policy is still set.
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.

# The retired binding policy was an exact contract on the binding-denied
# catalogs; the inventory now carries them. Set, it refuses in the agentic tier
# (it would have made the audit stricter than the inventory can) and is ignored
# with one notice in the core tier, which never audited with it. It is never
# exported to a tool again.
retire_foreign_catalog_binding_policy() {
  local value
  value="$(deployment_control_value MIP_UC_FOREIGN_CATALOG_BINDING_POLICY)"
  unset MIP_UC_FOREIGN_CATALOG_BINDING_POLICY
  if [[ -z "${value//[[:space:]]/}" ]]; then
    return 0
  fi
  if tier_is_agentic; then
    echo "${RED}[deploy] MIP_UC_FOREIGN_CATALOG_BINDING_POLICY is retired: the owner's estate inventory (MIP_ESTATE_INVENTORY_JSON or MIP_ESTATE_INVENTORY_FILE, acknowledged by MIP_ACK_ESTATE_INVENTORY; python -m tools.clients.estate_inventory) now carries the reviewed binding-denied catalogs. Unset it; nothing was changed.${RST}" >&2
    exit 2
  fi
  echo "  retired: MIP_UC_FOREIGN_CATALOG_BINDING_POLICY is set but unused (the core tier runs no foreign UC audit); unset it"
}

# Read-only, before the lease: the inventory and its exact estate ack, both
# from this process's environment only (never .env.local), for this metastore,
# workspace and install catalog, at most 30 days old at the end of the run's
# margin, with no gap. Prints counts and the digest, never a name. The adopt
# phases run no foreign UC audit, so they need no inventory either.
check_estate_inventory_prelease() {
  if ! tier_is_agentic || [[ "${DEPLOY_PHASE:-}" == "adopt" || "${DEPLOY_PHASE:-}" == "unadopt" ]]; then
    return 0
  fi
  step "estate inventory: the owner's metastore inventory and its exact estate ack (read-only)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  would check read-only: MIP_ESTATE_INVENTORY_JSON or MIP_ESTATE_INVENTORY_FILE against MIP_ACK_ESTATE_INVENTORY (metastore, workspace, install catalog, at most 30 days old, no gap)"
    return 0
  fi
  if ! "$PYTHON" -m tools.databricks.estate.inventory_check --catalog "${MIP_DEFAULT_CATALOG:-mip}"; then
    echo "${RED}[deploy] the estate inventory was refused before the lease (see above); nothing was changed.${RST}" >&2
    exit 2
  fi
}
