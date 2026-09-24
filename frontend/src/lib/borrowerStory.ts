import type { Borrower360 } from '../types';
import { bpsLabel, compactCurrency, ratePct } from './formatters';
import { offerDisplayLabel } from './offerLanguage';

/**
 * "Tell the story" — the borrower narrative (re-audit Buyer-Wow #3). The
 * moment a buyer sees the system EXPLAIN a lead, not just rank it. Composed
 * DETERMINISTICALLY from the dossier's already-proof-backed fields (no live
 * Genie call → no booth latency/failure), and every numeric claim is routed
 * through a real verifier: each number in the prose must equal its source
 * dossier field, and no number may appear in the prose that isn't a verified
 * claim. That guarantee is what lets the UI badge the story "evidence-
 * verified" honestly.
 */

export interface NarrativeClaim {
  /** Human label for the claim ("Rate spread"). */
  label: string;
  /** The exact token as it appears in the narrative ("356 bps"). */
  token: string;
  /** The dossier field it is grounded in. */
  field: keyof Borrower360;
  /** True when the token's number equals the source field's value. */
  verified: boolean;
}

export interface BorrowerStory {
  sentences: string[];
  claims: NarrativeClaim[];
  /** True iff every claim verified AND no un-grounded number appears. */
  allVerified: boolean;
  /** Numbers found in the prose that map to no verified claim (should be []). */
  unverifiedTokens: string[];
}

/** Available equity % from display LTV; underwater LTV clamps the story to 0% equity. */
function equityPct(b: Borrower360): number | null {
  const hasAvm = typeof b.avm_value === 'number' && b.avm_value > 0;
  if (!hasAvm || typeof b.ltv !== 'number') return null;
  return Math.max(0, Math.min(100, 100 - b.ltv));
}

function ownerDescriptor(b: Borrower360): string {
  if (b.is_investor && (b.related_property_count ?? 0) > 1) return 'investor';
  if (b.is_current_customer) return 'current customer';
  if (b.is_former_customer) return 'former customer';
  if (b.is_owner_occupied) return 'owner-occupant';
  return 'homeowner';
}

/**
 * Compose the narrative and verify it. The prose is built from explicit
 * tokens; each token is registered as a claim tied to a field, then the
 * verifier (a) re-derives the field number and checks the token matches, and
 * (b) scans the finished prose for ANY number not covered by a claim.
 */
