/** @vitest-environment happy-dom */

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { UseWarmingUpRetryResult } from '../lib/useWarmingUpRetry';
import { LoadState } from './analytics.charts';

function query(overrides: Partial<UseWarmingUpRetryResult<{ value: number }>> = {}) {
  return {
    data: { value: 42 },
    warmingUp: null,
    error: null,
    isFetching: false,
    isPlaceholderData: false,
    manualRetry: vi.fn(),
    ...overrides,
  } as UseWarmingUpRetryResult<{ value: number }>;
}

describe('LoadState stale-while-refreshing contract', () => {
  it('keeps the current panel mounted and visibly announces a background refresh', () => {
    const html = renderToStaticMarkup(
      <LoadState query={query({ isFetching: true })} title="Economics analytics">
        {(data) => <div data-testid="stable-data">Value {data.value}</div>}
      </LoadState>,
    );

    expect(html).toContain('Value 42');
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('role="status"');
    expect(html).toContain('Updating economics analytics');
    expect(html).toContain('stable-refresh-status');
  });

  it('does not render the overlay after refresh completion', () => {
    const html = renderToStaticMarkup(
      <LoadState query={query()} title="Economics analytics">
        {(data) => <div>Value {data.value}</div>}
      </LoadState>,
    );

    expect(html).not.toContain('stable-refresh-status');
    expect(html).not.toContain('aria-busy');
  });
});
