# Identity boundary: whose data the browser shows

Module 0 runs on shared machines (booths, loaner laptops, one browser for a team). The server never trusts the browser, but the browser still keeps some private working state: pinned Genie answers, the Genie conversation id, transcript and in-flight turn, the Lead Queue context behind the dossier pager, the bulk-approve stash, and a snapshot of non-PII aggregates. This page says how that state is tied to the signed-in actor, so one operator's data never renders for the next.

Decisions: D-identity-review-a1, D-identity-review-b, D-identity-review-a2 and D-identity-review-a3 (built; audit 2026-09-21 `genie-02` item 1, `bundle-04` item 4, `delivery-05`), and the W5a integrator ruling on cross-tab access. D-identity-review-a4 is planned (section 6). Code: `backend/services/actor_identity.py`, `frontend/src/lib/healthTrust.ts`, `frontend/src/lib/actorScope.ts`, `frontend/src/lib/healthRecheck.ts`, `frontend/src/lib/queryPersist.ts`, `frontend/src/components/layout/AppShell.tsx`.

## 1. Trust model (a1)

**Server.** One module, `backend/services/actor_identity.py`, owns the edge-header read for every identity reader: health, session, audit `resolve_actor`, visit tracking, the authenticated-read RBAC gate and the backpressure bucket. `forwarded_identity(conn)` returns `X-Forwarded-Email` when non-empty, else `X-Forwarded-User` when non-empty, else nothing, and nothing at all unless `trust_forwarded_headers` is set (otherwise the headers are attacker-writable). The value is raw; each caller keeps its own fallback (`default_actor` plus the identity-fallback counter, the untrusted-edge marker, a 401, `'anonymous'`).

`actor_cache_key(identity)` is the opaque per-actor discriminator the browser keys on: `actor_` + the first 16 hex characters of HMAC-SHA256 over `identity.strip().lower()`, keyed by `MIP_GENIE_ACTION_SECRET_CURRENT`, else the legacy `MIP_GENIE_ACTION_SECRET`, else one random per-process secret. It is not reversible to the identity and is never sent to telemetry. The authenticated `/api/v1/health` body and `/api/v1/session` both carry it (session: `actor_cache_key`, null exactly when `actor_email` is), derived by the same function, so they always agree. `tests/unit/test_actor_identity.py` pins the derivation byte for byte (a golden key), the secret order, and a parity table of all six readers.

**Browser.** `lib/healthTrust.ts` decides which responses may say who the actor is:

- An `unreachable` health payload (a 5xx, a dropped connection, an unreadable body) says nothing, unless `api.health` marked it as an authentication failure (401, a confirmed sign-in redirect, an ended session, 403): then the actor is known to be nobody. The mark lives in a module `WeakSet`, so JSON cannot forge it.
- Any other health payload is trusted only when it is a real health body: `mode` is `live`, `status` is `ok` or `degraded`, and `actor_cache_key` is absent, null or matches `^actor_[0-9a-f]{16}$`. The anonymous `{status, mode}` body is a trusted "nobody". A proxy's JSON, `{}` or a malformed key says nothing.
- A `/api/v1/session` body seeds the actor only before the first trusted health probe: both capability flags are booleans and `actor_cache_key` is an own property holding null or a well-formed key. HealthProvider reads the boot-primed session query once (or its first success) and never creates an observer or a request; after that, health is the only ongoing detector.

## 2. The actor gate (b)

`lib/actorScope.ts` is the only module that reads or writes a private key. Each storage area carries an owner stamp: `mip.actorOwner` in localStorage (shared by every tab of the origin) and `mip.actorCacheKey` in the tab's sessionStorage. A stamp holds an actor key or `~nobody`.

The gate is **pending** when a document starts: private reads return null and writes are queued as updaters. Every trusted observation `k` (null = nobody) resolves each area against its stamp `s`; the first matching row wins:

