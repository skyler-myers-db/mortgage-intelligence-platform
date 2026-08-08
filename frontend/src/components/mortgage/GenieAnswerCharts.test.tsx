/**
 * @vitest-environment happy-dom
 *
 * Strategy-board rendering contract (2026-08-07 audit C2). The board's
 * headline number is the most quotable thing on an Ask Genie answer, so the
 * column it comes from has to be a real measure — a ZIP is a place.
 */

import { createRoot, type Root } from 'react-dom/client';
import { act, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GenieStrategyBoard } from './GenieAnswerCharts';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('react-router', () => ({
  Link: ({ children, to }: { children: ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

describe('GenieStrategyBoard measure selection', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  const render = (node: ReactNode) => act(() => root.render(node));

  it('never promotes a ZIP to the headline metric when no measure exists', () => {
    render(
      <GenieStrategyBoard
        rows={[
          { city: 'Garland', zip: '75040' },
          { city: 'Bridgeport', zip: '06614' },
        ]}
      />,
    );

    expect(document.body.textContent).toContain('Garland');
    expect(document.body.textContent).not.toContain('75,040');
    expect(document.querySelector('.genie-board__value')).toBeNull();
  });

  it('ignores an identifier handed in as the explicit y column', () => {
    render(
      <GenieStrategyBoard
        rows={[
          { city: 'Garland', zip5: 75040, borrowers: 1503 },
          { city: 'Bridgeport', zip5: 6614, borrowers: 1482 },
        ]}
        x="city"
        y="zip5"
      />,
    );

    expect(document.body.textContent).not.toContain('75,040');
    // Falls back to the honest measure in the same row.
    expect(document.querySelector('.genie-board__value')?.textContent).toBe('1,503');
  });

  it('renders a real measure, formatted for its unit', () => {
    render(
      <GenieStrategyBoard
        rows={[
          { city: 'Garland', equity_estimate: 412350 },
          { city: 'Bridgeport', equity_estimate: 289000 },
        ]}
        x="city"
        y="equity_estimate"
      />,
    );

    expect(document.querySelector('.genie-board__value')?.textContent).toBe('$412,350');
  });
});
