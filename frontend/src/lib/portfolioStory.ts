import type { PortfolioPreview } from '../types';

/**
 * "Your book today" — a current-state portfolio summary for Home. A plain-
 * English orientation of the lender's book RIGHT NOW (scale, in-the-money
 * share, top-tier opportunities, offers ready), NOT a day-over-day delta (the
 * morning-briefing delta card was removed because the snapshot data carries no
 * honest "what changed" signal).
 *
 * Composed DETERMINISTICALLY from the PortfolioPreview already loaded on Home
 * (no new request, no live-LLM call → no booth latency/failure), and every
 * number in the prose is routed through the same verifier the borrower "Tell
 * the story" narrative uses: each token must equal its grounded source value,
 * and no number may appear that isn't a verified claim. That guarantee is what
 * lets the card badge the summary "grounded in the current gold snapshot"
 * honestly — it is not, and is not labelled as, a live AI generation.
 */

export type PortfolioClaimSourceKey = 'population' | 'itm' | 'leadScore' | 'nbo';

export interface PortfolioClaim {
  label: string;
  /** The exact token as it appears in the narrative ("111,726", "2.2%"). */
  token: string;
  /** True when the token's number equals its grounded source value. */
  verified: boolean;
  /** Key into DRAWER_SOURCES for the evidence chip (resolved by the card). */
  sourceKey: PortfolioClaimSourceKey;
}

export interface PortfolioStory {
  /** False → render nothing (day-zero / no population / no preview). */
  available: boolean;
  sentences: string[];
  claims: PortfolioClaim[];
  /** True iff every claim verified AND no un-grounded number appears. */
  allVerified: boolean;
  /** Numbers in the prose mapping to no verified claim (should be []). */
  unverifiedTokens: string[];
  /** ISO timestamp the gold snapshot was refreshed (honest "as of" label). */
  asOf: string | null;
}

const EMPTY: PortfolioStory = {
  available: false,
  sentences: [],
  claims: [],
  allVerified: false,
  unverifiedTokens: [],
  asOf: null,
};

function intToken(n: number): string {
  return Math.round(n).toLocaleString();
}

/**
 * Compose the summary and verify it. The prose is built from explicit tokens;
 * each token is registered as a claim tied to a preview value, then the
 * verifier (a) re-checks the token matches its source and (b) scans the
 * finished prose for ANY number not covered by a claim (a template-bug
 * tripwire). Locale-grouped integers are comma-stripped before the scan so
 * "5,156,184" reads as one number, not three.
 */
export function buildPortfolioStory(preview: PortfolioPreview | null | undefined): PortfolioStory {
  if (!preview || preview.day_zero === true) return EMPTY;
  const marketable = preview.marketable_population;
  const highIntent = preview.high_intent_leads;
  if (!Number.isFinite(marketable) || marketable <= 0) return EMPTY;

  const claims: PortfolioClaim[] = [];
  const register = (
    token: string,
    label: string,
    sourceKey: PortfolioClaimSourceKey,
    sourceNum: number,
  ): string => {
    const tokenNum = Number.parseFloat(token.replace(/[^0-9.]/g, ''));
    // Counts are exact (tolerance absorbs locale rounding only); the derived
    // percent absorbs its one-decimal rounding.
    const verified = Number.isFinite(tokenNum) && Math.abs(tokenNum - sourceNum) <= 0.5;
    claims.push({ label, token, verified, sourceKey });
    return token;
  };

  // Derived in-the-money share of the book — an insight the bare KPI cards
  // don't show. Grounded in marketable + high-intent, verified against the
  // computed ratio.
  const pct = (highIntent / marketable) * 100;
  const pctToken = `${pct.toFixed(pct < 10 ? 1 : 0)}%`;

  const s1 = `Your marketable book stands at ${register(
    intToken(marketable),
    'Marketable population',
    'population',
    marketable,
  )} borrowers.`;

  let s2 = `${register(intToken(highIntent), 'In-the-money leads', 'itm', highIntent)} are in the money — ${register(
    pctToken,
    'In-the-money share',
    'itm',
    pct,
  )} of the book`;
  if (preview.top_tier_opportunities != null && preview.top_tier_opportunities > 0) {
    s2 += `, and ${register(
      intToken(preview.top_tier_opportunities),
      'Top-tier opportunities',
      'leadScore',
      preview.top_tier_opportunities,
    )} clear the top-tier opportunity bar`;
  }
  s2 += '.';

  const s3 =
    preview.offers_recommended != null && preview.offers_recommended > 0
      ? `${register(
          intToken(preview.offers_recommended),
          'Offers recommended',
          'nbo',
          preview.offers_recommended,
        )} already carry a recommended next-best offer.`
      : '';

  const sentences = [s1, s2, s3].filter((s) => s && s.trim().length > 1);

  // Stray-number scan (count-based; commas stripped so grouped integers read
  // as one token). Any prose number beyond what's claimed is a template bug.
  const claimCounts = new Map<string, number>();
  for (const c of claims) {
    const n = c.token.replace(/[^0-9.]/g, '');
    claimCounts.set(n, (claimCounts.get(n) ?? 0) + 1);
  }
  const prose = sentences.join(' ').replace(/,/g, '');
  const proseNumbers = prose.match(/\d+(?:\.\d+)?/g) ?? [];
  const usedCounts = new Map<string, number>();
  const unverifiedTokens: string[] = [];
  for (const n of proseNumbers) {
    const used = usedCounts.get(n) ?? 0;
    if (used >= (claimCounts.get(n) ?? 0)) unverifiedTokens.push(n);
    usedCounts.set(n, used + 1);
  }

  const allVerified = claims.every((c) => c.verified) && unverifiedTokens.length === 0;

  return {
    available: true,
    sentences,
    claims,
    allVerified,
    unverifiedTokens,
    asOf: preview.data_refreshed_at ?? null,
  };
}
