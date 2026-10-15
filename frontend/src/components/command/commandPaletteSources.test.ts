import { describe, expect, it } from 'vitest';
import type { ActorAuditEventSummary } from '../../lib/apiTypes';
import { SEGMENT_DEFINITIONS, segmentName } from '../../lib/segmentMetadata';
import type { DataEstateResponse } from '../../types';
import type { SavedLead } from '../../types/workspace';
import {
  PALETTE_PROVIDER_MAX,
  dataAssetRows,
  dedupePaletteBorrowers,
  glossaryRows,
  paletteAssetGroup,
  paletteSourceGroups,
  rankPaletteRows,
  recentBorrowerRows,
  savedViewRows,
  segmentRows,
} from './commandPaletteSources';

const event = (subject_id: string | null, entity_type = 'borrower'): ActorAuditEventSummary => ({
  event_type: 'VIEW_BORROWER',
  entity_type,
  subject_id,
  created_at: '2026-10-07T12:00:00Z',
});

const lead = (borrower_id: string, updated_at = '2026-10-07T12:00:00Z'): SavedLead => ({
  borrower_id,
  city: 'Chicago',
  state: 'IL',
  zip: '60611',
  saved_at: updated_at,
  updated_at,
});

const ids = ['B-0000000000001', 'B-0000000000002', 'B-0000000000003', 'B-0000000000004', 'B-0000000000005', 'B-0000000000006'];

describe('palette provider builders', () => {
  it('recent borrowers: borrower events with a masked id only, each once, newest first', () => {
    const rows = recentBorrowerRows([
      event(ids[0]),
      event('B-not-masked'),
      event(null),
      event(ids[1], 'campaign'),
      event(ids[0]),
      event(ids[2]),
    ]);
    expect(rows.map((row) => row.borrowerId)).toEqual([ids[0], ids[2]]);
    expect(rows[0].to).toBe(`/borrower-360/${ids[0]}`);
    // Any borrower event lists the borrower (a decision too), so the hint
    // claims no more than 'Recent'.
    expect(recentBorrowerRows([{ ...event(ids[3]), event_type: 'OUTREACH_APPROVE' }])[0].hint).toBe('Recent');
  });

  it('saved views open the Lead Queue with their exact params', () => {
    const [row] = savedViewRows([{ view_id: 'v1', name: 'Texas ITM', params: 'state=TX&segment=itm', created_at: '', updated_at: '' }]);
    expect(row).toEqual(expect.objectContaining({ label: 'Texas ITM', hint: 'Saved view', to: '/lead-queue?state=TX&segment=itm' }));
  });

  it('segments take their names and icons from segmentMetadata (never hard-coded)', () => {
    const rows = segmentRows();
    expect(rows.map((row) => row.label)).toEqual(SEGMENT_DEFINITIONS.map((definition) => segmentName(definition.code)));
    expect(rows[0].to).toBe(`/segment-intelligence?segment=${SEGMENT_DEFINITIONS[0].code}`);
  });

  it('glossary rows find a term by a search-only alias the glossary never shows (critic-11)', () => {
    const row = glossaryRows().find((entry) => entry.id === 'glossary-in-the-money');
    expect(row?.label).toBe('In the Money');
    expect(row?.keywords).toEqual(expect.arrayContaining(['ITM', 'in-the-money', 'prime refi candidates']));
    expect(rankPaletteRows(glossaryRows(), 'prime refi candidates').map((entry) => entry.id)).toContain('glossary-in-the-money');
  });

  it('data assets keep only assets with an asset page, each once', () => {
    const estate = {
      lanes: [
        {
          id: 'gold', title: 'Gold', description: '', status: 'live',
          assets: [
            { name: 'property_master', label: 'Property master', status: 'live', uc_object: 'mip.silver.property_master', note: '' },
            { name: 'property_master_again', label: 'Property master (again)', status: 'live', uc_object: 'mip.silver.property_master', note: '' },
            { name: 'unknown', label: 'Unknown', status: 'live', uc_object: 'mip.gold.not_a_mapped_asset', note: '' },
            { name: 'none', label: 'None', status: 'roadmap', uc_object: null, note: '' },
          ],
        },
      ],
    } as unknown as DataEstateResponse;
    const rows = dataAssetRows(estate);
    expect(rows.map((row) => row.label)).toEqual(['Property master']);
    expect(rows[0].to).toBe('/data-estate/assets/property_master');
    expect(rows.map((row) => row.label)).not.toContain('Unknown');
    expect(rows.map((row) => row.label)).not.toContain('None');
    expect(dataAssetRows(null)).toEqual([]);
    expect(paletteAssetGroup(estate, '')).toBeNull();
  });
});

describe('palette provider groups', () => {
  const base = { savedViews: [], savedLeads: {}, recentEvents: [] };

  it('an empty query shows Saved views and Recent borrowers only', () => {
    const groups = paletteSourceGroups({
      ...base,
      query: '',
      savedViews: [{ view_id: 'v1', name: 'Mine', params: 'a=1', created_at: '', updated_at: '' }],
      savedLeads: { [ids[3]]: lead(ids[3]) },
      recentEvents: [event(ids[0])],
    });
    expect(groups.map((group) => group.id)).toEqual(['saved-views', 'recent-borrowers']);
  });

  it('"refi" reaches Segments and Glossary, in the palette order', () => {
    const groups = paletteSourceGroups({ ...base, query: 'refi' });
    expect(groups.map((group) => group.id)).toEqual(['segments', 'glossary']);
  });

  it('caps every provider at five rows', () => {
    expect(PALETTE_PROVIDER_MAX).toBe(5);
    const groups = paletteSourceGroups({ ...base, query: '', recentEvents: ids.map((id) => event(id)) });
    expect(groups[0].rows).toHaveLength(5);
  });

  it('lists a borrower once: saved leads, then recents, then the live search', () => {
    const groups = paletteSourceGroups({
      ...base,
      query: 'B-000',
      savedLeads: { [ids[0]]: lead(ids[0]) },
      recentEvents: [event(ids[0]), event(ids[1])],
    });
    expect(groups.map((group) => [group.id, group.rows.map((row) => row.borrowerId)])).toEqual([
      ['saved-leads', [ids[0]]],
      ['recent-borrowers', [ids[1]]],
    ]);
    const live = dedupePaletteBorrowers(
      [{ borrower_id: ids[0] }, { borrower_id: ids[1] }, { borrower_id: ids[2] }],
      groups,
    );
    expect(live.map((row) => row.borrower_id)).toEqual([ids[2]]);
  });

  it('ranks with the action scoring, the fuzzy step last', () => {
    const rows = rankPaletteRows(
      [
        { id: 'a', label: 'Prime Refi Candidates', hint: '', icon: 'money', keywords: [], to: '/a' },
        { id: 'b', label: 'Refinance', hint: '', icon: 'money', keywords: [], to: '/b' },
        { id: 'c', label: 'Other', hint: '', icon: 'money', keywords: ['refi economics'], to: '/c' },
      ],
      'refi',
    );
    expect(rows.map((row) => row.id)).toEqual(['b', 'a', 'c']);
    expect(rankPaletteRows([{ id: 'x', label: 'Prime Refi Candidates', hint: '', icon: 'money', keywords: [], to: '/x' }], 'prirecan'))
      .toHaveLength(1);
  });
});
