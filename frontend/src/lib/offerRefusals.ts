/**
 * The Offer's governed refusals, reviewed and closed (2026-09-21 audit
 * states-04, review fix 1). A 409 or 422 on an Offer write (approve, reject,
 * save draft) or on its draft read is the server refusing on purpose, and its
 * detail is usually the one sentence a reviewer can act on: "borrower hit
 * frequency cap; earliest re-contact 2026-11-02", "Borrower is not in the
 * saved campaign cohort.". describeApiError's generic conflict and validation
 * bodies ("Reload to read the current version.", "Clear filters or choose a
 * supported filter value.") are wrong on this surface: a reload re-runs its
 * audited reads, and it has no filters.
 *
 * So the detail is shown ONLY when it is one of the sentences below, written
 * by the backend for a reviewer, or matches one of the closed shapes whose
 * only open slot is a date, a field name or a reason token (never free text,
 * never an id). Anything else reads as the scope's neutral sentence, which
 * names no cause and gives no advice. The sources are cited per entry; a
 * backend sentence that drifts from this list fails closed to the neutral
 * sentence, never to the raw detail.
 *
 * A pure module with no imports. It rides the Offer's failure chunk
 * (components/mortgage/OfferFailureSentence), which loads with the first
 * failure on screen, never on the Offer's natural load.
 */

/** What the reviewer reads for a refusal, and whether a fresh draft answers it. */
export interface ReviewedOfferRefusal {
  sentence: string;
  /** True only for the draft-proof refusals: the server asks for a fresh audited draft. */
  regenerate: boolean;
}

/** What was refused: a read, a decision (approve / reject) or a draft save. */
export type OfferRefusalScope = 'request' | 'decision' | 'draft-save';

/** A refusal outside the reviewed set: what was not done, no cause, no advice. */
export const OFFER_REFUSED: Readonly<Record<OfferRefusalScope, string>> = {
  request: 'The server refused this request.',
  decision: 'The server refused this decision; it was not recorded.',
  'draft-save': 'The server refused this draft; it was not saved.',
};

/**
 * Draft-proof refusals: the decision or the save cites a generated draft the
 * server can no longer prove, and a fresh audited draft is the answer, so the
 * alert offers an explicit Regenerate draft.
 */
const DRAFT_PROOF_SENTENCES: ReadonlySet<string> = new Set([
  // POST /outreach/approve: backend/api/outreach.py
  'Campaign-bound approval requires matching generated draft proof.',
  // POST /outreach/approve: backend/services/outreach_drafts.py _verified_generated_draft
  'Approval requires the audited generated draft proof.',
  'Generated draft proof was not found; regenerate the draft before approval.',
  'Generated draft proof is invalid; regenerate the draft before approval.',
  'Generated draft proof does not match this approval; regenerate the draft.',
  'Borrower data changed after the draft was generated; regenerate before approval.',
  'Edited outreach copy cannot be approved from an older proof; regenerate an audited draft before approval.',
  // POST /workspace/drafts/{id}: backend/api/workspace.py
  'Generated draft proof is invalid; regenerate before saving.',
  'Generated draft no longer matches the reviewed copy; regenerate before saving.',
]);

/**
 * Campaign and decision refusals: shown as written. Regenerating the Offer
 * draft cannot answer them (the campaign, its copy or its cohort must change).
 */
const DECISION_REFUSAL_SENTENCES: ReadonlySet<string> = new Set([
  // backend/services/outreach_campaign_gate.py _resolve_governed_campaign_variant
  'Campaign lifecycle state does not allow outreach review.',
  'Campaign outreach cannot be approved before the campaign copy is approved.',
  'Campaign must be rebuilt before it can be used for outreach.',
  'Campaign targeting contract is invalid; rebuild the campaign.',
  'Borrower is not in the saved campaign cohort.',
  'campaign variant binding is invalid',
  'campaign variant content is invalid',
  'Campaign variant copy is not bound to durable server proof; regenerate the campaign message before approval.',
  // backend/services/outreach_decision_commit.py _lock_and_revalidate_campaign_decision
  'campaign decision proof is invalid',
  'Campaign lifecycle state changed before the outreach decision was saved.',
  'Campaign targeting proof changed before the outreach decision was saved.',
]);

/**
 * Closed shapes, each anchored at both ends. The open slot is a date, a
 * snake_case field name (never the refused text: outreach_text_policy.py
 * names the field only) or a snake_case reason token; an id, a name or a
 * sentence of free text cannot match.
 */
const REFUSAL_SHAPES: readonly RegExp[] = [
  // backend/services/outreach_campaign_gate.py _enforce_contact_eligibility; workspace.py
  /^borrower hit frequency cap; earliest re-contact \d{4}-\d{2}-\d{2}$/,
  // backend/services/outreach_text_policy.py text_policy_refusal
  /^[a-z_]+ failed the governed text policy$/,
  // backend/services/outreach_campaign_gate.py _enforce_contact_eligibility; workspace.py
  /^borrower is not marketing-eligible: [a-z_=]+$/,
];

/** The server's lower-case details read as sentences: a capital first letter and a full stop. */
function asSentence(text: string): string {
  const capped = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?]$/.test(capped) ? capped : `${capped}.`;
}

/**
 * The reviewed sentence for a refusal's detail, or null when the detail is
 * not one of them.
 */
export function reviewedOfferRefusal(detail: string): ReviewedOfferRefusal | null {
  if (DRAFT_PROOF_SENTENCES.has(detail)) return { sentence: detail, regenerate: true };
  if (DECISION_REFUSAL_SENTENCES.has(detail)) return { sentence: asSentence(detail), regenerate: false };
  if (REFUSAL_SHAPES.some((shape) => shape.test(detail))) {
    // Snake-case slots read as words: "bulk_rationale" -> "bulk rationale".
    return { sentence: asSentence(detail.replace(/_/g, ' ')), regenerate: false };
  }
  return null;
}

/** The sentence a reviewer reads for a refusal: the reviewed one, or the scope's neutral one. */
export function offerRefusalSentence(detail: string, scope: OfferRefusalScope): string {
  return reviewedOfferRefusal(detail)?.sentence ?? OFFER_REFUSED[scope];
}

/** True only when a fresh audited draft is what the server asks for. */
export function refusalOffersRegenerate(detail: string): boolean {
  return reviewedOfferRefusal(detail)?.regenerate === true;
}

/** Every exact sentence, so a test can hold each one to the backend source it cites. */
export const REVIEWED_REFUSAL_SENTENCES = {
  draftProof: [...DRAFT_PROOF_SENTENCES],
  decision: [...DECISION_REFUSAL_SENTENCES],
} as const;
