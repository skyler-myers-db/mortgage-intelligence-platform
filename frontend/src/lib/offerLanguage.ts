export interface OfferLanguage {
  label: string;
  short: string;
  rationale: string;
  borrowerBenefit: string;
}

export const OFFER_LANGUAGE: Record<string, OfferLanguage> = {
  purchase: {
    label: 'Next-home purchase loan',
    short: 'For borrowers with an active listing; prepare financing for the next home instead of refinancing the current one.',
    rationale: 'The property is listed for sale, so the useful conversation is likely about financing the next home before closing.',
    borrowerBenefit: 'A loan officer can help compare timing, pre-approval, and next-home financing options before closing.',
  },
  refi_plus_heloc: {
    label: 'Refinance + home-equity review',
    short: 'Rate economics and equity both clear the bar, so review the first mortgage and equity options together.',
    rationale: "The current mortgage appears meaningfully above today's market reference rate, and the property has enough equity to review refinance and home-equity options together.",
    borrowerBenefit: 'The borrower can compare a lower-rate first mortgage and available equity options in one conversation.',
  },
  heloc: {
    label: 'Home-equity line review',
    short: 'Equity and HELOC intent support an equity-credit conversation; refinance economics are weaker.',
    rationale: 'Home-equity signals suggest a conversation about available equity may be useful without replacing the first mortgage.',
    borrowerBenefit: 'The borrower can explore access to home equity without replacing the first mortgage.',
  },
  refi: {
    label: 'Refinance review',
    short: 'The current rate appears above market and equity clears the refinance screen.',
    rationale: "The current mortgage appears above today's market reference rate, and the property has enough equity to review refinance options.",
    borrowerBenefit: 'The borrower can compare whether a refinance may improve the current mortgage structure.',
  },
  cash_out: {
    label: 'Cash-out refinance review',
    short: 'Available equity supports a cash-out conversation even when rate economics are less compelling.',
    rationale: 'The borrower appears to have available equity, so a licensed loan officer can review whether a cash-out refinance would fit their goals.',
    borrowerBenefit: 'The borrower can compare replacing the current mortgage while accessing available equity.',
  },
  investor: {
    label: 'Investor financing review',
    short: 'Owner Link shows a multi-property profile; route to investor lending instead of a homeowner refinance path.',
    rationale: 'Owner Link connects this borrower to related properties, so route the review to an investor-lending specialist.',
    borrowerBenefit: 'The borrower can review financing options suited to a multi-property portfolio.',
  },
  retention: {
    label: 'Customer retention review',
    short: 'A current-customer or recapture signal makes this a relationship-protection conversation.',
    rationale: 'This current-customer relationship has signals worth reviewing, so prioritize a service-focused check-in before the borrower shops alternatives.',
    borrowerBenefit: 'The borrower can review whether their current loan still fits before shopping elsewhere.',
  },
  nurture: {
    label: 'Monitor for later',
    short: 'No strong trigger is active; keep the borrower in nurture instead of approving outreach.',
    rationale: 'No strong borrower benefit is active yet, so keep this borrower in nurture until a clearer signal appears.',
    borrowerBenefit: 'No borrower outreach is recommended until a clearer benefit appears.',
  },
};

const LEGACY_LABEL_TO_CODE: Record<string, string> = {
  'purchase mortgage': 'purchase',
  'refinance + heloc': 'refi_plus_heloc',
  heloc: 'heloc',
  refinance: 'refi',
  'cash-out refi': 'cash_out',
  'cash-out refinance': 'cash_out',
  'investor product': 'investor',
  retention: 'retention',
  nurture: 'nurture',
};

function normalizeOfferCode(code?: string | null): string {
  return (code ?? '').trim().toLowerCase();
}

function resolveOfferLanguage(code?: string | null, fallback?: string | null): OfferLanguage | undefined {
  const normalized = normalizeOfferCode(code);
  const fallbackCode = LEGACY_LABEL_TO_CODE[normalizeOfferCode(fallback)];
  return OFFER_LANGUAGE[normalized] ?? OFFER_LANGUAGE[fallbackCode];
}

const INTERNAL_RATIONALE_PATTERNS = [
  /public-record signals/i,
  /right offer/i,
  /cross-sell/i,
  /lead with a refinance/i,
  /refi cushion/i,
  /heloc bar/i,
  /retention threshold/i,
  /algorithm/i,
  /deterministic/i,
];

function safeFallbackRationale(fallback?: string | null): string | undefined {
  const value = (fallback ?? '').trim();
  if (!value) return undefined;
  return INTERNAL_RATIONALE_PATTERNS.some((pattern) => pattern.test(value)) ? undefined : value;
}

export function offerDisplayLabel(code?: string | null, fallback?: string | null): string {
  return resolveOfferLanguage(code, fallback)?.label ?? fallback ?? 'Mortgage review';
}

export function offerShortDescription(code?: string | null, fallback?: string | null): string {
  return resolveOfferLanguage(code, fallback)?.short ?? fallback ?? 'Review the strongest current borrower signal before approving outreach.';
}

export function offerRationale(code?: string | null, fallback?: string | null): string {
  return resolveOfferLanguage(code, fallback)?.rationale
    ?? safeFallbackRationale(fallback)
    ?? 'A licensed loan officer can review the strongest current borrower signal before any outreach is approved.';
}

export function offerBorrowerBenefit(code?: string | null, fallback?: string | null): string {
  return resolveOfferLanguage(code, fallback)?.borrowerBenefit ?? fallback ?? 'A licensed loan officer can review whether the current mortgage still fits.';
}
