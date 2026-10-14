/**
 * @vitest-environment happy-dom
 *
 * The closed RUM vocabularies and the client-side event validator
 * (D-platform-process-d2; audit 2026-09-21 stack-08, runtime-09). The helpers
 * never return null or undefined; the validator mirrors the server's closed
 * sets so an event the server would refuse is dropped alone, before it can
 * cost its whole batch a 422.
 */
import { describe, expect, it } from 'vitest';
import { ROUTES } from './routeMeta';
import {
  RUM_INTERACTION_TARGETS,
  RUM_LCP_ELEMENTS,
  RUM_ROUTE_TEMPLATES,
  closedInteractionTarget,
  closedLcpElement,
  isValidRumEvent,
  lcpElementBucket,
  nearestRumTarget,
  type RumEventShape,
} from './rumVocabulary';

function fragment(html: string): HTMLElement {
  const host = document.createElement('div');
  host.innerHTML = html;
  return host;
}

describe('nearestRumTarget', () => {
  it('reads the nearest [data-rum-target] ancestor when its value is in the set', () => {
    const host = fragment(
      '<nav data-rum-target="pager"><div data-rum-target="filter"><button><span id="leaf">x</span></button></div></nav>',
    );
    expect(nearestRumTarget(host.querySelector('#leaf'))).toBe('filter');
    expect(nearestRumTarget(host.querySelector('nav'))).toBe('pager');
  });

  it("answers 'other' for an unknown value, no ancestor, a text node, a document or null", () => {
    const host = fragment('<div data-rum-target="tr.lead-row > td"><i id="i">t</i></div><b id="b">u</b>');
    expect(nearestRumTarget(host.querySelector('#i'))).toBe('other');
    expect(nearestRumTarget(host.querySelector('#b'))).toBe('other');
    expect(nearestRumTarget(host.querySelector('#b')?.firstChild ?? null)).toBe('other');
    expect(nearestRumTarget(document)).toBe('other');
    expect(nearestRumTarget(null)).toBe('other');
    expect(nearestRumTarget(undefined)).toBe('other');
  });
});

describe('lcpElementBucket', () => {
  it('buckets a tag in the set, lowercase, and anything else as other', () => {
    const host = fragment('<img id="img" alt=""><h2 id="h2">t</h2><section id="s">x</section><table id="t"></table>');
    expect(lcpElementBucket(host.querySelector('#img'))).toBe('img');
    expect(lcpElementBucket(host.querySelector('#h2'))).toBe('h2');
    expect(lcpElementBucket(host.querySelector('#t'))).toBe('table');
    expect(lcpElementBucket(host.querySelector('#s'))).toBe('other');
    expect(lcpElementBucket(document.createElementNS('http://www.w3.org/2000/svg', 'svg'))).toBe('svg');
    expect(lcpElementBucket(null)).toBe('other');
  });

  it('folds a reported target that is not a closed value (a selector) to other', () => {
    expect(closedInteractionTarget('lead-row')).toBe('lead-row');
    expect(closedInteractionTarget('#leads > tr:nth-child(3)')).toBe('other');
    expect(closedInteractionTarget(undefined)).toBe('other');
    expect(closedLcpElement('canvas')).toBe('canvas');
    expect(closedLcpElement('html > body > img')).toBe('other');
  });

  it('keeps both sets closed, duplicate-free and ending in other', () => {
    for (const set of [RUM_INTERACTION_TARGETS, RUM_LCP_ELEMENTS]) {
      expect(new Set(set).size).toBe(set.length);
      expect(set).toContain('other');
    }
  });
});

describe('isValidRumEvent', () => {
  const ok: RumEventShape = { metric: 'inp', value: 180, rating: 'good', route: '/lead-queue', details: { interaction_target: 'sort' } };

  it('accepts the closed shapes', () => {
    expect(isValidRumEvent(ok)).toBe(true);
    expect(isValidRumEvent({ metric: 'cls', value: 0.01, rating: 'good', route: '/*', navigation_type: 'soft_navigation' })).toBe(true);
    expect(isValidRumEvent({
      metric: 'api_call', value: 12, rating: 'info', route: '/', details: { api_route: '/api/leads', cache: 'hit', total_ms: 3 },
    })).toBe(true);
    expect(isValidRumEvent({
      metric: 'client_error', value: 1, rating: 'info', route: '/borrower-360/:id',
      details: { error_name: 'TypeError', error_kind: 'render', error_source: 'caught', boundary: 'drawer' },
    })).toBe(true);
  });

  it.each<[string, RumEventShape]>([
    ['unknown metric', { ...ok, metric: 'long_task' }],
    ['value over the ceiling', { ...ok, value: 600_001 }],
    ['negative value', { ...ok, value: -1 }],
    ['NaN value', { ...ok, value: Number.NaN }],
    ['unknown rating', { ...ok, rating: 'needs-improvement' }],
    ['concrete path', { ...ok, route: '/borrower-360/B-0123456789ABC' }],
    ['old conversation spelling', { ...ok, route: '/ask-genie/:conversation_id' }],
    ['unknown navigation type', { ...ok, navigation_type: 'back-forward' }],
    ['selector target', { ...ok, details: { interaction_target: '#x' } }],
    ['lcp key on inp', { ...ok, details: { lcp_element: 'img' } }],
    ['phase over the ceiling', { ...ok, details: { processing_ms: 700_000 } }],
    ['from_route on inp', { ...ok, details: { from_route: '/' } }],
    ['concrete from_route', { metric: 'route_change', value: 5, rating: 'good', route: '/', details: { from_route: '/lead-queue?x=1' } }],
    ['unknown cache', { metric: 'api_call', value: 5, rating: 'info', route: '/', details: { api_route: '/api/leads', cache: 'warm' } }],
    ['open error name', { metric: 'client_error', value: 1, rating: 'info', route: '/', details: { error_name: 'Cannot read x', error_kind: 'render', error_source: 'caught' } }],
  ])('refuses %s', (_label, event) => {
    expect(isValidRumEvent(event)).toBe(false);
  });

  it('holds every route registry pattern plus /*', () => {
    const patterns = new Set([...Object.values(ROUTES).map((route) => route.pattern as string), '/*']);
    expect(RUM_ROUTE_TEMPLATES).toEqual(patterns);
    expect(RUM_ROUTE_TEMPLATES.has('/audit-ledger')).toBe(true);
    expect(RUM_ROUTE_TEMPLATES.has('/ask-genie/:conversationId')).toBe(true);
  });
});
