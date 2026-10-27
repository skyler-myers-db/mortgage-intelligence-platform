# Mortgage Intelligence Platform, Module 0: Accessibility Conformance Report

WCAG Edition, based on the ITI VPAT® Version 2.5Rev WCAG template (WCAG 2.2 Level A and AA).

VPAT® is a registered service mark of the Information Technology Industry Council (ITI). This report follows the template's structure; confirm the current ITI edition before it is published to a buyer (audit `a11y-08`).

## Name of product and version

Mortgage Intelligence Platform, Module 0 (Top-of-Funnel Lead Generation and Borrower Segmentation): the React web application served as a Databricks App. Evaluated at source revision `main` 550dcf97 (waves 0 to 5 merged, PRs #269 to #272) plus the branch `post-w5/screen-reader-vpat` that adds this report, the virtual screen-reader pass and the three label fixes it found.

## Report date

2026-10-26.

## Product description

A browser application for mortgage lenders. It ranks borrowers from governed Unity Catalog data, explains each score with source evidence, drafts outreach that a person must review and approve, records every decision in an audit ledger, and answers questions through a Genie panel. Routes in scope: Home, Portfolio Builder, Segment Intelligence, Lead Queue, Borrower 360, Offer Orchestrator, Analytics, Ask Genie, Glossary, asset detail, Admin configuration and the Audit ledger, with the shell's rail, topbar, command palette, Console, evidence drawer, toasts and floating Genie panel.

Out of scope: Databricks workspace sign-in (the App is reached through the workspace's own authentication), the Databricks UI (Genie space administration, dashboards, Catalog Explorer, which the product links to), and outreach messages (the product never sends one).

## Contact information

Not yet supplied. An accessibility contact for this report needs an owner decision (the audit's `MIP_SUPPORT_CONTACT` follow-up is not built). Open item 1.

## Notes

- **This is a self-assessment from automated and code-level evidence. No person using assistive technology has evaluated the product.** Read "Supports" below as "the evidence listed found no failure", never as a certified result. The ratings are conservative: where a criterion was not exercised, it is rated by what the code and tests show, and the remark says what was not verified.
- Every remark cites evidence that exists in this repository. Paths are relative to the repository root.
- The audit that produced most of the findings cited here is `docs/audits/ui-ux-technology-audit-2026-09-21.md` with its register `docs/audits/ui-ux-technology-audit-2026-09-21-findings.md` (finding ids such as `a11y-04`).

## Evaluation methods used

What was done:

1. **axe-core in the pull-request gate.** `frontend/tests/e2e/fixture/axe.fixture.spec.ts` runs axe-core (through `@axe-core/playwright` 4.13.0, the one helper `frontend/tests/e2e/fixture/axe.ts`) with the `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` and `wcag22aa` tags over the production build against the synthetic fixture API: the default state of every route in the dark and light themes; the evidence drawer, command palette and Genie panel on the five core routes; Lead Queue filter menu, expanded row, review, request and revoke states; the teal, navy and red accents on three routes; and more (see `docs/testing.md`, "axe gate"). Any WCAG-tagged violation fails the build at every impact. The ratchet of accepted violations (`KNOWN_VIOLATIONS` in `axe.ts`) is empty: no known axe violation is accepted. Best-practice findings are advisory.
2. **oxlint jsx-a11y ratchet.** `npm --prefix frontend run lint:a11y` (`tools/oxlint_ratchet.mjs`, config `frontend/.oxlintrc.json`, baseline `frontend/oxlint-baseline.json`) runs all 35 enabled jsx-a11y rules of oxlint 1.86.0 over `frontend/src` on every pull request. Twelve accepted hits remain in six files, all in the hero map, the Score Spine and the toast region (listed under 2.1.1 and 4.1.2); suppressions are banned.
3. **Contrast.** `frontend/src/design-system/tokenContrast.test.ts` resolves the token cascade for both themes and all four accents and gates text pairs at 4.5:1 and UI pairs at 3:1, with no documented exception left. `frontend/tests/e2e/fixture/contrastStates.fixture.spec.ts` proves the light-theme states axe's default pages never render (warning ink, amber glyphs, the active drawer tab, focus rings on three inputs, the Sankey focus stroke, accent ink).
4. **Forced colors and reduced motion.** `frontend/tests/e2e/fixture/css-hygiene.forced-ink.fixture.spec.ts` and `css-hygiene.modes.fixture.spec.ts` (Chromium, emulated `forcedColors: 'active'`) and `forced-colors.firefox.fixture.spec.ts` (real Firefox forced colors in CI's `e2e-cross-engine` job) sample painted pixels. Every fixture project runs with `reducedMotion: 'reduce'` (`frontend/playwright.config.ts`).
5. **Keyboard.** Fixture and Vitest specs drive the keyboard paths: `queue-keyboard.fixture.spec.ts`, `filter-listbox.fixture.spec.ts`, `queue-clearance.cross-engine.fixture.spec.ts` and `approval-core-pinned-focus.cross-engine.fixture.spec.ts` (also in WebKit), `focus-clearance-cost.fixture.spec.ts`, `headings.fixture.spec.ts`, `aria-snapshots.fixture.spec.ts` (all under `frontend/tests/e2e/fixture/`), plus `frontend/src/components/mortgage/LeadTable.hotkeys.test.tsx`, `frontend/src/routes/portfolio-builder.state-picker.test.tsx`, `frontend/src/routes/analytics.tabs.keyboard.test.tsx` and the component suites for the menus, palette and drawer.
6. **Live canaries, not run for this report.** `frontend/tests/e2e/accessibility.spec.ts` (axe on the deployed app) and `frontend/tests/e2e/accessibility_procurement.spec.ts` (skip-link focus, named controls, a 24 px target floor, Lead Queue row metadata, reduced motion) run only with `E2E_LIVE=1` in the on-demand release job of `.github/workflows/nightly.yml`. They are cited where they are the only browser proof, and none of their results is claimed for this date.
7. **Virtual screen-reader pass (new with this report).** `frontend/src/test/screenReader/` drives `@guidepup/virtual-screen-reader` 0.33.0 over the real components in Vitest (happy-dom) and records what it speaks as committed golden transcripts in `frontend/src/test/screenReader/transcripts/`, each step asserted: the app shell's skip link, landmarks and route announcement (`shell.txt`); the Lead Queue table, a row's actions, the approval review and its announcement (`lead-queue.txt`); the evidence drawer (`evidence-drawer.txt`); the command palette (`command-palette.txt`); the identity menu (`identity-menu.txt`); the Genie panel's answer and its provenance (`genie-panel.txt`); a form with validation errors (`form-validation.txt`). The pass found three labelling defects, fixed on the same branch (open item 9 lists what it found and left).

What the virtual reader cannot model, so none of the following is evidenced: pronunciation (it passes U+2212 "−" through as a character; that NVDA, JAWS or VoiceOver read it as "minus" is expected from their symbol tables but unverified); following `aria-activedescendant` (it never speaks the newly active option on an arrow key); an inserted `role="alert"` element (it speaks only changes inside an existing live region); the inert page behind a native `showModal()` dialog; the typed text of a `<textarea>`; speech queueing and interruption; verbosity settings; braille; browse versus focus mode; and anything computed from layout.

What was **not** done:

- **No NVDA or JAWS pass on Windows.** NVDA and JAWS run only on Windows; this evaluation host is macOS, and JAWS also needs a licence.
- **No live VoiceOver pass.** Scripting VoiceOver on macOS needs a system security setting change that was out of bounds for this evaluation, and no person ran it by hand.
- **No TalkBack, Narrator, Dragon (speech input), switch access or screen-magnifier pass.**
- **No testing with people who use assistive technology.**
- No mobile or tablet assistive-technology pass, and no manual WCAG audit of every route by a trained evaluator.

These are open items 2 to 5 for a person.

## Applicable standards and guidelines

| Standard / guideline | Included in report |
| --- | --- |
| Web Content Accessibility Guidelines 2.0 | Level A (Yes), Level AA (Yes), Level AAA (No) |
| Web Content Accessibility Guidelines 2.1 | Level A (Yes), Level AA (Yes), Level AAA (No) |
| Web Content Accessibility Guidelines 2.2 | Level A (Yes), Level AA (Yes), Level AAA (No) |

WCAG 2.2 includes every WCAG 2.0 and 2.1 Level A and AA criterion except 4.1.1 Parsing, which is listed with its WCAG 2.2 note. The Revised Section 508 and EN 301 549 editions are not produced (the audit recommends the WCAG edition first and an INT edition only when a federal or EU buyer asks).

## Terms

- **Supports**: the functionality of the product has at least one method that meets the criterion without known defects, or meets it with equivalent facilitation.
- **Partially Supports**: some functionality of the product does not meet the criterion.
- **Does Not Support**: the majority of product functionality does not meet the criterion.
- **Not Applicable**: the criterion is not relevant to the product.
- **Not Evaluated**: not used (the template allows it only for Level AAA, which is not in this report).

## Summary

| Level | Supports | Partially Supports | Does Not Support | Not Applicable |
| --- | --- | --- | --- | --- |
| A (32 rows, 4.1.1 included) | 22 | 5 | 0 | 5 |
| AA (24 rows) | 14 | 6 | 0 | 4 |

## WCAG 2.2 Report

### Table 1: Success Criteria, Level A

| Criteria | Conformance Level | Remarks and Explanations |
| --- | --- | --- |
| 1.1.1 Non-text Content (Level A) | Supports | Decorative icons are `aria-hidden` (`frontend/src/components/Icon.test.tsx`); axe's image and SVG name rules gate every scanned state. The hero map's states carry data-rich labels and the map has a table view (`frontend/src/components/mortgage/USChoroplethMapTable.tsx`, audit `a11y-04`); charts have caption and table alternatives (`frontend/src/components/charts/ChartFrame.tsx`). The virtual pass reads the Entrada logo as "image, Entrada" (`shell.txt`). |
| 1.2.1 Audio-only and Video-only (Prerecorded) (Level A) | Not Applicable | The product has no audio or video content. |
| 1.2.2 Captions (Prerecorded) (Level A) | Not Applicable | The product has no synchronized media. |
| 1.2.3 Audio Description or Media Alternative (Prerecorded) (Level A) | Not Applicable | The product has no synchronized media. |
| 1.3.1 Info and Relationships (Level A) | Supports | Landmarks, one h1 per route with h2 and h3 section titles (`headings.fixture.spec.ts`, audit `a11y-03`), native tables with column headers, `aria-sort` and row indexes (`frontend/src/components/mortgage/LeadTable.aria.test.tsx`, `aria-snapshots.fixture.spec.ts`), labelled form fields and groups. The virtual pass reads these relationships (`shell.txt`, `lead-queue.txt`, `form-validation.txt`). The pass found and fixed two names ARIA 1.2 prohibits on generic elements and a hint duplicated into a field name (open item 9 keeps the one it left, ScoreBadge). |
| 1.3.2 Meaningful Sequence (Level A) | Supports | DOM order is the reading order; the seven transcripts read each flow in its visual order. The floating Genie launcher follows `main` in the DOM, outside every landmark (`shell.txt`), and is reachable in sequence. |
| 1.3.3 Sensory Characteristics (Level A) | Supports | No instruction found that relies only on shape, colour, size, location or sound in the recorded flows; the one spatial instruction, the Genie panel resize ("Drag any edge or corner, or use arrow keys", `genie-panel.txt`), also names the keyboard way. Not a full copy review of every route. |
| 1.4.1 Use of Color (Level A) | Supports | Scores show their number and band, not colour only (`frontend/src/components/mortgage/ScoreBadge.tsx`); status chips carry text; map fill tiers are repeated in each state's label and in the table view (`a11y-04`); the meter has text (`lead-queue.txt`: "Recommendation signal strength 80 percent"). |
| 1.4.2 Audio Control (Level A) | Not Applicable | The product plays no audio on any page. |
| 2.1.1 Keyboard (Level A) | Partially Supports | The product flow is keyboard-operable and pinned: the Lead Queue (`queue-keyboard.fixture.spec.ts`, `LeadTable.hotkeys.test.tsx`), filter listboxes (`filter-listbox.fixture.spec.ts`, `a11y-02`), the Portfolio Builder state picker (`portfolio-builder.state-picker.test.tsx`, `a11y-v1`), tabs (`analytics.tabs.keyboard.test.tsx`), menus, palette, drawer and review (transcripts). Not supported: moving the floating Genie panel is drag-only (resizing has arrow keys, `frontend/src/components/mortgage/useGenieWindow.ts`). The oxlint baseline keeps mouse handlers on non-interactive elements in the map (`USChoroplethMap.tsx`, `USChoroplethMapStates.tsx`, `USChoroplethMapZipLevel.tsx`), the Score Spine bar (`ScoreSpine.tsx`, an `aria-hidden` shortcut to components also listed as controls) and the toast region (`Toaster.tsx`); the map's roving keyboard path is tested, but these hits are not individually proven to have keyboard equivalents. |
| 2.1.2 No Keyboard Trap (Level A) | Supports | Modal layers are native `<dialog>`s opened with `showModal()` and close on Escape through one shared Escape stack (audit `a11y-07`); focus returns to the opener (`evidence-drawer.txt`, `command-palette.txt`, `identity-menu.txt`, `genie-panel.txt`; `frontend/src/components/mortgage/EvidenceDrawer.test.tsx`). |
| 2.1.4 Character Key Shortcuts (Level A) | Supports | The Lead Queue's single-key shortcuts (J, K, Enter, X, A, R, Shift+A) work only while focus is in the table region and no overlay is open (`frontend/src/components/mortgage/useLeadTableHotkeys.ts`, audit `a11y-09`), and the Console's "Single-key shortcuts" switch turns off every bare single-key shortcut, the topbar's `/` included (`frontend/src/lib/keymap.ts`, `frontend/src/components/layout/Console.tsx`); pinned by `LeadTable.hotkeys.test.tsx` and `queue-keyboard.fixture.spec.ts`. The approve key opens the review; it never approves by itself (`lead-queue.txt`). |
| 2.2.1 Timing Adjustable (Level A) | Supports | The product sets no time limit on a task. Success toasts leave after 8 s but pause while the pointer or focus is on them, and failure toasts stay until dismissed (`frontend/src/components/feedback/Toaster.tsx`, `Toaster.test.tsx`); the outcome stays on the page and in the audit ledger. The session lifetime is set by the Databricks workspace's authentication, not by the product; when it ends, a dialog says so (`frontend/src/components/layout/SessionExpiredDialog.tsx`). |
| 2.2.2 Pause, Stop, Hide (Level A) | Partially Supports | Several indicators loop indefinitely (the topbar heartbeat, the freshness pulse in the palette, the Genie launcher ring, the degraded-banner pulse, the map pulse, loading shimmer: `frontend/src/design-system/components/*.css`) and stop only under the operating system's reduced-motion preference; there is no in-product pause control. A running Genie turn has Stop (`genie-stop-context.fixture.spec.ts`). |
| 2.3.1 Three Flashes or Below Threshold (Level A) | Supports | No flashing content: the animations are opacity, transform and slow pulse transitions on the motion tokens. Not measured with a flash-analysis tool. |
| 2.4.1 Bypass Blocks (Level A) | Supports | "Skip to main content" is the first Tab stop and targets a focusable `main`; the landmarks are Primary navigation, banner, Breadcrumb navigation, search, main and the Notifications region (`shell.txt`, `frontend/src/components/layout/AppShell.test.tsx`). A Lead Queue "Skip table" link passes the table (`lead-queue.txt`). The browser focus move on activation is proven only by the live canary (Evaluation methods, item 6). |
| 2.4.2 Page Titled (Level A) | Supports | Each route sets its own document title, for example "Lead Queue · Mortgage Intelligence Platform" (`frontend/src/components/layout/AppShell.continuity.test.tsx`, audit `a11y-03`). |
| 2.4.3 Focus Order (Level A) | Supports | A route change focuses the page's h1 and announces it (`shell.txt`, `frontend/src/hooks/useRouteAnnouncer.test.tsx`); dialogs, menus and the palette return focus to their opener; an approval returns focus to the table region and moves its cursor to the next row (`lead-queue.txt`); a dismissed toast hands focus on (`Toaster.test.tsx`). |
| 2.4.4 Link Purpose (In Context) (Level A) | Supports | Links have descriptive names (axe `link-name` gates every scanned state; examples in the transcripts: "Skip to main content", "Skip table"). |
| 2.5.1 Pointer Gestures (Level A) | Supports | No function needs a multipoint or path-based gesture; the map drills with single clicks or the keyboard, and panel move and resize are single-pointer drags. |
| 2.5.2 Pointer Cancellation (Level A) | Supports | Controls are native buttons and links, activated on the up event; the drawer's backdrop dismisses only when both the press and the click land outside the dialog (`EvidenceDrawer.test.tsx`). |
| 2.5.3 Label in Name (Level A) | Partially Supports | Most names begin with the visible label ("Approve B-REVIEW0000001", "Export 0 leads as CSV", "Edit question"; `lead-queue.txt`, `genie-panel.txt`). Two found by the virtual pass do not contain their visible text: the Genie "New thread" button is named "Start a new Genie thread", and the Lead Queue row toggle shows the borrower id and CLIP but is named "Toggle preview for lead B-…" without the CLIP. Speech-input users saying the visible words may not reach them. Open item 9. |
| 2.5.4 Motion Actuation (Level A) | Not Applicable | No function is operated by device motion. |
| 3.1.1 Language of Page (Level A) | Supports | `<html lang="en">` in `frontend/index.html`. |
| 3.2.1 On Focus (Level A) | Supports | In every recorded walk and keyboard spec, focus alone never navigates, submits or opens a window; tooltips and evidence previews that appear on focus do not move focus. |
| 3.2.2 On Input (Level A) | Supports | Filters update the table in place and keep focus (`filter-listbox.fixture.spec.ts`); no form submits or navigates on a change of value. |
| 3.2.6 Consistent Help (Level A) | Supports | The topbar's account menu offers Keyboard shortcuts and the Glossary on every page, in the same place (`identity-menu.txt`). There is no human contact mechanism yet (open item 1). |
| 3.3.1 Error Identification (Level A) | Partially Supports | A refused submission is described in text and the field is marked invalid and described by the message (`form-validation.txt`: the governed-text refusal). An empty required reason is refused by moving focus to the field, which is announced as required, with no error text (`RequestApprovalForm.tsx`, `RevokeApprovalPanel.tsx`). Whether a screen reader speaks the inserted `role="alert"` is unverified (Evaluation methods). |
| 3.3.2 Labels or Instructions (Level A) | Supports | Fields have visible labels and hints ("Reason for the request", "Say why these borrowers; leave out personal details.", `form-validation.txt`); numeric editors use the shared Field primitive with unit and hint (`frontend/src/components/ui/Field.tsx`). |
| 3.3.7 Redundant Entry (Level A) | Supports | No multi-step process asks again for information already given: the Portfolio Builder keeps its draft (`frontend/src/routes/portfolio-builder.draft.test.tsx`), and a review shows the exact draft it approves (`lead-queue.txt`). |
| 4.1.1 Parsing (Level A) | Supports | Removed from WCAG 2.2 as obsolete; for WCAG 2.0 and 2.1 the 2023 errata treat it as always satisfied for HTML. React renders the DOM, so there is no hand-written markup to parse. |
| 4.1.2 Name, Role, Value (Level A) | Partially Supports | The transcripts show names, roles and states for the skip link, landmarks, menu button and menu items (checked, position), combobox and listbox (expanded, active descendant, selected), tabs (selected, controls), dialogs, the table (sort state, row index), meters and form fields (required, invalid) (`frontend/src/test/screenReader/transcripts/`). Gaps: ScoreBadge names a `<span>` with `aria-label`, which ARIA 1.2 prohibits on a generic and screen readers may skip (the visible score is still read; `ScoreBadge.tsx`, open item 9); the map table's footer row has two empty cells oxlint reports as unlabelled (`USChoroplethMapTable.tsx`). |

### Table 2: Success Criteria, Level AA

| Criteria | Conformance Level | Remarks and Explanations |
| --- | --- | --- |
| 1.2.4 Captions (Live) (Level AA) | Not Applicable | The product has no live audio or video. |
| 1.2.5 Audio Description (Prerecorded) (Level AA) | Not Applicable | The product has no synchronized media. |
| 1.3.4 Orientation (Level AA) | Supports | Nothing locks orientation (no orientation media query or manifest lock in the source); layouts are tested at phone and short widths (`css-hygiene.fixture.spec.ts`: 375x812, 360x225, 320x256). Not tested on a physical device. |
| 1.3.5 Identify Input Purpose (Level AA) | Not Applicable | No field collects information about the user (sign-in happens in the Databricks workspace); the inputs are search, filter, reason and campaign fields. |
| 1.4.3 Contrast (Minimum) (Level AA) | Supports | Token gate for both themes and all four accents with no documented exception (`tokenContrast.test.ts`); axe `color-contrast` gates every scanned state, including the accent sweep, with no accepted violation; rendered light-theme states in `contrastStates.fixture.spec.ts` (audit `a11y-01`, closed). |
| 1.4.4 Resize Text (Level AA) | Partially Supports | Page zoom scales the px-based type, and the 720x450 and 320x256 viewport cases (200% and 400% of 1440x900 and 1280x1024) keep the heading and first panel reachable on Home and Lead Queue (`css-hygiene.fixture.spec.ts`). Not tested at 200% on the other routes. Font sizes are px, so a browser's text-only size preference is ignored (audit overflow note). |
| 1.4.5 Images of Text (Level AA) | Supports | The only image of text is the Entrada wordmark, a logotype. |
| 1.4.10 Reflow (Level AA) | Partially Supports | At 320x256 Home and Lead Queue keep their heading and first panel reachable (`css-hygiene.fixture.spec.ts`). Not tested on the other routes. The Lead Queue table and the map need two-dimensional scrolling, which the criterion exempts. |
| 1.4.11 Non-text Contrast (Level AA) | Supports | UI pairs are gated at 3:1 in both themes and all accents (`tokenContrast.test.ts`); focus rings and amber glyphs are proven at 3:1 on rendered states (`contrastStates.fixture.spec.ts`); forced-colors rendering keeps meters, dots, focus and selection visible (`forced-colors.firefox.fixture.spec.ts`, `css-hygiene.forced-ink.fixture.spec.ts`, audit `a11y-10`). |
| 1.4.12 Text Spacing (Level AA) | Partially Supports | Not tested. Controls and chips have token heights (`--control-h`, `--chip-h`) and many table cells truncate to one line, so the criterion's spacing overrides may clip text; rated Partially Supports until a spacing-override check is run (open item 7). |
| 1.4.13 Content on Hover or Focus (Level AA) | Partially Supports | Tooltips are dismissible with Escape and stay while the trigger is hovered or focused (`frontend/src/components/ui/Tooltip.tsx`). Neither tooltips nor the evidence hover card can be hovered (`pointer-events: none`), and the hover card, a passive `aria-hidden` preview of the drawer, has no Escape dismissal (`frontend/src/components/EvidenceHoverCard.tsx`). |
| 2.4.5 Multiple Ways (Level AA) | Supports | The rail navigation, breadcrumbs and the command palette's page search (`command-palette.txt`). |
| 2.4.6 Headings and Labels (Level AA) | Supports | Section titles are headings (`headings.fixture.spec.ts`); labels describe their fields, and the reason fields are now named by their label alone (`form-validation.txt`, `RevokeApprovalPanel.test.tsx`). |
| 2.4.7 Focus Visible (Level AA) | Supports | A shared focus ring at 3:1 in both themes and all accents, restored on the inputs that had removed it (`contrastStates.fixture.spec.ts`), and the system Highlight ring under forced colors (`forced-colors.firefox.fixture.spec.ts`). |
| 2.4.11 Focus Not Obscured (Minimum) (Level AA) | Partially Supports | Lead Queue focus clears the sticky route nav, header and bulk bar in Chromium and WebKit (`queue-clearance.cross-engine.fixture.spec.ts`, `focus-clearance-cost.fixture.spec.ts`, audit `a11y-v2`). Known residuals: a route nav that wraps on a narrow screen is under-cleared, and a header sort or select-all control focused after the page has scrolled the header under the nav stays covered (audit report, Lead Queue residuals). |
| 2.5.7 Dragging Movements (Level AA) | Partially Supports | Moving the floating Genie panel needs a drag (double-click only resets it); resizing it also works with the arrow keys (`useGenieWindow.ts`). No other drag-only function is known. |
| 2.5.8 Target Size (Minimum) (Level AA) | Supports | axe's `target-size` rule (tag `wcag22aa`) gates every scanned state; the live canary checks a 24 px floor on six routes. Map state shapes are exempt as essential geographic presentation (`data-target-size-exempt="geographic-shape"`) and the map's table view offers the same choices. |
| 3.1.2 Language of Parts (Level AA) | Supports | All content is English; no passage is in another language. |
| 3.2.3 Consistent Navigation (Level AA) | Supports | One shell (rail, topbar, breadcrumbs) on every route (`frontend/src/components/layout/AppShell.tsx`). |
| 3.2.4 Consistent Identification (Level AA) | Supports | Shared components give the same function the same name across routes (evidence chips, Approve and Reject, Close drawer, the toast actions). |
| 3.3.3 Error Suggestion (Level AA) | Supports | Refusals say how to fix the input ("Rewrite it without personal details or protected-class language.", `form-validation.txt`); numeric editors announce the clamp they applied (`Field.tsx`). |
| 3.3.4 Error Prevention (Legal, Financial, Data) (Level AA) | Supports | Outreach approval is a recorded commitment: Approve opens a review of the exact copy and approves only on Confirm (`lead-queue.txt`, `frontend/src/components/mortgage/LeadTable.approveReview.test.tsx`); a recorded approval can be revoked with a reason (`RevokeApprovalPanel.test.tsx`); bulk decisions require a shared rationale. |
| 3.3.8 Accessible Authentication (Minimum) (Level AA) | Not Applicable | The product has no sign-in step of its own; authentication is the Databricks workspace's. |
| 4.1.3 Status Messages (Level AA) | Supports | Status messages reach a persistent polite region without moving focus: the route announcer, toasts ("Approved B-REVIEW0000001", `lead-queue.txt`), the palette's search status (`command-palette.txt`) and the Genie panel's single announcer, which says each stage and "Answer ready" once, never the elapsed-seconds ticker (`genie-panel.txt`, `frontend/src/components/mortgage/GenieAnnouncerRegion.tsx`, audit `a11y-06`). Failures render as `role="alert"`; that a real screen reader speaks an inserted alert is unverified (open item 2). |

### Table 3: Success Criteria, Level AAA

Not included in this report.

## Open items for a person

1. **Accessibility contact.** An owner names the contact for this report and for users (the `MIP_SUPPORT_CONTACT` follow-up), and links it from the Glossary.
2. **NVDA with Chrome or Firefox on Windows**, over the seven flows the transcripts cover and both Genie surfaces (the floating panel and `/ask-genie`). Confirm in particular: U+2212 read as "minus" (the Lead Queue's negative rate spread, "−35"); the active option spoken on arrow keys in the palette and filter comboboxes; inserted `role="alert"` failures spoken; one "Answer ready" per Genie turn with no ticker chatter (audit `a11y-06` item 2); the identity menu button heard as "collapsed" after Escape (the virtual reader hears it one microtask early).
3. **VoiceOver with Safari on macOS**, the same checklist.
4. **JAWS** (licence needed) and, for mobile, VoiceOver on iOS and TalkBack on Android, if buyers ask.
5. **Testing with people who use assistive technology**, including speech input (Dragon or Voice Control) for 2.5.3 and screen magnification for 1.4.4 and 1.4.10.
6. **Re-confirm the ITI template edition** and decide whether a Section 508 or EN 301 549 (INT) edition is needed.
7. **Text spacing (1.4.12)**: run the criterion's overrides (line height 1.5, paragraph spacing 2, letter spacing 0.12, word spacing 0.16) on every route and fix clipping.
8. **Reflow and resize (1.4.4, 1.4.10)**: extend the 320x256 and 200% checks beyond Home and Lead Queue.
9. **Defects the virtual pass found and left**: ScoreBadge's `aria-label` on a `<span>` (4.1.2; give it a role that permits a name or move the sentence into hidden text); "New thread" named "Start a new Genie thread" and the row toggle's name without the CLIP (2.5.3); the Genie launcher outside every landmark (best practice); the evidence hover card's Escape and hover behaviour (1.4.13); the Genie panel's drag-only move (2.1.1, 2.5.7); the looping indicators with no in-product pause (2.2.2). Fixed on this branch: the request and revoke reason fields named by their label alone, and the names removed or permitted on the page footer and the review's chip row.

## Legal disclaimer

This report describes the Mortgage Intelligence Platform Module 0 web application at the revision named above, from the evidence listed. It is a self-assessment, not a certification, and it will change as the product and the open items above change.
