type SourceTuple = readonly [layer: string, name: string, meta?: string];
type SegmentEvidenceSpec = readonly [predicate: string, sources: readonly SourceTuple[]];

export const SEGMENT_EVIDENCE_SPECS: Record<string, SegmentEvidenceSpec> = {
  itm: [
    'fn_in_the_money(rate_spread_bps, equity_pct)',
    [
      ['SILVER', 'mip.silver.lien_current', 'rates, AVM, equity'],
      ['SILVER', 'mip.silver.market_rates_weekly', 'par-rate reference'],
    ],
  ],
  listed: [
    'listed_for_sale = TRUE (current active/under-contract MLS row)',
    [['SILVER', 'mip.silver.listing_activity', 'MLS rows joined to CLIP']],
  ],
  permit: [
    'has_permit OR has_heloc_propensity_trigger',
    [['SILVER', 'mip.silver.heloc_propensity', 'HELOC propensity score']],
  ],
  investor: [
    'related_property_count >= 2 OR is_corporate_owner OR is_absentee',
    [['SILVER', 'mip.silver.owner_property_bridge', 'Owner Link rollup']],
  ],
  equity: [
    'equity_pct >= heloc threshold AND COALESCE(second_pos_amount, 0) = 0',
    [['SILVER', 'mip.silver.lien_current', 'AVM + open-lien equity']],
  ],
  retention: [
    'is_current_customer AND (spread >= retention threshold OR competitor lien OR listed)',
    [
      ['REF', 'mip.ref.lender_dictionary', 'tenant vs competitor mapping'],
      ['SILVER', 'mip.silver.lien_current', 'servicer + spread'],
    ],
  ],
  second_lien_itm: [
    'second_pos_amount > 0 AND fn_in_the_money(second_pos_rate_spread_bps, equity_pct)',
    [
      ['SILVER', 'mip.silver.lien_current', 'second lien rate/balance'],
      ['SILVER', 'mip.silver.market_rates_weekly', 'par-rate reference'],
    ],
  ],
  heloc_draw_to_payback: [
    'open equity-loan lien originated 102-126 months ago',
    [['SILVER', 'mip.silver.mortgage_events', 'equity-loan timeline']],
  ],
  home_equity_history: [
    'appreciation >= 40% since purchase AND tenure >= 36 months AND equity_pct >= 20',
    [['SILVER', 'mip.silver.lien_current', 'purchase basis + AVM']],
  ],
  refi_propensity: [
    'fn_refi_propensity_heuristic(...) >= 60',
    [
      ['UDF', 'mip.gold.fn_refi_propensity_heuristic', 'published heuristic'],
      ['SILVER', 'mip.silver.lien_current', 'spread, seasoning, equity, balance'],
    ],
  ],
  itm_on_related_property: [
    'Owner Link also holds a different in-the-money CLIP',
    [
      ['SILVER', 'mip.silver.property_owners', 'Owner Link ids'],
      ['SILVER', 'mip.silver.lien_current', 'related-property economics'],
    ],
  ],
  payoff_loss_leads: [
    'tenant lien released within 24 months AND current competitor lien',
    [
      ['SILVER', 'mip.silver.mortgage_events', 'release/payoff events'],
      ['REF', 'mip.ref.lender_dictionary', 'tenant vs competitor mapping'],
    ],
  ],
  permit_activity: [
    'has_permit = TRUE (filed-permit source pending)',
    [['GOLD', 'mip.gold.source_readiness', 'permit source status']],
  ],
};

export const SEGMENT_GATE_COPY: Record<string, string> = {
  not_connected: 'Source not connected; count is gated.',
  not_licensed: 'Source not licensed; count is gated.',
};
