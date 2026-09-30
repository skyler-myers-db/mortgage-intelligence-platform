# Identity boundary: whose data the browser shows

Module 0 runs on shared machines (booths, loaner laptops, one browser for a team). The server never trusts the browser, but the browser still keeps some private working state: pinned Genie answers, the Genie conversation id, transcript and in-flight turn, the Lead Queue context behind the dossier pager, the bulk-approve stash, and a snapshot of non-PII aggregates. This page says how that state is tied to the signed-in actor, so one operator's data never renders for the next.

Decisions: D-identity-review-a1, D-identity-review-b, D-identity-review-a2 step 1 (audit 2026-09-21 `genie-02` item 1, `bundle-04` item 4, `delivery-05`). Code: `backend/services/actor_identity.py`, `frontend/src/lib/healthTrust.ts`, `frontend/src/lib/actorScope.ts`, `frontend/src/lib/queryPersist.ts`.

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

Unowned data is never adopted, not even by nobody. The gate is **open** only when both areas resolve open. Before the rows, a document-level check: once this document has opened for a real actor A (`documentOwnerKey()`, held in memory only, never cleared by a close), a different real `k` that is not an alias of A is a proven change and goes to a replaceable handler. Its interim default runs the rows and makes `k` the document owner; D-identity-review-a3 (W5b) replaces it with a document reset. Because the check is per document, another tab rewriting the shared local stamp cannot hide the change.

On resolution the gate replays queued updaters against the post-clear values (open) or drops them (closed); while closed, writes are dropped. Events, in order: `cleared` (a stored private key was removed, or the proven-change default ran), `restamped` (a stamp now names another owner), `opened` / `closed` (a status change). Storage that throws holds values and stamps in memory for the document, so row 1 applies at every document start.

**The W5a nobody bridge.** Row 5 keeps storage but closes the gate, so the stores read nothing. The shell also clears its in-memory actor state on `closed`, as it does on `cleared`: the TanStack query cache, the last borrower, approvals, the drawer and workspace state, the route-local caches, and the live Genie turn (the Genie reset event). The removals that reset triggers are dropped by the closed gate, so a same-actor reopen (after a 401 or 403 clears up) shows the pins and the transcript again and a reload resumes the turn. The same-page resume re-arm and the 401 remount cases are D-identity-review-a2's remainder (W5b).

## 3. Every storage key, by class

`actorScope.registry.test.ts` parses every non-test source under `frontend/src` and `frontend/public` and fails on an unregistered `mip` key, a storage call outside the reviewed files, or a whole-area `.clear()`, index, enumeration or `for-in`.

| Class | Keys | Rule |
|---|---|---|
| PRIVATE_LOCAL | `mip.pinnedInsights`, `mip.genie.conversationId` | through the gate only; removed by rows 1/3/4 |
| PRIVATE_SESSION | `mip-genie-conversation-v1`, `mip.genie.inFlightTurn`, `mip.queueContext`, `mip.bulkApprove.lastCancelled`, `mip.queryCache.v1` | same; `bulkApproveStash.ts` still touches its key directly (TRANSITIONAL until w5-identity-reset-portfolio) |
| ACTOR_PREFERENCE_LOCAL | `mip.shortcuts.singleKey` | a per-actor map, never cleared |
| DEVICE_LOCAL | `mip.theme`, `mip.accent`, `mip.density`, `mip.consoleOpen`, `mip-genie-chat-size-v1`, `mip-genie-chat-pos-v1`; pre-registered `mip.themeChosen`, `mip.accentChosen` | device preferences, not actor data |
| DEVICE_SESSION | `mip.mainScroll.v1`, `mip.leadTableScroll.v1`, `mip.rumApiSample`, `mip.staleChunkReloadAt`; pre-registered `mip.actorResetNotice`, `mip.actorResetAt` | per-tab device state |
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
- Nothing here reads an audited endpoint or writes an audit row.

## 6. Planned, not built

- Actor-key aliases on secret rotation, the `X-MIP-Actor-Key` request binding and the `409 actor_changed` contract: D-identity-review-a4 (w5-lq-requests-binding). Until then `aliases` is always empty.
- The proven-change document reset, the cross-tab `storage` listener with a health recheck, and the `mip.actorResetNotice` / `mip.actorResetAt` notice: D-identity-review-a3 (w5-identity-reset-portfolio).

## 7. Release notes (wave 5)

- The upgrade deploy drops every browser's pins and stored Genie conversation id once: no `mip.actorOwner` stamp exists yet, and unowned data is never adopted. This is the fail-closed outcome.
- Only the legacy shortcut value is adopted.
- A same-actor reload keeps the tab's transcript and in-flight turn, because `mip.actorCacheKey` already stamps the tab.
- The key derivation is byte-identical, so the deploy is an actor change to no open tab. Rolling `MIP_GENIE_ACTION_SECRET_CURRENT` back to a value that is not published as an alias (a4) resets every open tab once.

## 8. Tests

Unit: `tests/unit/test_actor_identity.py`, `frontend/src/lib/healthTrust.test.ts`, `actorScope.test.ts`, `actorScope.registry.test.ts`, `queryPersist.test.ts`, `components/layout/AppShell.actorBoundary*.test.tsx`, `GenieDock.resume.test.tsx`, `HealthProvider.identity.test.tsx`. Browser: `frontend/tests/e2e/fixture/identity-boundary.fixture.spec.ts` (a new tab after an actor change, the persisted cache) and the actor cases of `delivery-boot`, `genie-jobs` and `genie-turn`. Fixture specs seed private data with `seedOwnedStorage` (see `docs/testing.md`, "Owned storage in fixture specs").
