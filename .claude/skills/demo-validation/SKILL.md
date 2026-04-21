---
name: demo-validation
description: Use when validating the booth demo, screenshots, UX flow, talk track, or fallback behavior.
---


# DAIS demo validation Skill

Use this skill before any demo review.

Validate:
- 1440×900 layout remains readable.
- **Visual parity with `design_files/Module 0 Prototype.html` and `design_files/module_0_prototype_*.png`.** Topbar (breadcrumbs + workspace pill + warehouse-status pill + icon buttons), persistent right-rail Console, floating Genie chat reachable from every page, agent activity log on home, evidence-chip density on every metric. Class names match the prototype's BEM (see CLAUDE.md "Design source of truth").
- Tweaks/dev panels are hidden by default.
- Primary demo path works in mock mode.
- Every page has a clear business purpose.
- Evidence drawer opens from KPI, score, and recommendation surfaces.
- Approval action creates an audit event.
- Genie has deterministic fallback answers.
- No route depends on live Cotality, real CRM, real email, or real borrower PII.
- Demo lender name is consistent.

Output a 6–8 minute click path and backup path.

