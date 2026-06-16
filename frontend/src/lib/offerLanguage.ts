export interface OfferLanguage {
  label: string;
  short: string;
  borrowerBenefit: string;
}

export const OFFER_LANGUAGE: Record<string, OfferLanguage> = {
  purchase: {
    label: 'Next-home purchase loan',
    short: 'For borrowers with an active listing; prepare financing for the next home instead of refinancing the current one.',
    borrowerBenefit: 'A loan officer can help compare timing, pre-approval, and next-home financing options before closing.',
  },
  refi_plus_heloc: {
    label: 'Refinance + home-equity review',
    short: 'Rate economics and equity both clear the bar, so review the first mortgage and equity options together.',
    borrowerBenefit: 'The borrower can compare a lower-rate first mortgage and available equity options in one conversation.',
  },
  heloc: {
    label: 'Home-equity line review',
    short: 'Equity and HELOC intent support an equity-credit conversation; refinance economics are weaker.',
    borrowerBenefit: 'The borrower can explore access to home equity without replacing the first mortgage.',
  },
  refi: {
    label: 'Refinance review',
    short: 'The current rate appears above market and equity clears the refinance screen.',
    borrowerBenefit: 'The borrower can compare whether a refinance may improve the current mortgage structure.',
  },
  cash_out: {
    label: 'Cash-out refinance review',
    short: 'Available equity supports a cash-out conversation even when rate economics are less compelling.',
    borrowerBenefit: 'The borrower can compare replacing the current mortgage while accessing available equity.',
  },
  investor: {
    label: 'Investor financing review',
    short: 'Owner Link shows a multi-property profile; route to investor lending instead of a homeowner refinance path.',
    borrowerBenefit: 'The borrower can review financing options suited to a multi-property portfolio.',
  },
  retention: {
    label: 'Customer retention review',
    short: 'A current-customer or recapture signal makes this a relationship-protection conversation.',
    borrowerBenefit: 'The borrower can review whether their current loan still fits before shopping elsewhere.',
  },
  nurture: {
    label: 'Monitor for later',
    short: 'No strong trigger is active; keep the borrower in nurture instead of approving outreach.',
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

export function offerDisplayLabel(code?: string | null, fallback?: string | null): string {
  const normalized = normalizeOfferCode(code);
  const fallbackCode = LEGACY_LABEL_TO_CODE[normalizeOfferCode(fallback)];
  return OFFER_LANGUAGE[normalized]?.label ?? OFFER_LANGUAGE[fallbackCode]?.label ?? fallback ?? 'Mortgage review';
}

export function offerShortDescription(code?: string | null, fallback?: string | null): string {
  const normalized = normalizeOfferCode(code);
  const fallbackCode = LEGACY_LABEL_TO_CODE[normalizeOfferCode(fallback)];
  return OFFER_LANGUAGE[normalized]?.short ?? OFFER_LANGUAGE[fallbackCode]?.short ?? fallback ?? 'Review the strongest current borrower signal before approving outreach.';
}

export function offerBorrowerBenefit(code?: string | null, fallback?: string | null): string {
  const normalized = normalizeOfferCode(code);
  const fallbackCode = LEGACY_LABEL_TO_CODE[normalizeOfferCode(fallback)];
  return OFFER_LANGUAGE[normalized]?.borrowerBenefit ?? OFFER_LANGUAGE[fallbackCode]?.borrowerBenefit ?? fallback ?? 'A licensed loan officer can review whether the current mortgage still fits.';
}