| Row | Stamp `s` | Observed `k` | Private data | Area |
|---|---|---|---|---|
| 1 | absent | any | removed; stamp `k` (or `~nobody`) | open |
| 2 | `k`, or an alias of `k` | real | kept; re-stamped to `k` | open |
| 3 | another real actor | real | removed; stamp `k` | open |
| 4 | `~nobody` | real | removed; stamp `k` | open |
| 5 | real | nobody | kept; nothing rewritten | **closed** |
| 6 | `~nobody` | nobody | kept | open (nobody) |

Unowned data is never adopted, not even by nobody. The gate is **open** only when both areas resolve open. Before the rows, a document-level check: once this document has opened for a real actor A (`documentOwnerKey()`, held in memory only, never cleared by a close), a different real `k` that is not an alias of A is a **proven change** (below). Because the check is per document, another tab rewriting the shared local stamp cannot hide the change. A FIRST observation (no document owner yet) only runs the rows: a reload as another actor is that actor's own navigation, so it clears and opens with no reset and no notice.

On resolution the gate replays queued updaters against the post-clear values (open) or drops them (closed); while closed, writes are dropped. Events, in order: `cleared` (a stored private key was removed), `restamped` (a stamp now names another owner, a suspend, a reset), `opened` / `closed` (a status change). The shell clears in-memory actor state on `cleared` and `closed` only; stores drop their caches on every event. Storage that throws holds values and stamps in memory for the document, so row 1 applies at every document start (and a reset's notice and loop guard are lost with the old document).

**A trusted nobody (D-identity-review-a2).** Row 5 keeps storage but closes the gate, so the stores read nothing. The shell also clears its in-memory actor state on `closed`, as it does on `cleared`: the TanStack query cache, the last borrower, approvals, the drawer and workspace state, the route-local caches, and the live Genie turn (the Genie reset event). The removals that reset triggers are dropped by the closed gate, and the `closed` event re-arms the Genie turn resume (`lib/genieInFlightTurn`). So a same-page reopen for the owner (a 403 or an anonymous body that clears up) shows the pins and the transcript again with nothing removed and resumes the kept in-flight record; a 401 ends the session (health polling stops until a reload), and the reload is a new document whose first observation keeps the owner's data (and resumes the record) or clears another actor's.

**A proven change resets the document (D-identity-review-a3).** Synchronously and in order: (i) the gate closes for good: a `resetting` flag makes later observations, the storage listener and the stamp guard no-ops, so it can never reopen in place, for `k` or for A, and the document owner stays A; (ii) `clearUnsavedWork()` empties the unsaved-work registry and detaches its `beforeunload` listener, so neither "Leave without saving?" nor the browser's "Leave site?" can hold A's work; (iii) every area whose stamp is neither `k` nor an alias of `k` loses its private keys, then both stamps become `k` (the preference map and LEGACY are untouched); (iv) the loop guard on `mip.actorResetAt`. With no reset in the last 30 s the gate emits `restamped` **only** (a `cleared` or `closed` would clear the query cache, and the re-render would send audited refetches, `VIEW_*`, under `k`'s cookie before the page goes), writes `mip.actorResetNotice` and `mip.actorResetAt`, and replaces the document with `window.location.replace('/')`, even on `/`. The next document's first observation is `k` on stamps of `k`, so it opens with nothing to clear, and the shell shows one persistent info toast ("The signed-in user changed, so this tab was reset. Nothing from the previous session carries over.") the first time the gate is open, or on the first change to visible in a hidden tab (`takeActorResetNotice()` reads and removes the flag).

**The hold.** A second proven change within 30 s of the last reset is held until that boundary: `actorResetHeld()` is true, the gate emits `restamped` then `closed`, and one timer resets the document at the boundary. While held, the shell renders the route placeholder (`RouteFallback`) instead of the routed page and the Console placeholder instead of the Console, and only after the commit that unmounted them does it clear the query cache, with every query disabled by default first, so the shell's own readers (the workspace, the footprint) rebuild without fetching. Nothing but health and session leaves during the hold.

**Another tab (the W5a integrator ruling).** A window `storage` event on `mip.actorOwner` (or a `clear()`, key null) whose new value is not this document's owner SUSPENDS an open gate: it closes at once and emits `restamped` only, so stores drop their caches and read null (the pins and the transcript hide) while the query cache stays, and asks `lib/healthRecheck` for one more trusted probe. A probe answering the owner (or an alias) runs the rows and reopens; another actor is a proven change. While open, a PRIVATE_LOCAL read returns null and a write is dropped when the stored local stamp is present and is not this document's owner (`~nobody` for a document open for nobody); either queues one suspend in a microtask, never an event from a read. An absent stamp is not foreign (a test's `installLocalStorage()` clears it, and another tab's `clear()` reaches the listener). Preferences are keyed per actor and the session area is per tab, so neither is guarded.

**The fresh-identity rule.** The health poll keeps the previous identity object while the key is unchanged, so the shell's actor effect runs once per change. A suspended gate needs to hear "still A", so the first TRUSTED probe started after a recheck request publishes a fresh `{ key }` object even for an unchanged key; every other probe keeps the dedupe. A request probes at once (no probe in flight, the 1 s nudge gap passed), else exactly one tick is scheduled; a hidden, offline or session-expired tab probes later on its own path, and that probe answers the request.

## 3. Every storage key, by class

`actorScope.registry.test.ts` parses every non-test source under `frontend/src` and `frontend/public` and fails on an unregistered `mip` key, a storage call outside the reviewed files, or a whole-area `.clear()`, index, enumeration or `for-in`.

| Class | Keys | Rule |
|---|---|---|
| PRIVATE_LOCAL | `mip.pinnedInsights`, `mip.genie.conversationId` | through the gate only; removed by rows 1/3/4 |
| PRIVATE_SESSION | `mip-genie-conversation-v1`, `mip.genie.inFlightTurn`, `mip.queueContext`, `mip.bulkApprove.lastCancelled`, `mip.queryCache.v1`, `mip.portfolio.campaignDraft.v1` (registered by w5-portfolio-forms) | same; the bulk-approve stash goes through the guarded accessors and is cleared only while the gate is open |
| ACTOR_PREFERENCE_LOCAL | `mip.shortcuts.singleKey` | a per-actor map, never cleared |
| DEVICE_LOCAL | `mip.theme`, `mip.accent`, `mip.density`, `mip.consoleOpen`, `mip-genie-chat-size-v1`, `mip-genie-chat-pos-v1`; pre-registered `mip.themeChosen`, `mip.accentChosen` | device preferences, not actor data |
| DEVICE_SESSION | `mip.mainScroll.v1`, `mip.leadTableScroll.v1`, `mip.rumApiSample`, `mip.staleChunkReloadAt`, `mip.actorResetNotice`, `mip.actorResetAt` (the reset's notice and loop guard) | per-tab device state |
| STAMPS | `mip.actorOwner` (local), `mip.actorCacheKey` (session) | written by the gate only |
| LEGACY | `mip.lastBorrowerId` | removed at each document's first resolution (in-memory AppContext state since 8a30eafa) |

## 4. The preference map

The single-key shortcut switch (WCAG 2.1.4) is stored as `{actorKey | "~nobody": "on" | "off"}`, at most 8 entries, most recently written first. Reads use the resolved owner's entry (absent means on); while the gate is pending or closed they use the local stamp's entry. Writes land under the resolved owner (queued while pending, dropped while closed). An alias re-stamp moves the entry to the new key. On the upgrade, a legacy plain `on` / `off` value is adopted once as the first owner's entry.

## 5. The persisted aggregate cache (delivery-05)

A reload, a restored or a duplicated tab repaints Home and the analytics aggregates from the last snapshot while the fresh reads run.

- Key: `mip.queryCache.v1`, PRIVATE_SESSION (per tab; no next-morning restore, which would need localStorage and is an owner decision).
- When: `lib/queryPersist.ts` is imported only after the gate first opens, then restores once and saves on a 1 s trailing throttle (plus once after the restore). Nothing restores for an unconfirmed actor, and an actor change removes the key.
- What: default deny. Successful queries on an exact allow-list (Home preview and contactable preview, Home summary, geo state rollups and rate sensitivity, segments, analytics executive / geography / segments / rate-window, config options) whose data holds no masked borrower id (`B-` + 13). Never leads, a borrower, lifecycle, audit, Genie, outreach, workspace, admin or ZIP-level reads, and never a mutation. The restore applies the same filter.
- Age: restored queries keep their true `dataUpdatedAt` (a FetchedAt label shows the real age); a fresher read always replaces them; `maxAge` is 24 h; the buster is the hashed entry file name, so a deploy discards the snapshot. A snapshot over 256 KiB is not written.
- Limit of the buster: it changes only when the frontend bundle changes. A backend-only deploy that changes an allow-listed response shape leaves the old snapshot restorable. Two things bound this: `maxAge` (24 h), and the ordinary stale-time refetch. A restored query keeps its true `dataUpdatedAt`, so once it is older than its `staleTime` (30 s by default, 60 s for config options) it refetches when observed, and that read replaces the restored copy.
- Nothing here reads an audited endpoint or writes an audit row.

## 6. Planned, not built

- PLANNED: actor-key aliases on secret rotation, the `X-MIP-Actor-Key` request binding and the `409 actor_changed` contract: D-identity-review-a4 (w5-lq-requests-binding). Until then `aliases` is always empty.
- Known residual windows until a4 is the backstop: an audited request can still leave under the new cookie (1) between `resetDocument()` and the old document's unload, and (3) from a live Genie turn that keeps polling while a cross-tab suspicion awaits its recheck. Window (2), the 30 s hold, is closed by the hold-path swap (no routed reader is mounted when the cache clears). Imperative prefetches (a hover during a hold) are not gated.
- Known over-clear (fails safe, not a leak): a local-area row-3 `cleared` at a first observation dispatches the Genie reset, which also removes the current actor's own session transcript. Narrowing it per area changes the event contract; it needs an owner decision (w5-lq-requests-binding).

## 7. Release notes (wave 5)

- The upgrade deploy drops every browser's pins and stored Genie conversation id once: no `mip.actorOwner` stamp exists yet, and unowned data is never adopted. This is the fail-closed outcome.
- Only the legacy shortcut value is adopted.
- A same-actor reload keeps the tab's transcript and in-flight turn, because `mip.actorCacheKey` already stamps the tab.
- The key derivation is byte-identical, so the deploy is an actor change to no open tab. Rolling `MIP_GENIE_ACTION_SECRET_CURRENT` back to a value that is not published as an alias (a4) resets every open tab once.

## 8. Tests

Unit: `tests/unit/test_actor_identity.py`, `frontend/src/lib/healthTrust.test.ts`, `actorScope.test.ts`, `actorScope.reset.test.ts` (the reset order, the hold, the never-reopen invariant, the storage listener, the stamp guard, the notice), `actorScope.registry.test.ts`, `queryPersist.test.ts`, `components/healthPoll.recheck.test.tsx`, `components/layout/AppShell.actorBoundary.test.tsx`, `AppShell.actorBoundary.nobody.test.tsx` (the 401 / 403 / anonymous matrix, the same-page reopen and resume, a proven change from closed), `AppShell.actorBoundary.midSession.test.tsx` (cases (ix)-(xiv) under a data router: the reset, a first observation, the cross-tab path, unsaved work, the hold, the notice), `GenieDock.resume.test.tsx`, `HealthProvider.identity.test.tsx`, `components/mortgage/GenieChat.actorGate.test.tsx`, `components/mortgage/bulkApproveStash.test.ts`, `hooks/useUnsavedGuard.test.tsx`. Browser: `frontend/tests/e2e/fixture/identity-boundary.fixture.spec.ts` (a new tab after an actor change, the mid-session reset from `/lead-queue` and from a dirty `/portfolio-builder`, the two-page suspension, `/ask-genie` mounted while the gate is pending, the persisted cache) and the actor cases of `delivery-boot`, `genie-jobs` and `genie-turn`. Fixture specs seed private data with `seedOwnedStorage` (see `docs/testing.md`, "Owned storage in fixture specs").
