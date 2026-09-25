/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { WarmingUpState } from '../../lib/useWarmingUpRetry';
import { WarmingUpBlock } from './WarmingUpBlock';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// The DegradedBanner already names the warehouse outage.
vi.mock('../HealthProvider', () => ({
  useOptionalHealth: () => ({ health: { dependencies: { warehouse: 'down', lakebase: 'up', genie: 'up' } }, connection: 'online' }),
}));
const chunk = vi.hoisted(() => ({ loads: 0 }));
vi.mock('./WarmingUpBlock.wait', () => {
  chunk.loads += 1;
  return { WaitLine: () => <span data-testid="wait-line" />, ReferenceDetails: () => null };
});

/**
 * A block that steps aside for the banner (healthRecovery.blockDefersToBanner)
 * renders nothing, so it never fetches the wait-clock chunk either; a block
 * that shows loads it. (WarmingUpBlock.test preloads the chunk; this file
 * must not.)
 */

function warming(dependency: string): WarmingUpState {
  return { dependency, label: 'Warming up', attempt: 1, maxAttempts: 6, correlationId: null, intervalMs: 5_000 };
}

let root: Root;

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
});

async function flush(): Promise<void> {
  await act(async () => {
    await vi.dynamicImportSettled();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('WarmingUpBlock under a banner that names its dependency', { timeout: 30_000 }, () => {
  it('renders nothing and loads no clocks; a block that shows loads them', async () => {
    act(() => root.render(<WarmingUpBlock state={warming('warehouse')} />));
    await flush();
    expect(document.querySelector('[data-testid="warming-up-block"]')).toBeNull();
    expect(document.getElementById('root')?.innerHTML).toBe('');
    expect(chunk.loads).toBe(0);

    // An unrelated dependency warming up is not the banner's story.
    act(() => root.render(<WarmingUpBlock state={warming('genie')} />));
    await flush();
    expect(document.querySelector('[data-testid="warming-up-block"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="wait-line"]')).not.toBeNull();
    expect(chunk.loads).toBe(1);
  });
});
