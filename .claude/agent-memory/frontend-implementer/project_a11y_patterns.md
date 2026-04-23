---
name: A11y/race patterns pinned in this repo
description: Dialogs mirror EvidenceDrawer; window-level hotkeys must check document.activeElement too; async UI uses useRef latches.
type: project
---

Three recurring patterns enforced by audit fixes (R5 round, 2026-04-23):

1. **Dialog a11y contract**: floating panels (EvidenceDrawer, GenieChat) use
   `role="dialog" aria-modal="true"`, focus the first interactive element on
   open via `queueMicrotask(() => ref.current?.focus())`, trap Tab cycling
   inside the panel, and restore focus to `lastFocusedRef` on close. New
   dialogs must mirror this exactly.

2. **Window-level hotkey handlers** must check BOTH `e.target` AND
   `document.activeElement` against `isEditableTarget` (exported from
   `LeadTable.tsx`). Belt-and-suspenders: `e.target` falls back to
   `document.body` when nothing is focused, bypassing an input check.

3. **Async click handlers need a synchronous useRef latch** alongside the
   React state that drives the disabled UI. `setState` is async, so two
   rapid clicks in the same frame can both read `inFlight=false` and
   spawn duplicate POSTs. Flip the ref synchronously before any `await`.

**Why:** Audit sweep found three distinct duplicate-audit-row bugs all
rooted in relying on React state as a gate. Useful for reviewing any
future button that POSTs on click — if the handler is async and there's
no ref latch, it's probably a double-click footgun.

**How to apply:** When editing or adding any component that calls
`api.approve`, `api.reject`, or similar state-changing POSTs, check for
a synchronous guard before the first await. For new dialogs, import the
focus-trap pattern from `EvidenceDrawer.tsx` or `GenieChat.tsx` instead
of reinventing it.
