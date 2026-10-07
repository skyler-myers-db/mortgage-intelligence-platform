import { describe, expect, it } from 'vitest';
import {
  ROUTE_NAV_BLOCK_PROPERTY,
  ROUTE_NAV_DOCKED_ATTRIBUTE,
  ROUTE_NAV_MAX_SCROLLPORT_SHARE,
  routeNavDocks,
} from './routeNavDock';

/**
 * The route-nav dock policy (report 12.4 #5, D-theme-nav-b): docked while the
 * measured nav is at most one sixth of `.main`'s content box. The cases are
 * the ruling's table: a 57px one-line nav, a 97px two-line nav, a 177px
 * wrapped nav, the 1366x768 laptop scrollports (564-604px) and 400% zoom.
 */
describe('routeNavDocks', () => {
  it('is one sixth, written as the properties the shell reads', () => {
    expect(ROUTE_NAV_MAX_SCROLLPORT_SHARE).toBe(1 / 6);
    expect(ROUTE_NAV_BLOCK_PROPERTY).toBe('--route-nav-block');
    expect(ROUTE_NAV_DOCKED_ATTRIBUTE).toBe('data-docked');
  });

  it.each([
    [57, 564, true],
    [97, 564, false],
    [97, 604, true],
    [97, 582, true],
    [97, 581, false],
    [177, 748, false],
    [57, 169, false],
    [57, 342, true],
    [137, 822, true],
  ])('a %ipx nav in a %ipx scrollport docks: %s', (nav, scrollport, docks) => {
    expect(routeNavDocks(nav, scrollport)).toBe(docks);
  });

  it('never docks an unmeasured nav or scrollport', () => {
    expect(routeNavDocks(0, 900)).toBe(false);
    expect(routeNavDocks(57, 0)).toBe(false);
    expect(routeNavDocks(0, 0)).toBe(false);
  });
});
