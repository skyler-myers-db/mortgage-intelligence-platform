type SourceTuple = readonly [layer: string, name: string, meta?: string];
type SegmentEvidenceSpec = readonly [predicate: string, sources: readonly SourceTuple[]];

export const SEGMENT_EVIDENCE_SPECS: Record<string, SegmentEvidenceSpec> = {
  itm: [
    'fn_in_the_money(spread, equity)',
    [
      ['SILVER', 'mip.silver.lien_current'],
      ['SILVER', 'mip.silver.market_rates_weekly'],
    ],
  ],
  listed: [
    'listed_for_sale',
    [['SILVER', 'mip.silver.listing_activity']],
  ],
  permit: [
    'permit OR HELOC propensity',
    [['SILVER', 'mip.silver.heloc_propensity']],
  ],
  investor: [
    '2+ properties OR corporate/absentee',
    [['SILVER', 'mip.silver.owner_property_bridge']],
  ],
  equity: [
    'equity clears threshold; no open 2nd lien',
    [['SILVER', 'mip.silver.lien_current']],
  ],
  retention: [
    'current customer with retention trigger',
    [
      ['REF', 'mip.ref.lender_dictionary'],
      ['SILVER', 'mip.silver.lien_current'],
    ],
  ],
  second_lien_itm: [
    'open 2nd lien and in-the-money',
    [
      ['SILVER', 'mip.silver.lien_current'],
      ['SILVER', 'mip.silver.market_rates_weekly'],
    ],
  ],
  heloc_draw_to_payback: [
    'equity-loan draw reset window',
    [['SILVER', 'mip.silver.mortgage_events']],
  ],
  home_equity_history: [
    'appreciation, tenure, and equity',
    [['SILVER', 'mip.silver.lien_current']],
  ],
  refi_propensity: [
    'refi heuristic >= 60',
    [
      ['UDF', 'mip.gold.fn_refi_propensity_heuristic'],
      ['SILVER', 'mip.silver.lien_current'],
    ],
  ],
  itm_on_related_property: [
    'related property is in-the-money',
    [
      ['SILVER', 'mip.silver.property_owners'],
      ['SILVER', 'mip.silver.lien_current'],
    ],
  ],
  payoff_loss_leads: [
    'recent payoff plus competitor lien',
    [
      ['SILVER', 'mip.silver.mortgage_events'],
      ['REF', 'mip.ref.lender_dictionary'],
    ],
  ],
  permit_activity: [
    'filed permit source pending',
    [['GOLD', 'mip.gold.source_readiness']],
  ],
};

export const SEGMENT_GATE_COPY: Record<string, string> = {
  not_connected: 'Source not connected.',
  not_licensed: 'Source not licensed.',
};
