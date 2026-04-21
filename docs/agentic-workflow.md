# Agentic coding workflow

## Master agent start prompt

Use the prompt in `prompts/master-claude-opus-implementation-prompt.md` to start the main session.

## Daily loop

1. `git status`
2. `/plan-sprint <today's objective>`
3. Select one vertical slice.
4. Delegate file-heavy work to one subagent.
5. Implement.
6. Run narrow tests.
7. Run `python tools/verify_scaffold.py`.
8. Commit.
9. Compact context with a summary of completed slices and remaining risks.

## Token/context discipline

- Keep master context for decisions, not file dumps.
- Use subagents for large searches and code inspection.
- Ask subagents to summarize; do not paste full files unless necessary.
- Use `tools/agent_context_builder.py` for a compact project map.
- Use `/compact` after a complete vertical slice, not mid-debug.

## Multi-agent sequencing pattern

Example for `/lead-queue`:

1. Principal architect defines route acceptance criteria.
2. Frontend implementer builds table and expansion drawer.
3. Backend engineer wires leads/borrowers endpoints.
4. Data modeler validates payload names map to gold tables.
5. QA engineer writes tests.
6. Governance reviewer checks evidence and approval rules.
7. Storyteller updates talk track.

## Validation cadence

- After UI component: `npm --prefix frontend run test`.
- After route: `npm --prefix frontend run build`.
- After backend service: `pytest -q tests/unit`.
- After endpoint: `pytest -q tests/integration`.
- After Databricks config: `databricks bundle validate -t dev`.
- Before release: all of the above plus manual click path.
