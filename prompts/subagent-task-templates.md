# Subagent task templates

## Frontend task

```text
Use the frontend-implementer subagent. Implement [ROUTE/COMPONENT]. Keep the design aligned with docs/design-review.md and the current Module 0 prototype. Acceptance: [LIST]. Validate with npm --prefix frontend run test and npm --prefix frontend run build.
```

## Backend task

```text
Use the backend-databricks-engineer subagent. Implement [ENDPOINT/SERVICE] with mock mode and Databricks adapter boundary. Acceptance: [LIST]. Validate with pytest -q tests/unit tests/integration.
```

## Data task

```text
Use the data-modeler subagent. Implement [TABLE/METRIC_VIEW/FUNCTION]. Map fields to docs/data-contract.md and maintain evidence lineage. Acceptance: [LIST]. Validate SQL assumptions and update docs/data-contract.md if needed.
```

## QA task

```text
Use the qa-test-engineer subagent. Create tests for [SLICE]. Include unit, integration, and one UI acceptance where appropriate. Validate commands and report exact failures.
```

## Governance task

```text
Use the governance-security-reviewer subagent. Review [FILES/SLICE] for PII, secrets, approval gate, audit, UC/Genie scope, and partner claims. Return risk severity and required fixes.
```
