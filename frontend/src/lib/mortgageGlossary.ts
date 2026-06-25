import type { ReactNode } from 'react';

export type GlossaryCategory =
  | 'property'
  | 'mortgage'
  | 'scoring'
  | 'evidence'
  | 'governance';

export interface GlossaryEntry {
  id: string;
  term: string;
  aliases: string[];
  category: GlossaryCategory;
  short: string;
  appContext: string;
  proof?: string;
}

export const mortgageGlossary = {
  avm: {
    id: 'avm',
    term: 'AVM',
    aliases: ['automated valuation model'],
    category: 'property',
    short: "Cotality's automated valuation estimate for the property.",
    appContext: 'Used to calculate equity and LTV when a current lien is present.',
    proof: 'Proof shows AVM, lien balance, equity dollars, and equity percent.',
  },
  bps: {
    id: 'bps',
    term: 'bps',
    aliases: ['basis points', 'rate spread'],
    category: 'mortgage',
    short: 'Basis points; 100 bps equals 1 percentage point.',
    appContext: 'Rate spread is shown in bps so small rate differences stay readable.',
    proof: 'Proof shows current rate minus market rate.',
  },
  clip: {
    id: 'clip',
    term: 'CLIP',
    aliases: ['property ref'],
    category: 'property',
    short: 'A Cotality mastered property identifier.',
    appContext: 'Only masked clip_ref values are shown; raw CLIP stays inside governed joins.',
    proof: 'Lineage lists the governed tables without exposing raw identifiers.',
  },
  ltv: {
    id: 'ltv',
    term: 'LTV',
    aliases: ['loan-to-value'],
    category: 'mortgage',
    short: 'Loan-to-value: current lien balance divided by AVM.',
    appContext: 'Lower LTV usually means more available equity; underwater borrowers can show LTV above 100%.',
    proof: 'Proof shows current lien divided by AVM. Equity scoring clamps underwater values to 0.',
  },
  heloc: {
    id: 'heloc',
    term: 'HELOC',
    aliases: ['home equity line of credit'],
    category: 'mortgage',
    short: 'Home equity line of credit.',
    appContext: 'HELOC Intent appears when the governed Cotality HELOC propensity trigger and equity branch support an equity-credit conversation.',
    proof: 'Proof shows HELOC propensity and branch thresholds; filed building permits remain a separate pending source.',
  },
  helocIntent: {
    id: 'heloc-intent',
    term: 'HELOC Intent',
    aliases: ['equity-credit intent', 'home equity propensity'],
    category: 'evidence',
    short: 'A Cotality HELOC propensity signal for likely equity-credit demand.',
    appContext: 'This is not a filed building permit; it combines propensity and equity context.',
    proof: 'Proof shows propensity, run date, and the selected HELOC branch.',
  },
  mlsListings: {
    id: 'mls-listings',
    term: 'MLS/Listings',
    aliases: ['listing feed'],
    category: 'evidence',
    short: 'Cotality listing data.',
    appContext: 'Used for next-home purchase conversations and separate from permits or HELOC propensity.',
    proof: 'Proof shows listing status, date, source table, and refresh timestamp.',
  },
  listedForSale: {
    id: 'listed-for-sale',
    term: 'Listed for Sale',
    aliases: ['for sale', 'active listing'],
    category: 'evidence',
    short: 'A borrower/property has a live Cotality MLS signal.',
    appContext: 'This is a purchase-intent trigger, not a loan application.',
    proof: 'Proof shows listing status, date, and backing evidence.',
  },
  buildingPermits: {
    id: 'building-permits',
    term: 'Building permits',
    aliases: ['filed permits', 'permit activity'],
    category: 'evidence',
    short: 'Filed permit records for property work or renovation.',
    appContext: 'Filed permit data is separate from HELOC Intent. Do not infer filed permits from HELOC propensity.',
    proof: 'Source readiness shows permit feed status.',
  },
  inTheMoney: {
    id: 'in-the-money',
    term: 'In-the-money',
    aliases: ['ITM', 'refi economics'],
    category: 'scoring',
    short: 'A refinance-only economics screen: rate spread and equity both clear the configured thresholds.',
    appContext: 'It is not the same as a high-quality lead. A borrower can be in-the-money but still rank lower after intent, relationship, fit, contactability, and evidence are applied.',
    proof: 'Proof compares rate spread and equity to the configured thresholds.',
  },
  nextBestOffer: {
    id: 'next-best-offer',
    term: 'Primary offer',
    aliases: ['recommended offer', 'selected offer'],
    category: 'scoring',
    short: 'The offer path the rules selected as the most useful next conversation for this borrower.',
    appContext: 'The audit records the selected rule branch, while screens show the plain-language offer path.',
    proof: 'Proof marks each branch passed or failed and highlights the selected offer path.',
  },
  opportunityScore: {
    id: 'opportunity-score',
    term: 'Opportunity score',
    aliases: ['lead score', 'score 75+'],
    category: 'scoring',
    short: 'A 0-100 ranking score for how strong the borrower is for review.',
    appContext: 'A score of 75 or higher marks the strongest review candidates. It is broader than refinance economics because it also considers intent, fit, relationship, and evidence.',
    proof: 'Proof shows the five weighted sub-scores that make up the final score.',
  },
  ownerLink: {
    id: 'owner-link',
    term: 'Owner Link',
    aliases: ['owner graph'],
    category: 'property',
    short: 'Cotality owner/entity graph connecting related properties.',
    appContext: 'Used for multi-property, investor, absentee, and relationship signals.',
    proof: 'Evidence rows show public source products without raw owner names.',
  },
  signalStrength: {
    id: 'signal-strength',
    term: 'Signal strength',
    aliases: ['confidence score', 'confidence'],
    category: 'scoring',
    short: 'A deterministic average of the five scoring sub-scores.',
    appContext: 'Not a statistical confidence interval or probability of approval.',
    proof: 'Proof shows the five sub-scores and the average.',
  },
  evidenceConfidence: {
    id: 'evidence-confidence',
    term: 'Evidence confidence',
    aliases: ['source confidence'],
    category: 'evidence',
    short: 'A row-level confidence value from the governed evidence event.',
    appContext: 'Separate from signal strength. AVM rows inherit source confidence; other rows use governed constants.',
    proof: 'Signals and Borrower proof list source, signal type, confidence, and timestamp.',
  },
  rateSpread: {
    id: 'rate-spread',
    term: 'Rate spread',
    aliases: ['rate delta'],
    category: 'mortgage',
    short: "The borrower's current note rate minus the current market reference rate.",
    appContext: 'Positive spread means the borrower appears above market.',
    proof: 'Proof shows current rate, market rate, and bps.',
  },
  supportingEvidence: {
    id: 'supporting-evidence',
    term: 'Supporting evidence',
    aliases: ['evidence chips'],
    category: 'evidence',
    short: 'Display-safe source rows backing the score and offer recommendation.',
    appContext: 'Evidence chips open lineage; proof opens arithmetic and governed SQL.',
    proof: 'Rows are redacted at the API boundary.',
  },
  unityCatalog: {
    id: 'unity-catalog',
    term: 'Unity Catalog',
    aliases: ['UC'],
    category: 'governance',
    short: "Databricks' governed data catalog and access-control layer.",
    appContext: 'The app reads curated gold and semantic views through UC.',
    proof: 'Proof lists UC assets and copyable SQL for authenticated users.',
  },
} satisfies Record<string, GlossaryEntry>;

export type GlossaryTermKey = keyof typeof mortgageGlossary;

export const glossaryEntries = Object.values(mortgageGlossary).sort((a, b) =>
  a.term.localeCompare(b.term),
);

export function glossaryEntry(key: GlossaryTermKey): GlossaryEntry {
  return mortgageGlossary[key];
}

export function glossaryAnchor(key: GlossaryTermKey): string {
  return `/glossary#${mortgageGlossary[key].id}`;
}

export function glossaryText(key: GlossaryTermKey): ReactNode {
  return mortgageGlossary[key].term;
}
