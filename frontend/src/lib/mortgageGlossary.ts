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
    appContext: 'Lower LTV usually means more available equity.',
    proof: 'Proof shows current lien divided by AVM.',
  },
  heloc: {
    id: 'heloc',
    term: 'HELOC',
    aliases: ['home equity line of credit'],
    category: 'mortgage',
    short: 'Home equity line of credit.',
    appContext: 'A HELOC lane appears when estimated equity clears the branch threshold.',
    proof: 'Proof shows the equity threshold used for the HELOC branch.',
  },
  inTheMoney: {
    id: 'in-the-money',
    term: 'In-the-money',
    aliases: ['ITM'],
    category: 'scoring',
    short: 'A borrower appears to have enough economic incentive for outreach.',
    appContext: 'Module 0 defaults to rate spread >= 75 bps and equity >= 15%.',
    proof: 'Proof compares rate spread and equity to those thresholds.',
  },
  nextBestOffer: {
    id: 'next-best-offer',
    term: 'Next-best-offer',
    aliases: ['NBO'],
    category: 'scoring',
    short: 'The deterministic offer lane selected from the governed branch rules.',
    appContext: 'Refi, HELOC, cash-out, retention, and nurture branches are evaluated deterministically.',
    proof: 'Proof marks each branch passed or failed and highlights the selected branch.',
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
