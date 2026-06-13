# Principal Architect Memory

- [Genie source taxonomy](project_genie_source_taxonomy.md) — trusted answers aren't only `source==='genie'`; `trusted_sql`/`sales_ops` are too. Gate UI on the denylist, not a genie allowlist.
- [Salesforce activation gate](project_salesforce_activation_gate.md) — real SF delivery is inert at booth: no code flips a destination to 'connected', so the inline POST /stage adapter never fires unless manually armed.
