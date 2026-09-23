/**
 * The default fixture registry: every READ endpoint the app needs to render
 * all routes populated. One module per API domain under ./data/.
 *
 * Adding an endpoint: put a typed `fixture<ResponseType>(...)` in the matching
 * domain module. `new MockApi(...)` throws on a duplicate `METHOD pattern`
 * key, so two lanes cannot silently register the same endpoint twice.
 */
import type { FixtureEntry } from './mockApi';
import { adminFixtures } from './data/admin';
import { analyticsFixtures } from './data/analytics';
import { dataEstateFixtures } from './data/dataEstate';
import { genieFixtures } from './data/genie';
import { leadFixtures } from './data/leads';
import { offerFixtures } from './data/offers';
import { portfolioFixtures } from './data/portfolio';
import { rateWindowFixtures } from './data/rateWindow';
import { salesOpsFixtures } from './data/salesOps';
import { segmentFixtures } from './data/segments';
import { shellFixtures } from './data/shell';

export function defaultFixtures(): FixtureEntry[] {
  return [
    ...shellFixtures,
    ...portfolioFixtures,
    ...segmentFixtures,
    ...leadFixtures,
    ...offerFixtures,
    ...analyticsFixtures,
    ...rateWindowFixtures,
    ...salesOpsFixtures,
    ...genieFixtures,
    ...adminFixtures,
    ...dataEstateFixtures,
  ];
}
