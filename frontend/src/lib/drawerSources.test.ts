import { describe, expect, it } from 'vitest';
import {
  assetHrefForSource,
  assetKeyForSource,
  descriptorFor,
  descriptorForEvidence,
  drawerForAsset,
  ASSET_KEYS_BY_SOURCE,
  DRAWER_SOURCES,
  evidenceDestinationFor,
} from './drawerSources';
import lineageManifest from '../../../backend/resources/lineage_manifest.json';

describe('home headline KPI sources cite the metric view (S1)', () => {
  it('routes the headline view lineage to its own drawer entry', () => {
    expect(descriptorFor('mip.semantics.portfolio_headline_metric_view')).toBe(
      DRAWER_SOURCES.portfolioHeadlineView,
    );
    expect(assetKeyForSource('mip.semantics.portfolio_headline_metric_view')).toBe(
      'portfolio_headline_metric_view',
    );
  });

  it('cites the headline metric view + underlying rows on every home KPI drawer', () => {
    const homeKpiSources = [
      DRAWER_SOURCES.population,
      DRAWER_SOURCES.itm,
      DRAWER_SOURCES.leadScore,
      DRAWER_SOURCES.nbo,
    ];
    for (const source of homeKpiSources) {
      const lineageNames = (source.lineage ?? []).map((step) => step.name);
      expect(lineageNames).toContain('mip.semantics.portfolio_headline_metric_view');
      // Underlying row asset stays anchored so the drawer's freshness +
      // lineage link resolve to a real governed table.
      expect(source.assetKey).toBeTruthy();
      expect(
        (source.signals ?? []).some((signal) =>
          signal.source.includes('portfolio_headline_metric_view'),
        ),
      ).toBe(true);
    }
  });

  it('keeps the offers-available measure visible on the offer drawer', () => {
    expect(
      (DRAWER_SOURCES.nbo.signals ?? []).some(
        (signal) => signal.source === 'portfolio_headline_metric_view.offer_available',
      ),
    ).toBe(true);
  });
});

