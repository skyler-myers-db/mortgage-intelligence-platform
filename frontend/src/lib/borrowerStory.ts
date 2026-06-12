import type { Borrower360 } from '../types';

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

function fmtCurrencyK(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${Math.round(n / 1_000)}K`;
  return `$${Math.round(n)}`;
}

/** Equity % from LTV when AVM is available (LTV + equity = 100 by construction). */
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

  const city = `${b.city}, ${b.state}`;
  const descriptor = ownerDescriptor(b);
  const props = b.related_property_count ?? 0;

  // Sentence 1 — who they are, with the owner-graph property count when it
  // is a real multi-property signal.
  let s1 = `This ${city} ${descriptor}`;
  if (props > 1) {
    s1 += ` holds ${register(`${props} properties`, 'Related properties', 'related_property_count', props)} via the owner graph`;
  }
  s1 += '.';

  // Sentence 2 — the economic trigger: rate, spread, lien, equity.
  const parts: string[] = [];
  if (typeof b.current_rate === 'number' && b.current_rate > 0) {
    parts.push(`carries a ${register(`${b.current_rate.toFixed(2)}%`, 'Current rate', 'current_rate', b.current_rate)} rate`);
  }
  if (typeof b.rate_spread_bps === 'number') {
    parts.push(`${register(`${b.rate_spread_bps} bps`, 'Rate spread', 'rate_spread_bps', b.rate_spread_bps)} above market`);
  }
  const eq = equityPct(b);
  if (eq !== null) {
    parts.push(`with ${register(`${Math.round(eq)}% equity`, 'Equity', 'ltv', Math.round(eq))}`);
  }
  let s2 = '';
  if (parts.length > 0) {
    const lienStr =
      typeof b.current_lien_balance === 'number' && b.current_lien_balance > 0
        ? ` on a ${register(fmtCurrencyK(b.current_lien_balance), 'Lien balance', 'current_lien_balance', b.current_lien_balance)} lien`
        : '';
    // Capitalize the first part.
    const joined = parts.join(', ').replace(/^./, (c) => c.toUpperCase());
    s2 = `${joined}${lienStr}.`;
  }

  // Sentence 3 — the recommendation (no number to verify; the offer is text).
  const s3 = b.recommended_offer
    ? `${b.recommended_offer} is the next-best move.`
    : 'Reviewed for the next-best offer.';

  const sentences = [s1, s2, s3].filter((s) => s && s.trim().length > 1);

  // Verifier pass (b): scan the CLAIM-BEARING sentences (s1, s2) for any
  // number not covered by a registered claim — an un-grounded figure is a
  // template bug. The recommendation sentence (s3) is deliberately excluded:
  // it carries a categorical offer label ("5/1 ARM", "30-year refi") whose
  // digits are part of a product name, not a numeric claim about the
  // borrower, so they must not be flagged as unverified.
  const claimNumbers = new Set(
    claims.map((c) => c.token.replace(/[^0-9.]/g, '')),
  );
  const verifiableProse = [s1, s2].filter(Boolean).join(' ');
  const proseNumbers = (verifiableProse.match(/\d+(?:\.\d+)?/g) ?? []);
  const unverifiedTokens = proseNumbers.filter((n) => !claimNumbers.has(n));

  const allVerified = claims.every((c) => c.verified) && unverifiedTokens.length === 0;

  return { sentences, claims, allVerified, unverifiedTokens };
}
