/**
 * State label placement on the real us-atlas geometry (review follow-up to
 * audit dataviz-02: the Florida label sat on the Florida / Georgia line).
 */
import { describe, expect, it } from 'vitest';
import { feature as topoFeature } from 'topojson-client';
import statesTopology from 'us-atlas/states-albers-10m.json';
import type { FeatureCollection, Geometry } from 'geojson';
import { LABEL_CLEARANCE, labelAnchor, labelClearance } from './USChoroplethMap.labels';

function stateGeometries(): Map<string, Geometry> {
  const topology = statesTopology as { objects: { states: unknown } };
  const fc = topoFeature(topology as never, topology.objects.states as never) as unknown as FeatureCollection;
  const out = new Map<string, Geometry>();
  for (const feature of fc.features) {
    const name = (feature.properties as { name?: string } | null)?.name;
    if (name && feature.geometry) out.set(name, feature.geometry);
  }
  return out;
}

describe('state label anchors', () => {
  const states = stateGeometries();

  it('puts every label inside its own state', () => {
    expect(states.size).toBeGreaterThanOrEqual(51);
    for (const [name, geometry] of states) {
      const at = labelAnchor(geometry);
      expect(at, name).not.toBeNull();
      expect(labelClearance(geometry, at as [number, number]), name).toBeGreaterThan(0);
    }
  });

  it('moves a label off its state edge: Florida off the Georgia line, Louisiana inland', () => {
    for (const name of ['Florida', 'Louisiana']) {
      const geometry = states.get(name) as Geometry;
      expect(labelClearance(geometry, labelAnchor(geometry) as [number, number]), name).toBeGreaterThanOrEqual(LABEL_CLEARANCE);
    }
  });

  it('keeps the centroid where it already clears the edge', () => {
    // A 100 x 100 square: the centroid is the most interior point.
    expect(labelAnchor({ type: 'Polygon', coordinates: [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]] })).toEqual([50, 50]);
  });

  it('never anchors outside the shape, even where the centroid falls in a notch', () => {
    // A U: the area centroid (150, ~136) lies in the notch, outside the shape.
    const u: Geometry = {
      type: 'Polygon',
      coordinates: [[[0, 0], [300, 0], [300, 300], [200, 300], [200, 100], [100, 100], [100, 300], [0, 300], [0, 0]]],
    };
    const at = labelAnchor(u) as [number, number];
    expect(labelClearance(u, at)).toBeGreaterThanOrEqual(40);
  });
});
