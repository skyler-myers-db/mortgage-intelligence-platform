import { describe, expect, it } from 'vitest';
import { feature as topoFeature } from 'topojson-client';
import statesTopology from 'us-atlas/states-albers-10m.json';
import {
  buildUsaStateMapPayload,
  buildCountiesPayload,
  buildLeadQueuePath,
  countyDisplayName,
  featureBBox,
  FIPS_TO_USCODE,
  geometryToPath,
  labelAnchor,
} from './USChoroplethMap.utils';
import type { CountyRollup } from '../../types';
import type { Feature, FeatureCollection } from 'geojson';

describe('USChoroplethMap geography helpers', () => {
  it('anchors a label at the area centroid of the largest polygon', () => {
    // A 10x10 square and a far-off 2x2 island: the label sits on the square.
    const anchor = labelAnchor({
      type: 'MultiPolygon',
      coordinates: [
        [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
        [[[50, 50], [52, 50], [52, 52], [50, 52], [50, 50]]],
      ],
    });
    expect(anchor).toEqual([5, 5]);
    expect(labelAnchor({ type: 'Point', coordinates: [1, 1] })).toBeNull();
  });

  it('formats county names without double-appending County', () => {
    const base = {
      fips_5: '17031',
      state: 'IL',
      addressable_borrowers: 100,
      in_the_money_borrowers: 20,
      high_opportunity_borrowers: 5,
      avg_opportunity_score: 80,
      top_segment_code: 'itm',
    } satisfies Omit<CountyRollup, 'county_name'>;

    expect(countyDisplayName({ ...base, county_name: 'Cook' })).toBe('Cook County');
    expect(countyDisplayName({ ...base, county_name: 'Cook County' })).toBe('Cook County');
    expect(countyDisplayName({ ...base, county_name: '' })).toBe('17031');
  });

  it('converts polygon geometry into SVG path data and computes its bbox', () => {
    const feature: Feature = {
      type: 'Feature',
      id: '17031',
      properties: {},
      geometry: {
        type: 'Polygon',
        coordinates: [[[1, 2], [3, 2], [3, 4], [1, 2]]],
      },
    };

    expect(geometryToPath(feature.geometry)).toBe('M1.0,2.0L3.0,2.0L3.0,4.0L1.0,2.0Z');
    expect(featureBBox(feature)).toEqual([1, 2, 3, 4]);
  });

  it('builds lead queue links with geography, segment, and portfolio filters', () => {
    expect(
      buildLeadQueuePath({
        geo: { state: 'IL', county: '17031', zip: '60626' },
        segmentFilter: ['itm', 'equity'],
        segmentFilterMode: 'all',
        portfolioCriteria: {
          min_score: 78,
          approval_status: 'hold',
          ignored_null: null,
          ignored_empty: '',
        },
      }),
    ).toBe('/lead-queue?state=IL&county=17031&zip=60626&segment_codes=itm%2Cequity&segment_mode=all&min_score=78&approval_status=hold');
  });

  it('uses the canonical single-segment lead queue URL shape', () => {
    expect(
      buildLeadQueuePath({
        geo: { state: 'IL', county: '17031' },
        segmentFilter: ['itm'],
        segmentFilterMode: 'all',
      }),
    ).toBe('/lead-queue?state=IL&county=17031&segment=itm');
  });

  it('builds county payloads from a national FeatureCollection without blank viewboxes', () => {
    const fc: FeatureCollection = {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          id: '17031',
          properties: { name: 'Cook' },
          geometry: {
            type: 'Polygon',
            coordinates: [[[1, 2], [3, 2], [3, 4], [1, 2]]],
          },
        },
        {
          type: 'Feature',
          id: '17043',
          properties: { name: 'DuPage' },
          geometry: {
            type: 'Polygon',
            coordinates: [[[10, 5], [12, 5], [12, 9], [10, 5]]],
          },
        },
        {
          type: 'Feature',
          id: '06001',
          properties: { name: 'Alameda' },
          geometry: {
            type: 'Polygon',
            coordinates: [[[100, 100], [101, 100], [101, 101], [100, 100]]],
          },
        },
      ],
    };

    const payload = buildCountiesPayload(fc, 'il', '17', 1);
    expect(payload?.state).toBe('il');
    expect(payload?.viewBox).toBe('0.0 1.0 13.0 9.0');
    expect(payload?.features.map((feature) => feature.id)).toEqual(['17031', '17043']);
    expect(payload?.features[0]).toMatchObject({
      name: 'Cook',
      paths: 'M1.0,2.0L3.0,2.0L3.0,4.0L1.0,2.0Z',
      cx: 2,
      cy: 3,
    });
    expect(buildCountiesPayload(fc, 'xx', '99')).toBeNull();
  });

  it('adapts us-atlas state topology into the existing USPS-keyed map shape', () => {
    const topology = statesTopology as {
      objects: { states: unknown };
    };
    const fc = topoFeature(
      topology as never,
      topology.objects.states as never,
    ) as unknown as FeatureCollection;

    const payload = buildUsaStateMapPayload(fc);
    const [x, y, width, height] = payload.viewBox.split(' ').map(Number);
    const ids = new Set(payload.locations.map((location) => location.id));

    expect(payload.label).toBe('United States');
    expect(payload.locations).toHaveLength(51);
    expect([x, y, width, height].every(Number.isFinite)).toBe(true);
    expect(width).toBeGreaterThan(0);
    expect(height).toBeGreaterThan(0);
    expect(ids.has('il')).toBe(true);
    expect(ids.has('ca')).toBe(true);
    expect(ids.has('tx')).toBe(true);
    expect(ids.has('dc')).toBe(true);
    expect(payload.locations.find((location) => location.id === 'dc')?.name).toBe(
      'Washington, DC',
    );
    expect(payload.locations.every((location) => location.path.startsWith('M'))).toBe(true);
    // Every state gets a label anchor inside its own bounding box.
    for (const feature of fc.features) {
      const id = String(feature.id ?? '').padStart(2, '0');
      const location = payload.locations.find((candidate) => candidate.id === FIPS_TO_USCODE[id]);
      if (!location) continue;
      const [x0, y0, x1, y1] = featureBBox(feature);
      const [lx, ly] = location.labelAt ?? [Number.NaN, Number.NaN];
      expect(lx >= x0 && lx <= x1 && ly >= y0 && ly <= y1, `${location.id} label inside its box`).toBe(true);
    }
  });

  it('keeps Genie state-map lookups aligned with normalized USPS values', () => {
    const topology = statesTopology as {
      objects: { states: unknown };
    };
    const fc = topoFeature(
      topology as never,
      topology.objects.states as never,
    ) as unknown as FeatureCollection;
    const payload = buildUsaStateMapPayload(fc);
    const illinois = payload.locations.find((location) => location.name === 'Illinois');

    expect(illinois?.id.toUpperCase()).toBe('IL');
  });
});
