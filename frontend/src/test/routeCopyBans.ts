/**
 * Plumbing words that never appear in buyer-facing route copy (2026-09-21
 * audit flow-10). Lifted from ask-genie.tabs.test.tsx, which imports it
 * unchanged; routes/routeCopy.lint.test.tsx lints the Segments copy module
 * and the Portfolio save guard against the same list plus the set algebra
 * the Segments page used to speak. Test-only (src/test is never shipped).
 */
export const ROUTE_COPY_BANS: readonly RegExp[] = [
  /Conversation API/i,
  /Agent Responses/i,
  /capabilit/i,
  /preflight/i,
  /de-duplicat/i,
  /\bsemantics\b/i,
  /hand-editing/i,
  /\bSQL\b/,
  /\bUC\b/,
  /Module 0/,
  /scheduler/i,
];

/** Set algebra the Segments page used to explain itself in (flow-10). */
export const SET_ALGEBRA_BANS: readonly RegExp[] = [/\bOR cohort\b/i, /\bAND intersection\b/i, /\bde-dup/i];
