---
name: browser-signoff-ops
description: Operational facts for live Playwright-MCP browser signoffs of deployed mip-app (file sandbox, slow capability probe, gap-measurement method)
metadata:
  type: project
---

Operational notes for running a live browser signoff of the deployed mip-app with the Playwright MCP tools. Complements the API-layer recipe in [[live-signoff-recipe]].

**Playwright MCP is file-sandboxed to the repo.** Screenshot/output paths outside `/Users/entrada-mac/repos/mortgage-intelligence-platform` (and its `.playwright-mcp/`) are rejected ("outside allowed roots"). Write screenshots with a **relative filename** (they land in the repo root), then `mv` them to the scratchpad afterward so a READ-ONLY task leaves the repo clean. `git status` afterward should show only pre-existing untracked dirs.
**Why:** the task's requested output dir (`/private/tmp/.../scratchpad/ux-signoff/`) is unreachable by the browser tool directly.
**How to apply:** for any browser-signoff, screenshot to `name.png`, then batch-move to the scratchpad and verify no `*.png` residue in repo root.

**The admin capabilities live probe is slow (~40s).** `/admin-config` → "Platform capabilities (live probes)" disclosure fires `GET /api/v1/admin/capabilities?live=1` (and `/growth-agent?live_capabilities=1`); these stay pending 30-40s (warehouse warm-start), showing a "Checking / Running live probes…" state before rendering the capability rows (Genie Conversation API, UC metric-view certification, reviewed SQL tools, MLflow Agent Eval, Agent Framework, AI Gateway, Lakebase — each with an Available/Configured/Not provisioned chip). Do NOT fail the item early — wait it out and re-poll `browser_network_requests` until request → 200.
**Why:** first probe after a deploy hits a cold warehouse; the app shows an honest loading state, not an error.
**How to apply:** budget ~45s for that disclosure; use `browser_network_requests` (a pending request shows no status code) to distinguish "still loading" from "failed".

**Topbar gap measurement (breadcrumb vs search).** The last breadcrumb crumb is `.cur` (e.g. "Segment Intelligence"); the search box is `form.topbar__search` with an **opaque** bg and its `input` inset ~28px from the form's left edge. Measure the gap both to the form box left AND the input left — the form box can overlap the crumb even when the input doesn't. At 1440×900 the longest route label ("Segment Intelligence") is the worst case.
