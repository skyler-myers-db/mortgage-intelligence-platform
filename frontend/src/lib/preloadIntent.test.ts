import { beforeEach, describe, expect, it, vi } from 'vitest';

const preloadRouteForPath = vi.fn();
vi.mock('./routePreloaders', () => ({ preloadRouteForPath: (path: string) => preloadRouteForPath(path) }));
const saveData = vi.hoisted(() => ({ on: false }));
vi.mock('./prefetch', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./prefetch')>()),
  saveDataRequested: () => saveData.on,
}));

import { preloadIntentProps } from './preloadIntent';

beforeEach(() => {
  preloadRouteForPath.mockReset();
  saveData.on = false;
});

describe('preloadIntentProps (bundle-09 item 1)', () => {
  it('warms the route chunk on pointer entry and on focus', () => {
    const props = preloadIntentProps('/borrower-360/B-0000000000001');
    props.onPointerEnter();
    props.onFocus();
    expect(preloadRouteForPath.mock.calls).toEqual([
      ['/borrower-360/B-0000000000001'],
      ['/borrower-360/B-0000000000001'],
    ]);
  });

  it('preloads nothing while the browser asks to save data', () => {
    saveData.on = true;
    const props = preloadIntentProps('/offer-orchestrator/B-0000000000001');
    props.onPointerEnter();
    props.onFocus();
    expect(preloadRouteForPath).not.toHaveBeenCalled();
  });

  it('hands out only the two intent handlers (no data work)', () => {
    expect(Object.keys(preloadIntentProps('/x')).sort()).toEqual(['onFocus', 'onPointerEnter']);
  });
});
