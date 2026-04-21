# AGENTS.md — Subagent operating model

Use this file to coordinate Claude Code subagents. The master agent remains responsible for final decisions, context compression, and validation.

## Team topology

| Subagent | Use when | Output expected |
|---|---|---|
| principal-architect | Architectural ambiguity, vertical-slice planning, platform tradeoffs | decision memo + next tasks |
| frontend-implementer | React route, component, design-system, interaction work | code + UI acceptance notes |
| backend-databricks-engineer | FastAPI, Databricks SQL, Lakebase, Genie, app runtime | code + endpoint contract |
| data-modeler | SQL tables, metric views, UC functions, data validation | SQL + data contract updates |
| qa-test-engineer | Unit/integration/e2e/accessibility/performance checks | test files + failure triage |
| governance-security-reviewer | PII, secrets, compliance, approvals, audit, UC governance | risk findings + fixes |
| demo-storyteller | DAIS talk track, screenshots, business narrative | story script + rehearsal checklist |
| performance-optimizer | Latency, caching, bundle/app size, SQL performance | before/after metrics |

## Coordination pattern

1. Master agent states a task objective in one paragraph.
2. Master agent assigns one subagent with a clear boundary.
3. Subagent returns a compact result: files touched, decisions, commands run, issues.
4. Master agent validates and integrates.
5. Governance/security reviewer checks any data, outreach, auth, or deployment change.
6. QA agent runs after each vertical slice.

## Agent quality gates

Every subagent must answer these before returning:

- What changed?
- Why is it correct for Module 0?
- What validation ran?
- What risk remains?
- What should the master agent do next?

## Extra gate for UI-touching agents

Any agent that edits CSS, components, routes, page copy, or anything visible to a user MUST read `design_files/index.html` first (and `design_files/Module 0 Prototype.html` for page composition). The interactive prototype is the design contract — not a reference. Class names match the prototype's BEM (`.surface__hdr`, `.kpi__value`, `.seg-card__count`, `.chip--success`, `.score--high`, `.tbl__expand`, `.drawer`, `.genie`, `.approval`). Token vocabulary matches the prototype (`--sp-4`, `--fs-22`, `--r-md`, `--seg-itm`). No inline hex colors, no inline pixel values. Geist + Geist Mono are the typefaces. See the "Design source of truth" section in `CLAUDE.md` for the full rule set.

## No-go handoffs

Never hand off a task with:

- “Make it production ready” without naming files and acceptance criteria.
- “Fix everything” without a failing command or bug reproduction.
- “Improve design” without route/component and screenshot target.
- “Wire Databricks” without exact env vars, endpoint, and fallback mode.
