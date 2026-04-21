---
name: agentic-quality-gate
description: Use before committing generated code or accepting subagent work.
---


# Agentic quality gate Skill

Use this skill to review generated code.

Gate checklist:
1. Does the change fit the Module 0 DAIS scope?
2. Are files small and well-named?
3. Are mocks clearly separated from production adapters?
4. Are typed interfaces used at route/service boundaries?
5. Are secrets denied and absent?
6. Are tests updated?
7. Did the agent run validation or explain why not?
8. Is the demo more stable after the change?

Reject work that hides errors, widens scope, or removes explainability/compliance UI.

