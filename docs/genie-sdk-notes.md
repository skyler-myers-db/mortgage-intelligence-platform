# Genie Space SDK notes (databricks-sdk 0.103)

Field-notes from the discovery probes that unblocked programmatic Genie
curation for Module 0. Keep these facts authoritative unless a newer SDK
rev moves them; the `tools/databricks/provision_genie_space.py` tool
encodes them end-to-end.

## Relevant API surface

`WorkspaceClient().genie` (SDK 0.103) exposes:

| method                                | use                                         |
| ------------------------------------- | ------------------------------------------- |
| `list_spaces(page_token=...)`         | paginated discovery                         |
| `create_space(warehouse_id, serialized_space, *, title, description, parent_path)` | creates           |
| `update_space(space_id, *, serialized_space, title, description, warehouse_id)`     | edits            |
| `get_space(space_id, *, include_serialized_space=True)` | round-trip verify                     |
| `trash_space(space_id)`               | cleanup (moves to trash; hidden from list)  |
| `start_conversation_and_wait(space_id, content)` | live smoke-test Q/A                 |

`create_space` and `update_space` accept `serialized_space` as a **JSON
string**. The shape of that string is not documented in the SDK — this
file is the contract.

## serialized_space schema (canonical)

Confirmed via `get_space(..., include_serialized_space=True)` on five
live spaces (DataPact, DBDemos Marketing, DBDemos Sales Pipeline,
HLS Interop, Tishman Speyer) and a successful create+verify round-trip.

```json
{
  "version": 2,
  "data_sources": {
    "tables": [
      {
        "identifier": "catalog.schema.table",
        "description": ["Optional per-table description — must be an array."]
      }
    ]
  },
  "config": {
    "sample_questions": [
      {
        "id": "ff33f1b12213e021c2c4a888141953ba",
        "question": ["How many borrowers are currently in-the-money?"]
      }
    ]
  },
  "instructions": {
    "text_instructions": [
      {
        "id": "9ce88802f07591e5ce0457ef51ece021",
        "content": ["Cite the UC table on every answer."]
      }
    ],
    "example_question_sqls": [
      {
        "id": "<32-hex>",
        "question": ["Rolling metrics"],
        "sql": ["SELECT ..."]
      }
    ]
  }
}
```

## Rules the server enforces (found the hard way)

1. **`id` is required and must be a lowercase 32-hex string (no hyphens)**
   on every `sample_questions[]` and `text_instructions[]` entry. Error
   when omitted:
   > `InvalidParameterValue: Failed to parse export proto: sample_question.id
   > must be provided and non-empty. Expected lowercase 32-hex UUID without hyphens.`

   The provisioner derives ids with `md5(f"{kind}||{index}||{text}")` so
   unchanged YAML → identical ids → truly idempotent updates.

2. **`description`, `content`, and `question` values must be JSON arrays.**
   Passing a bare string fails with:
   > `InvalidParameterValue: Invalid serialized_space: Expected an array
   > for description but found "One row per eligible borrower."`

3. **Table identifiers must reference existing catalogs/schemas.** Passing
   `mip.gold.lead_population` when that catalog has not yet been
   created fails with:
   > `PermissionDenied: An error occurred accessing the schema. Failed to
   > fetch tables for the space. ... Catalog 'mip.gold.lead_population'
   > does not exist`

   The error message confusingly quotes the full identifier as "Catalog"
   — in reality it's the catalog-name lookup that's failing. Fallback
   path: create/update with `data_sources.tables: []`, keep questions
   and instructions curated, and re-bind tables after the Lakeflow
   pipeline materializes the catalog.

4. **Minimum viable payload** is `{"version": 2, "data_sources": {}}` — a
   valid space with zero bindings. The server echoes `data_sources: {}`
   (not `{"tables": []}`) in that case, but accepts either shape on input.

5. **`start_conversation_and_wait` does not accept a `timeout` kwarg** in
   SDK 0.103 — it blocks on the default timeout internally. On a freshly
   created space with empty `data_sources`, the call can fail/warm-slow;
   we catch and degrade to a friendly "Genie is still warming up" message
   so the provisioner still exits 0.

## Pagination

`list_spaces()` returns `GenieListSpacesResponse.spaces` and an optional
`next_page_token`. Twelve spaces were returned across one page for the
target workspace; the provisioner paginates defensively anyway.

## Cleanup during discovery

Three probe spaces (`mip-schema-probe-*`, `mip-probe-ids-*`) were created
during schema discovery and immediately trashed via `trash_space`. None
remain visible from `list_spaces`.

## References

- Workspace used: `https://dbc-3aa503a9-4fa8.cloud.databricks.com` (DEFAULT profile).
- Provisioner: `tools/databricks/provision_genie_space.py`.
- YAML spec: `genie/mortgage_lead_intelligence_space.yml`.
- Test: `tests/unit/test_provision_genie_space.py`.