export function buildBorrowerStory(b: Borrower360): BorrowerStory {
  const claims: NarrativeClaim[] = [];
  const register = (token: string, label: string, field: keyof Borrower360, sourceNum: number): string => {
    // Parse the token's number, honoring a K/M magnitude suffix ($42K → 42000)
    // so an abbreviated display still verifies against the raw field value.
    let tokenNum = Number.parseFloat(token.replace(/[^0-9.]/g, ''));
    const scaled = /[kK]/.test(token) ? 1_000 : /[mM]/.test(token) ? 1_000_000 : 1;
    tokenNum *= scaled;
    // Exact match for un-scaled tokens; allow the rounding slack a K/M
    // abbreviation introduces (e.g. 42,500 → "$43K").
    const tolerance = scaled > 1 ? Math.max(1, sourceNum * 0.06) : 0.05;
    const verified = Number.isFinite(tokenNum) && Math.abs(tokenNum - sourceNum) <= tolerance;
    claims.push({ label, token, field, verified });
    return token;
  };

  // City/state can be null on a raw UC row — omit the locale rather than
  // interpolating "undefined, undefined".
  const locale = b.city && b.state ? `${b.city}, ${b.state} ` : '';
  const descriptor = ownerDescriptor(b);
  const props = b.related_property_count ?? 0;

  // Who they are. Registered first so claim order stays subject -> economics.
  const multiProperty = props > 1;
  const subject = `This ${locale}${descriptor}`;
  const holdings = multiProperty
    ? ` holds ${register(`${props} properties`, 'Related properties', 'related_property_count', props)} via the owner graph`
    : '';

  // The economic trigger: rate, spread, equity, lien. Each clause carries the
  // verb it needs when it has to follow the subject directly (`lead`); the
  // bare `text` is what a continuing clause, or a sentence of its own, uses.
  // `register` runs exactly once per figure either way.
  const parts: Array<{ text: string; lead: string }> = [];
  if (typeof b.current_rate === 'number' && b.current_rate > 0) {
    const clause = `carries a ${register(ratePct(b.current_rate), 'Current rate', 'current_rate', b.current_rate)} rate`;
    parts.push({ text: clause, lead: clause });
  }
  // Only claim "above market" for a genuine positive spread — a zero or
  // negative spread (at/below market) is not an above-market trigger, and
  // "-120 bps above market" would be backwards prose.
  if (typeof b.rate_spread_bps === 'number' && b.rate_spread_bps > 0) {
    const clause = `${register(bpsLabel(b.rate_spread_bps), 'Rate spread', 'rate_spread_bps', b.rate_spread_bps)} above market`;
    parts.push({ text: clause, lead: `is ${clause}` });
  }
  const eq = equityPct(b);
  if (eq !== null) {
    const token = register(`${Math.round(eq)}% equity`, 'Equity', 'ltv', Math.round(eq));
    parts.push({ text: `with ${token}`, lead: `has ${token}` });
  }
  const lienStr =
    parts.length > 0 && typeof b.current_lien_balance === 'number' && b.current_lien_balance > 0
      ? ` on a ${register(compactCurrency(b.current_lien_balance), 'Lien balance', 'current_lien_balance', b.current_lien_balance)} lien`
      : '';

  // Sentence 1 and 2. A multi-property owner gets the owner-graph sentence,
  // then the economics as a sentence of its own. Without that clause, "This
  // Chicago, IL homeowner." is a fragment — and it opened the story for EVERY
  // single-property borrower (2026-09-21 audit, visual-v2) — so the subject
  // takes the economics as its predicate instead. With neither, the subject
  // becomes a plain sentence; it adds no figure, so nothing new to verify.
  let s1: string;
  let s2 = '';
  if (multiProperty) {
    s1 = `${subject}${holdings}.`;
    if (parts.length > 0) {
      const joined = parts.map((p) => p.text).join(', ').replace(/^./, (c) => c.toUpperCase());
      s2 = `${joined}${lienStr}.`;
    }
  } else if (parts.length > 0) {
    const predicate = [parts[0].lead, ...parts.slice(1).map((p) => p.text)].join(', ');
    s1 = `${subject} ${predicate}${lienStr}.`;
  } else {
    // `descriptor` is a closed set (ownerDescriptor), so a vowel test is exact.
    const article = /^[aeiou]/i.test(descriptor) ? 'an' : 'a';
    const where = b.city && b.state ? ` in ${b.city}, ${b.state}` : '';
    s1 = `This borrower is ${article} ${descriptor}${where}.`;
  }

  // Sentence 3 — the recommendation (no number to verify; the offer is text).
  const offer = offerDisplayLabel(b.recommended_offer_code, b.recommended_offer);
  const s3 = b.recommended_offer
    ? `Primary offer: ${offer}.`
    : 'Reviewed for a primary offer path.';

  const sentences = [s1, s2, s3].filter((s) => s && s.trim().length > 1);

  // Verifier pass (b): scan the CLAIM-BEARING sentences (s1, s2) for any
  // number not covered by a registered claim — an un-grounded figure is a
  // template bug. The recommendation sentence (s3) is deliberately excluded:
  // it carries a categorical offer label ("5/1 ARM", "30-year refi") whose
  // digits are part of a product name, not a numeric claim about the
  // borrower, so they must not be flagged as unverified.
  //
  // COUNT-based (not set-membership): if the same digit string appears in the
  // prose more times than it is claimed (e.g. a city "District 41" colliding
  // with a property count of 41), the EXCESS occurrence is flagged — a
  // set-membership check would silently let the stray number ride on the
  // claim. This is a template-bug tripwire for our closed token set, not a
  // general adversarial guard.
  const claimCounts = new Map<string, number>();
  for (const c of claims) {
    const n = c.token.replace(/[^0-9.]/g, '');
    claimCounts.set(n, (claimCounts.get(n) ?? 0) + 1);
  }
  const verifiableProse = [s1, s2].filter(Boolean).join(' ');
  const proseNumbers = verifiableProse.match(/\d+(?:\.\d+)?/g) ?? [];
  const usedCounts = new Map<string, number>();
  const unverifiedTokens: string[] = [];
  for (const n of proseNumbers) {
    const used = usedCounts.get(n) ?? 0;
    if (used >= (claimCounts.get(n) ?? 0)) unverifiedTokens.push(n);
    usedCounts.set(n, used + 1);
  }

  const allVerified = claims.every((c) => c.verified) && unverifiedTokens.length === 0;

  return { sentences, claims, allVerified, unverifiedTokens };
}
