---
name: Design contract discipline
description: The HTML prototypes in design_files/ are the design spec, not a reference; port tokens + BEM verbatim before composing routes.
type: feedback
---

For UI work on Module 0, the canonical sources are `design_files/index.html` (tokens + BEM), `design_files/Module 0 Prototype.html` (page composition + AppShell + floating Genie + right-rail Console), and `design_files/Design System.html`. The master has explicitly called out that mismatches are bugs.

**Why:** The slice that preceded this one shipped functional but visually amateurish pages because the prototype's CSS/BEM wasn't copied verbatim; the user called it out and asked for a parity port ("take the time to get this right"). The prototype is the spec.

**How to apply:** When building any UI:
1. Read `design_files/index.html` :root + [data-theme=...] + [data-accent=...] + [data-density=...] + all BEM component blocks. Copy them into `frontend/src/design-system/tokens.css` and `components.css` verbatim.
2. Use prototype class names exactly: `.surface` / `.surface__hdr` / `.surface__body`, `.kpi__value`, `.seg-card__count`, `.chip--success`, `.score--high`, `.tbl__expand`, `.approval__ico`, `.drawer`, `.genie__msg--user`, `.evidence-chip`. No `.card-header` / `.kpi-value` style renames.
3. Use prototype tokens only: `var(--sp-4)`, `var(--fs-22)`, `var(--r-md)`, `var(--seg-itm)`. No inline hex or inline pixel values in JSX except in already-dynamic contexts (e.g. seg-color CSS variable composition).
4. AppShell is rail + topbar + main with right-rail Console + floating Genie panel — Genie is reachable from every page; the standalone `/ask-genie` is the deep-dive view.
5. Geist + Geist Mono via Google Fonts @import in tokens.css.
