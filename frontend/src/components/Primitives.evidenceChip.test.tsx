/**
 * @vitest-environment happy-dom
 *
 * The evidence chip's freshness dot reads its age through lib/time (audit
 * 2026-09-21 responsive-07 item 8b): "Fresh — refreshed 2 days ago", never
 * the raw wire timestamp a screen reader would spell out digit by digit.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EvidenceChip } from './Primitives';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('./AppContext', () => ({
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true }),
}));

const NOW = new Date('2026-07-16T08:00:00Z');

describe('EvidenceChip freshness dot', () => {
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.useRealTimers();
  });

  const dot = () => document.querySelector<HTMLElement>('.evidence-chip__dot');

  it('labels the dot with the band and a relative age', () => {
    act(() => root.render(<EvidenceChip source={{ title: 'Lien', updatedAt: '2026-07-14 08:00:00' }}>Lien</EvidenceChip>));

    expect(dot()?.getAttribute('aria-label')).toBe('Fresh — refreshed 2 days ago');
    expect(dot()?.getAttribute('aria-label')).not.toContain('2026-07-14');
  });

  it('says a stale source is older than a month, with the year past eleven months', () => {
    act(() => root.render(<EvidenceChip source={{ title: 'Lien', updatedAt: '2025-06-01T12:00:00Z' }}>Lien</EvidenceChip>));

    expect(dot()?.getAttribute('aria-label')).toBe('Stale — refreshed Jun 1, 2025');
  });

  it('renders no dot for a source without a refresh time', () => {
    act(() => root.render(<EvidenceChip source={{ title: 'Lien' }}>Lien</EvidenceChip>));

    expect(dot()).toBeNull();
  });
});
