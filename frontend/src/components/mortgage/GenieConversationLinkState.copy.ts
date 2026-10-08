/**
 * The conversation deep link's fixed copy (audit 2026-09-21 `shell-03`). A
 * tiny module of its own so the route (ask-genie.deep-link.tsx, which
 * announces a failed Retry) and the lazy GenieConversationLinkState chunk
 * share one string without the route pulling the lazy chunk in. Neutral by
 * design: a 404, a 403 and a malformed id read the same, and no copy ever
 * carries error text.
 */

export const GENIE_LINK_NOT_FOUND_COPY = "This conversation isn't available.";
export const GENIE_LINK_UNAVAILABLE_COPY = "This conversation can't be loaded right now. Nothing was changed.";
/** Announced when a Retry fails again (not for a 404/403, which reads as not found). */
export const GENIE_LINK_RETRY_FAILED_COPY = "This conversation still can't be loaded. Nothing was changed.";