describe('descriptorFor', () => {
  it('routes lead score lineage to the lead score drawer', () => {
    expect(descriptorFor('mip.gold.fn_lead_score')).toBe(DRAWER_SOURCES.leadScore);
    expect(descriptorFor('mip.gold.lead_scores')).toBe(DRAWER_SOURCES.leadScore);
  });

  it('keeps next-best-offer lineage separate from lead score lineage', () => {
    expect(descriptorFor('mip.gold.fn_next_best_offer')).toBe(DRAWER_SOURCES.nbo);
    expect(DRAWER_SOURCES.nbo.title).toBe('Primary offer rules');
  });

  it('keeps property graph, AVM, and lien evidence separate from marketable population', () => {
    expect(descriptorFor('mip.gold.property_owner_bridge')).toBe(DRAWER_SOURCES.ownerGraph);
    expect(descriptorFor('mip.silver.property_master')).toBe(DRAWER_SOURCES.propertyProfile);
    expect(descriptorFor('cotality.avm.current')).toBe(DRAWER_SOURCES.avm);
    expect(descriptorFor('mip.silver.lien_current')).toBe(DRAWER_SOURCES.lien);
    expect(descriptorFor('mip.gold.borrower_360')).toBe(DRAWER_SOURCES.borrower360);
    expect(descriptorFor('mip.gold.lead_population')).toBe(DRAWER_SOURCES.leadPopulation);
    expect(descriptorFor('mip.gold.segment_population')).toBe(DRAWER_SOURCES.segmentPopulation);
    expect(descriptorFor('mip.gold.evidence_events')).toBe(DRAWER_SOURCES.evidenceStream);
    expect(descriptorFor('mip.silver.mortgage_events')).toBe(DRAWER_SOURCES.mortgageDomain);
    expect(descriptorFor('mip.silver.owner_transfer_events')).toBe(DRAWER_SOURCES.ownerTransfer);
  });

  it('uses evidence product and signal before shared source table routing', () => {
    expect(
      descriptorForEvidence({
        source_product: 'AVM',
        source_table: 'mip.silver.lien_current',
        signal_type: 'equity',
      }),
    ).toBe(DRAWER_SOURCES.avm);

    expect(
      descriptorForEvidence({
        source_product: 'Voluntary Lien',
        source_table: 'mip.silver.lien_current',
        signal_type: 'rate_spread',
      }),
    ).toBe(DRAWER_SOURCES.rateSpread);

    expect(
      descriptorForEvidence({
        source_product: 'Owner Link',
        source_table: 'mip.gold.property_owner_bridge',
        signal_type: 'multi_property',
      }),
    ).toBe(DRAWER_SOURCES.ownerGraph);

    expect(
      descriptorForEvidence({
        source_product: 'Property',
        source_table: 'mip.silver.property_master',
        signal_type: 'foreclosure_stage',
      }),
    ).toBe(DRAWER_SOURCES.propertyProfile);

    expect(
      descriptorForEvidence({
        source_product: 'Mortgage Domain',
        source_table: 'mip.silver.mortgage_events',
        signal_type: 'recent_refi',
      }),
    ).toBe(DRAWER_SOURCES.mortgageDomain);

    expect(
      descriptorForEvidence({
        source_product: 'Owner Transfer',
        source_table: 'mip.silver.owner_transfer_events',
        signal_type: 'recent_sale',
      }),
    ).toBe(DRAWER_SOURCES.ownerTransfer);

    expect(
      descriptorForEvidence({
        source_product: 'MLS',
        source_table: 'mip.silver.listing_activity',
        signal_type: 'listing',
      }),
    ).toBe(DRAWER_SOURCES.mls);

    expect(
      descriptorForEvidence({
        source_product: 'Cotality HELOC Propensity',
        source_table: 'mip.silver.heloc_propensity',
        signal_type: 'heloc_propensity',
      }),
    ).toBe(DRAWER_SOURCES.helocPropensity);

    expect(
      descriptorForEvidence({
        source_product: 'Cotality Refi Propensity',
        source_table: 'mip.silver.refi_propensity',
        signal_type: 'refi_propensity',
      }),
    ).toBe(DRAWER_SOURCES.refiPropensity);
  });

  it('routes S1.6 product-type and origination-channel evidence to their curated drawers', () => {
    // product_type shares the Voluntary Lien source product with rate/lien
    // signals; the specific signal_type must win over the generic lien route.
    expect(
      descriptorForEvidence({
        source_product: 'Voluntary Lien',
        source_table: 'mip.silver.lien_current',
        signal_type: 'product_type',
      }),
    ).toBe(DRAWER_SOURCES.loanProductType);
    expect(
      descriptorForEvidence({
        source_product: 'First-Party LOS',
        source_table: 'mip.first_party.loan_applications',
        signal_type: 'origination_channel',
      }),
    ).toBe(DRAWER_SOURCES.originationChannel);
  });

  it('keeps S1.6 dimension drawers resolvable to a registered asset key', () => {
    expect(assetKeyForSource(DRAWER_SOURCES.loanProductType.assetPath)).toBe('borrower_360');
    expect(DRAWER_SOURCES.loanProductType.assetKey).toBe('borrower_360');
    expect(DRAWER_SOURCES.originationChannel.assetKey).toBe('loan_applications');
    expect(assetKeyForSource(DRAWER_SOURCES.originationChannel.assetPath)).toBe(
      'loan_applications',
    );
  });

  it('maps Genie trusted assets to specific curated drawers', () => {
    expect(drawerForAsset('mip.gold.segment_population')).toBe(DRAWER_SOURCES.segmentPopulation);
    expect(drawerForAsset('mip.gold.lead_population')).toBe(DRAWER_SOURCES.leadPopulation);
    expect(drawerForAsset('mip.gold.borrower_dossier')).toBe(DRAWER_SOURCES.borrowerDossier);
    expect(assetKeyForSource('mip.gold.borrower_dossier')).toBe('borrower_dossier');
    expect(assetHrefForSource('mip.gold.borrower_dossier')).toBe('/data-estate/assets/borrower_dossier');
    expect(drawerForAsset('mip.gold.evidence_events')).toBe(DRAWER_SOURCES.evidenceStream);
    expect(drawerForAsset('mip.gold.household_rollup')).toBe(DRAWER_SOURCES.householdRollup);
    expect(drawerForAsset('mip.gold.source_readiness')).toBe(DRAWER_SOURCES.sourceReadiness);
    expect(drawerForAsset('mip.gold.lockin_cohort')).toBe(DRAWER_SOURCES.lockinCohort);
    expect(drawerForAsset('mip.gold.funnel_snapshot_daily')).toBe(DRAWER_SOURCES.funnelSnapshot);
    expect(drawerForAsset('mip.gold.borrower_lifecycle_state')).toBe(DRAWER_SOURCES.funnelSnapshot);
    expect(drawerForAsset('mip.gold.county_rollup')).toBe(DRAWER_SOURCES.countyRollup);
    expect(drawerForAsset('mip.gold.zip_rollup')).toBe(DRAWER_SOURCES.zipRollup);
    expect(drawerForAsset('mip.gold.fn_rate_spread')).toBe(DRAWER_SOURCES.marketRate);
    expect(drawerForAsset('mip.gold.fn_estimated_upb_confidence_band')).toBe(DRAWER_SOURCES.lien);
    expect(drawerForAsset('mip.gold.evidence_events.rate_spread')).toBe(DRAWER_SOURCES.rateSpread);
    expect(drawerForAsset('mip.semantics.lead_generation_metric_view')).toBe(DRAWER_SOURCES.leadGenerationView);
    expect(drawerForAsset('mip.semantics.segment_performance_metric_view')).toBe(DRAWER_SOURCES.segmentPerformanceView);
    expect(drawerForAsset('mip.semantics.borrower_opportunity_metric_view')).toBe(DRAWER_SOURCES.borrowerOpportunityView);
    expect(drawerForAsset('mip.silver.listing_activity')).toBe(DRAWER_SOURCES.mls);
    expect(drawerForAsset('mip.silver.heloc_propensity')).toBe(DRAWER_SOURCES.helocPropensity);
    expect(drawerForAsset('mip.silver.refi_propensity')).toBe(DRAWER_SOURCES.refiPropensity);
    expect(drawerForAsset('mip_app.action_audit')).toBeNull();
  });

  it('maps only trusted app proof assets to in-app asset detail routes', () => {
    expect(assetKeyForSource('mip.gold.lead_population')).toBe('lead_population');
    expect(assetHrefForSource('mip.semantics.borrower_opportunity_metric_view')).toBe('/data-estate/assets/borrower_opportunity_metric_view');
    expect(assetKeyForSource('mip.silver.listing_activity')).toBe('listing_activity');
    expect(assetHrefForSource('mip.silver.heloc_propensity')).toBe('/data-estate/assets/heloc_propensity');
    expect(assetKeyForSource('mip.silver.lien_current')).toBe('lien_current');
    expect(assetHrefForSource('mip.first_party.loan_applications')).toBe(
      '/data-estate/assets/loan_applications',
    );
    expect(assetKeyForSource('other.gold.lead_population')).toBeNull();
  });

  it('routes campaign-performance Lakebase assets to non-admin Sales Ops analytics', () => {
    const cases = [
      ['mip_app.call_dispositions', DRAWER_SOURCES.callDispositions],
      ['mip_app.lead_outcomes', DRAWER_SOURCES.leadOutcomes],
    ] as const;

    for (const [assetPath, expectedSource] of cases) {
      const source = drawerForAsset(assetPath);
      expect(source).toBe(expectedSource);
      expect(source?.assetPath).toBe(assetPath);
      expect(assetHrefForSource(assetPath)).toBeNull();

      const destination = evidenceDestinationFor(source);
      expect(destination.kind).toBe('lakebase');
      if (destination.kind === 'lakebase') {
        expect(destination.objectPaths).toEqual([assetPath]);
        expect(destination.href).toBe('/analytics?view=sales-ops');
        expect(destination.href).not.toContain('/admin-config');
        expect(destination.actionLabel).toBe('Open sales operations');
      }
    }
  });

  it('classifies every curated drawer and maps every visible UC object to the registry', () => {
    for (const [key, source] of Object.entries(DRAWER_SOURCES)) {
      const destination = evidenceDestinationFor(source);
      expect(destination.kind, `${key} destination`).not.toBe('unmapped');

      if (destination.kind === 'unity_catalog') {
        expect(source.assetKey, `${key} assetKey`).toBeTruthy();
        expect(source.assetPath, `${key} assetPath`).toBeTruthy();
        expect(source.lineageFamily, `${key} lineageFamily`).toBeTruthy();
        expect(assetKeyForSource(source.assetPath), `${key} registry mapping`).toBe(
          source.assetKey,
        );
      }

      for (const node of source.lineage ?? []) {
        const isUcObject =
          (node.name.startsWith('mip.') ||
            node.name.startsWith('cotality_mortgage_data.')) &&
          node.name.split('.').length === 3;
        if (isUcObject) {
          expect(assetKeyForSource(node.name), `${key}: ${node.name}`).toBeTruthy();
        }
      }
    }

    for (const sourcePath of Object.keys(ASSET_KEYS_BY_SOURCE)) {
      expect(drawerForAsset(sourcePath), `${sourcePath} drawer route`).not.toBeNull();
    }

    const permit = evidenceDestinationFor(DRAWER_SOURCES.permit);
    expect(permit.kind).toBe('readiness');
    if (permit.kind === 'readiness') {
      expect(permit.objectPaths).toEqual(['mip.gold.source_readiness']);
      expect(permit.objectPaths.some((path) => path.includes('permit'))).toBe(false);
    }

    for (const source of [
      DRAWER_SOURCES.assignmentOverlay,
      DRAWER_SOURCES.callDispositions,
      DRAWER_SOURCES.leadOutcomes,
      DRAWER_SOURCES.config,
    ]) {
      const destination = evidenceDestinationFor(source);
      expect(destination.kind).toBe('lakebase');
      if (destination.kind === 'lakebase') {
        expect(destination.objectPaths.every((path) => path.startsWith('mip_app.'))).toBe(true);
      }
    }
  });

  it('maps every declared drawer lineage family to the governed manifest', () => {
    const manifestFamilyIds = new Set(lineageManifest.families.map((family) => family.id));

    for (const [key, source] of Object.entries(DRAWER_SOURCES)) {
      if (source.lineageFamily) {
        expect(manifestFamilyIds.has(source.lineageFamily), `${key} lineageFamily`).toBe(true);
      }
    }
  });

  it('resolves destinations by stable asset key when display copy is overridden', () => {
    const source = {
      ...DRAWER_SOURCES.leadPopulation,
      title: 'Tenant-specific ranked population',
      short: 'Customized copy',
    };
    expect(evidenceDestinationFor(source).kind).toBe('unity_catalog');
  });

  it('documents estimated UPB confidence-band lineage and inputs', () => {
    const lineageNames = DRAWER_SOURCES.lien.lineage?.map((node) => node.name) ?? [];
    const signalSources = DRAWER_SOURCES.lien.signals?.map((signal) => signal.source) ?? [];

    expect(lineageNames).toContain('mip.gold.fn_bounded_mortgage_rate');
    expect(lineageNames).toContain('mip.gold.fn_estimated_upb_confidence_band');
    expect(signalSources).toContain('lien_current.first_pos_amount');
    expect(signalSources).toContain('months_between(refresh_at, first_pos_date)');
    expect(signalSources).toContain('fn_estimated_upb_confidence_band');
  });

  it('carries event timestamps as event dates, not source freshness', () => {
    const source = descriptorForEvidence({
      source_product: 'AVM',
      source_table: 'mip.silver.lien_current',
      signal_type: 'equity',
      timestamp: '2026-05-06T00:32:19Z',
    });

    expect(source.title).toBe(DRAWER_SOURCES.avm.title);
    expect(source.eventDate).toBe('2026-05-06T00:32:19Z');
    expect(source.updatedAt).toBeUndefined();
    expect(DRAWER_SOURCES.avm.updatedAt).toBeUndefined();
  });

  it('keeps row-preview evidence drawer titles unique', () => {
    const rowPreviewSources = [
      DRAWER_SOURCES.population,
      DRAWER_SOURCES.leadPopulation,
      DRAWER_SOURCES.segmentPopulation,
      DRAWER_SOURCES.ownerGraph,
      DRAWER_SOURCES.propertyProfile,
      DRAWER_SOURCES.avm,
      DRAWER_SOURCES.lien,
      DRAWER_SOURCES.rateSpread,
      DRAWER_SOURCES.mortgageDomain,
      DRAWER_SOURCES.ownerTransfer,
      DRAWER_SOURCES.evidenceStream,
      DRAWER_SOURCES.sourceReadiness,
      DRAWER_SOURCES.borrowerDossier,
      DRAWER_SOURCES.householdRollup,
      DRAWER_SOURCES.itm,
      DRAWER_SOURCES.marketRate,
      DRAWER_SOURCES.leadScore,
      DRAWER_SOURCES.nbo,
      DRAWER_SOURCES.mls,
      DRAWER_SOURCES.helocPropensity,
      DRAWER_SOURCES.refiPropensity,
      DRAWER_SOURCES.leadGenerationView,
      DRAWER_SOURCES.segmentPerformanceView,
      DRAWER_SOURCES.borrowerOpportunityView,
    ];
    expect(new Set(rowPreviewSources.map((s) => s.title)).size).toBe(rowPreviewSources.length);
  });
});
