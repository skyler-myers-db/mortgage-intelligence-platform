# Principal Architect Memory

- [Genie source taxonomy](project_genie_source_taxonomy.md) — trusted answers aren't only `source==='genie'`; `trusted_sql`/`sales_ops` are too. Gate UI on the denylist, not a genie allowlist.
- [Salesforce activation gate](project_salesforce_activation_gate.md) — real SF delivery is inert at booth: no code flips a destination to 'connected', so the inline POST /stage adapter never fires unless manually armed.
- [W5b integration lessons](project_w5b_integration_lessons.md) — dep-batch holds only show on the installed tree (bisect via scratch npm ci + symlink swap); PG prefix suites need contract_as_of; mid-test gate resets need a reset stub; CSS-only br noise; route deferrals by the repo plan lane names
