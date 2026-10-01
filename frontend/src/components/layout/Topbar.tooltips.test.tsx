/**
 * @vitest-environment happy-dom
 *
 * Topbar tooltips (2026-09-21 audit critic-08): the command palette, theme,
 * Genie and Console buttons, the tenant pill, the system-status pill and the
 * footprint-fallback chip moved off native `title` onto ui/Tooltip. No DOM
 * `title` is left on them, their accessible names are unchanged, and each
 * one's `aria-describedby` resolves to its tooltip text (the Genie launcher's
 * own status id is kept, merged). The status pill's per-dependency details
 * are its description.
 */
import { act } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HealthPayload } from '../../lib/apiTypes';
import { GENIE_LAUNCHER_STATUS_ID } from '../../lib/genieTurnStatus';
import { mount } from '../../test/render';

const shell: { health: HealthPayload | null; connection: 'online'; warehouseResumingSince: number | null } = {
  health: {
    status: 'ok',
    mode: 'live',
    dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
    circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
  },
  connection: 'online',
  warehouseResumingSince: null,
};
const genieTurn = { status: 'idle' as 'idle' | 'running' };

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { borrowerSearch: vi.fn().mockResolvedValue([]) },
}));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    lender: 'Summit Mortgage',
    theme: 'dark',
    setTheme: vi.fn(),
    genieOpen: false,
    setGenieOpen: vi.fn(),
    consoleOpen: false,
    setConsoleOpen: vi.fn(),
  }),
}));
vi.mock('../HealthProvider', () => ({ useHealth: () => shell }));
vi.mock('../FootprintProvider', () => ({ useFootprint: () => ({ usingFallback: true }) }));
vi.mock('./IdentityMenu', () => ({ IdentityMenu: () => null }));
vi.mock('../../lib/genieTurnStatus', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/genieTurnStatus')>()),
  useGenieTurnStatus: () => genieTurn.status,
}));

import { Topbar } from './Topbar';

/** The text an `aria-describedby` list resolves to, ids in order. */
function description(element: Element): string {
  return (element.getAttribute('aria-describedby') ?? '')
    .split(/\s+/)
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? `<missing ${id}>`)
    .join(' | ');
}

describe('Topbar tooltips', () => {
  let container: HTMLElement;

  beforeEach(async () => {
    vi.useFakeTimers();
    genieTurn.status = 'idle';
    ({ container } = await mount(<MemoryRouter><Topbar /></MemoryRouter>));
    // The footprint chip waits out a 3 s mount grace.
    await act(async () => {
      vi.advanceTimersByTime(3_000);
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const migrated = () => ({
    palette: container.querySelector<HTMLElement>('.topbar__search-kbd')!,
    theme: container.querySelector<HTMLElement>('button[aria-label="Toggle theme"]')!,
    genie: container.querySelector<HTMLElement>('button[aria-label="Toggle Genie chat"]')!,
    console: container.querySelector<HTMLElement>('button[aria-label="Toggle console"]')!,
    tenant: container.querySelector<HTMLElement>('[aria-label="Configured tenant: Summit Mortgage"]')!,
    status: container.querySelector<HTMLElement>('[data-testid="system-status-pill"]')!,
    footprint: container.querySelector<HTMLElement>('[data-testid="footprint-fallback-chip"]')!,
  });

  it('leaves no native title on the migrated controls and keeps their accessible names', () => {
    const controls = migrated();
    for (const [name, element] of Object.entries(controls)) {
      expect(element, name).not.toBeNull();
      expect(element.hasAttribute('title'), `${name} still carries title=`).toBe(false);
    }
    expect(controls.palette.getAttribute('aria-label')).toMatch(/^Open command palette \((⌘K|Ctrl K)\)$/);
    expect(controls.status.getAttribute('aria-label')).toBe('System status: Live.');
    expect(controls.tenant.textContent).toBe('Summit Mortgage');
  });

  it('describes each control with its tooltip text', () => {
    const controls = migrated();

    expect(description(controls.palette)).toMatch(/^Command palette \((⌘K|Ctrl K)\)$/);
    expect(description(controls.theme)).toBe('Switch to light theme');
    expect(description(controls.genie)).toBe('Ask Genie');
    expect(description(controls.console)).toBe('Console (theme, density, accent)');
    expect(description(controls.tenant)).toBe(
      'Configured tenant · Summit Mortgage. Lender configuration is applied server-side.',
    );
    expect(description(controls.footprint)).toMatch(/^The \/api\/config\/footprint fetch failed/);
  });

  it('carries the status pill details as its description', () => {
    const text = description(migrated().status);

    expect(text).toContain('System status · live');
    expect(text).toContain('warehouse=up · lakebase=up · genie=up');
    expect(text).toContain('breakers warehouse=closed / lakebase=closed / genie=closed');
  });

  it('merges the Genie launcher status id instead of overwriting it', async () => {
    genieTurn.status = 'running';
    ({ container } = await mount(<MemoryRouter><Topbar /></MemoryRouter>));

    const genie = migrated().genie;
    const ids = (genie.getAttribute('aria-describedby') ?? '').split(' ');
    expect(ids[0]).toBe(GENIE_LAUNCHER_STATUS_ID);
    expect(ids).toHaveLength(2);
    expect(document.getElementById(ids[1] ?? '')?.textContent).toBe('Ask Genie');
  });
});
