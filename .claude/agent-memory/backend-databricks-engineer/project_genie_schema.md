---
name: Genie serialized_space schema (databricks-sdk 0.103)
description: Discovered-the-hard-way rules for constructing the serialized_space JSON that Genie create/update endpoints accept.
type: project
---

The `WorkspaceClient().genie.create_space` / `update_space` calls take
`serialized_space` as a **JSON string** whose shape is not in the SDK docs.
The canonical schema (confirmed via live probe + round-trip on DEFAULT workspace,
2026-04-20):

```json
{
  "version": 2,
  "data_sources": {"tables": [{"identifier": "c.s.t", "description": ["..."]}]},
  "config":       {"sample_questions":  [{"id": "<32hex>", "question": ["..."]}]},
  "instructions": {"text_instructions": [{"id": "<32hex>", "content":  ["..."]}]}
}
```

Server-enforced rules (each was discovered via a rejection error, see
`docs/genie-sdk-notes.md` for the exact error text):

1. `id` on sample_questions and text_instructions is REQUIRED and must be
   lowercase 32-hex (no hyphens). Server does not auto-generate. We derive
   deterministically from content (md5) so unchanged YAML ⇒ idempotent update.
2. `description`, `content`, `question` must be JSON arrays. Bare strings
   are rejected: "Expected an array for description but found ...".
3. Table identifiers must reference catalogs that EXIST in Unity Catalog
   at create/update time. In this workspace `mip` is materialized by
   the Lakeflow pipeline, so cold-provisioning before the bundle deploy
   requires the `include_tables=False` fallback (space + questions +
   instructions land; tables get bound on a later re-run).
4. `start_conversation_and_wait(space_id, content)` does NOT accept a
   `timeout` kwarg in SDK 0.103.

**Why:** The previous slice attempted `serialized_space="{}"` and
ran aground on these validations; the user asked for programmatic
create+curate. Future modules that spin up their own Genie spaces
(Retention, Cross-sell) should reuse `tools/databricks/provision_genie_space.py`
directly and share this schema.

**How to apply:** Any code that builds a `serialized_space` payload must
(a) emit id fields as md5(content), (b) wrap single strings in one-element
lists, and (c) handle the missing-catalog error by falling back to
`tables: []`. Reach for `provision_genie_space.SpaceSpec.to_serialized_payload`
rather than rebuilding the shape ad-hoc.
