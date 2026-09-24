/**
 * @vitest-environment happy-dom
 *
 * The topbar status pill (audit 2026-09-21 delivery-01 and the wave-1c
 * follow-up): the shell's connection outranks the last health payload, so an
 * ended session never reads "Live" behind the session dialog, and a warehouse
 * resuming from auto-stop reads as a calm amber "Waking warehouse" with an
 * elapsed timer instead of red "Degraded".
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HealthPayload } from '../../lib/apiTypes';
import type { ConnectionStatus } from '../connectionState';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const shell: { health: HealthPayload | null; connection: ConnectionStatus; warehouseResumingSince: number | null } = {
  health: null,
  connection: 'online',
  warehouseResumingSince: null,
};

vi.mock('../../lib/api', () => ({ api: { borrowerSearch: vi.fn().mockResolvedValue([]) } }));
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
vi.mock('../FootprintProvider', () => ({ useFootprint: () => ({ usingFallback: false }) }));
vi.mock('./IdentityMenu', () => ({ IdentityMenu: () => null }));

import { Topbar, systemStatusViewModel } from './Topbar';

const payload = (warehouse: string, extra: Partial<HealthPayload> = {}): HealthPayload => ({
  status: 'ok',
  mode: 'live',
  dependencies: { warehouse, lakebase: 'up', genie: 'up' },
  circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
  ...extra,
});

describe('systemStatusViewModel', () => {
  it('reads Live, Degraded and Probing as before', () => {
    expect(systemStatusViewModel(payload('up')).label).toBe('Live');
    expect(systemStatusViewModel(payload('down', { status: 'degraded' })).label).toBe('Degraded');
    expect(systemStatusViewModel(payload('down', { status: 'degraded' })).dotClass).toBe('dot danger');
    expect(systemStatusViewModel(null).label).toBe('Probing');
  });

  it('reads a resuming warehouse as a calm amber "Waking warehouse" carrying its start time', () => {
    const view = systemStatusViewModel(payload('resuming'), 'online', 1_000);

    expect(view.label).toBe('Waking warehouse');
    expect(view.dotClass).toBe('dot amber');
    expect(view.ariaLabel).toBe('System status: Waking warehouse.');
    expect(view.tooltip).toContain('2–6 s');
    expect(view.resumingSince).toBe(1_000);
  });

  it('does not call it a routine resume when anything else is wrong', () => {
    const lakebaseDown = payload('resuming', { status: 'degraded' });
    lakebaseDown.dependencies = { warehouse: 'resuming', lakebase: 'down', genie: 'up' };
    expect(systemStatusViewModel(lakebaseDown, 'online', 1).label).toBe('Degraded');

    const halfOpen = payload('resuming', { circuit_breakers: { warehouse: 'half_open' } });
    expect(systemStatusViewModel(halfOpen, 'online', 1).label).toBe('Degraded');

    expect(systemStatusViewModel(payload('resuming', { status: 'degraded' }), 'online', 1).label).toBe('Degraded');
  });

  it('lets the connection outrank the last payload: an ended session never reads Live', () => {
    const live = payload('up');

    expect(systemStatusViewModel(live, 'session_expired').label).toBe('Session ended');
    expect(systemStatusViewModel(live, 'session_expired').dotClass).toBe('dot amber');
    expect(systemStatusViewModel(live, 'offline').label).toBe('Offline');
    expect(systemStatusViewModel(live, 'unreachable').label).toBe('Unreachable');
    expect(systemStatusViewModel(payload('resuming'), 'offline', 1).label).toBe('Offline');
    expect(systemStatusViewModel(null, 'session_expired').label).toBe('Session ended');
  });

  it('keeps the unreachable probe body honest with the default connection', () => {
    expect(systemStatusViewModel({ status: 'unreachable', mode: 'unknown', dependencies: {} }).label).toBe('Unreachable');
  });
});

describe('the rendered topbar pill', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-24T12:00:00Z'));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    shell.health = null;
    shell.connection = 'online';
    shell.warehouseResumingSince = null;
    vi.useRealTimers();
  });

  const pill = () => container.querySelector<HTMLElement>('[data-testid="system-status-pill"]')!;
  const render = () => act(() => root.render(<MemoryRouter><Topbar /></MemoryRouter>));

  it('shows "Waking warehouse" with an amber dot and a ticker outside the label and aria text', () => {
    shell.health = payload('resuming');
    shell.warehouseResumingSince = Date.now();
    render();

    expect(pill().querySelector('.topbar__pill-label')?.textContent).toBe('Waking warehouse');
    expect(pill().getAttribute('aria-label')).toBe('System status: Waking warehouse.');
    expect(pill().querySelector('.dot')?.className).toBe('dot amber');
    const ticker = pill().querySelector<HTMLElement>('.mono');
    expect(ticker?.getAttribute('aria-hidden')).toBe('true');
    expect(ticker?.textContent).toBe('0s');

    act(() => {
      vi.advanceTimersByTime(4_000);
    });
    expect(ticker?.textContent).toBe('4s');
  });

  it('never reads Live behind an ended session', () => {
    shell.health = payload('up');
    shell.connection = 'session_expired';
    render();

    expect(pill().textContent).toBe('Session ended');
    expect(pill().querySelector('.mono')).toBeNull();
  });
});
