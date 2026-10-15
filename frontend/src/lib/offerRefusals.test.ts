/**
 * The Offer's reviewed refusal set (2026-09-21 audit states-04, review fix 1):
 * exact backend sentences and closed shapes pass, and nothing else does. Each
 * exact sentence is held to the backend source it cites, so a reworded
 * server sentence is caught here (in the app it fails closed to the neutral
 * sentence, never to the raw detail).
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the committed backend sources under Vitest only.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  OFFER_REFUSED,
  REVIEWED_REFUSAL_SENTENCES,
  offerRefusalSentence,
  refusalOffersRegenerate,
  reviewedOfferRefusal,
} from './offerRefusals';

const BACKEND_SOURCES = [
  'api/outreach.py',
  'api/workspace.py',
  'services/outreach_drafts.py',
  'services/outreach_campaign_gate.py',
  'services/outreach_decision_commit.py',
  'services/outreach_text_policy.py',
];

/** The backend's string literals, with Python's adjacent-literal concatenation joined. */
const backendText = BACKEND_SOURCES
  .map((path) => readFileSync(new URL(`../../../backend/${path}`, import.meta.url), 'utf8') as string)
  .join('\n')
  .replace(/"\s*\n\s*f?"/g, '');

describe('reviewedOfferRefusal (states-04)', () => {
  it.each(REVIEWED_REFUSAL_SENTENCES.draftProof)('a draft-proof sentence passes as written and offers Regenerate: %s', (sentence) => {
    expect(backendText).toContain(`"${sentence}"`);
    expect(reviewedOfferRefusal(sentence)).toEqual({ sentence, regenerate: true });
  });

  it.each(REVIEWED_REFUSAL_SENTENCES.decision)('a campaign or decision sentence passes without Regenerate: %s', (sentence) => {
    expect(backendText).toContain(`"${sentence}"`);
    const refusal = reviewedOfferRefusal(sentence);
    expect(refusal?.regenerate).toBe(false);
    expect(refusal?.sentence.toLowerCase()).toBe(sentence.toLowerCase().replace(/\.?$/, '.'));
  });

  it.each([
    ['borrower hit frequency cap; earliest re-contact 2026-11-02', 'Borrower hit frequency cap; earliest re-contact 2026-11-02.'],
    ['rationale failed the governed text policy', 'Rationale failed the governed text policy.'],
    ['bulk_rationale failed the governed text policy', 'Bulk rationale failed the governed text policy.'],
    ['borrower is not marketing-eligible: consent_status=opted_out', 'Borrower is not marketing-eligible: consent status=opted out.'],
    ['borrower is not marketing-eligible: do_not_contact', 'Borrower is not marketing-eligible: do not contact.'],
  ])('a closed shape reads as a sentence: %s', (detail, sentence) => {
    expect(reviewedOfferRefusal(detail)).toEqual({ sentence, regenerate: false });
  });

  it('the shapes are the ones the backend writes', () => {
    // f-strings: the open slot is a date, a field name or a reason token.
    expect(backendText).toContain('borrower hit frequency cap; earliest re-contact {');
    expect(backendText).toContain('detail=f"{exc.field} failed the governed text policy"');
    expect(backendText).toContain('f"borrower is not marketing-eligible: {reason}"');
    expect(backendText).toContain('f"borrower is not marketing-eligible: consent_status={');
  });

  it.each([
    'audit write failed: psycopg OperationalError at 10.0.0.12',
    'Reload the app to approve',
    'request_id already belongs to a different outreach decision',
    'Borrower B-0000000000001 not found',
    'rationale failed the governed text policy: Jane Doe',
    'Jane Doe failed the governed text policy',
    'borrower hit frequency cap; earliest re-contact soon',
    'borrower is not marketing-eligible: B-0000000000001',
    'borrower is not marketing-eligible: consent status opted out',
    ' Borrower is not in the saved campaign cohort.',
    'Borrower is not in the saved campaign cohort. Call Jane Doe.',
    '',
  ])('anything else is not reviewed: %j', (detail) => {
    expect(reviewedOfferRefusal(detail)).toBeNull();
    expect(refusalOffersRegenerate(detail)).toBe(false);
    expect(offerRefusalSentence(detail, 'decision')).toBe(OFFER_REFUSED.decision);
  });

  it('the neutral sentences name what was not done, never a cause or advice', () => {
    expect(OFFER_REFUSED).toEqual({
      request: 'The server refused this request.',
      decision: 'The server refused this decision; it was not recorded.',
      'draft-save': 'The server refused this draft; it was not saved.',
    });
    for (const sentence of Object.values(OFFER_REFUSED)) expect(sentence).not.toMatch(/reload|refresh|filter|try again/i);
  });

  it('only a draft-proof sentence offers Regenerate draft', () => {
    for (const sentence of REVIEWED_REFUSAL_SENTENCES.draftProof) expect(refusalOffersRegenerate(sentence)).toBe(true);
    for (const sentence of REVIEWED_REFUSAL_SENTENCES.decision) expect(refusalOffersRegenerate(sentence)).toBe(false);
    expect(refusalOffersRegenerate('borrower hit frequency cap; earliest re-contact 2026-11-02')).toBe(false);
  });
});
