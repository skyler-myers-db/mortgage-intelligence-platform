/**
 * @vitest-environment happy-dom
 *
 * LenderMark (audit responsive-10, 12.4 #9; deviation:lender-mark): the
 * reviewed lender mark shows only on a co-branded build whose session lender
 * equals the lender the build validated it for. Everything else is the
 * prototype's building glyph, and without the build's meta nothing
 * subscribes to the session.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the source tree under Vitest only.
import { readdirSync, readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { mount } from '../../test/render';

declare const process: { cwd(): string };

const session = vi.hoisted(() => ({ fn: vi.fn() }));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { session: (...args: unknown[]) => session.fn(...args), borrowerSearch: vi.fn().mockResolvedValue([]) },
}));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    lender: 'Fixture Test Lending',
    theme: 'dark',
    setTheme: vi.fn(),
    genieOpen: false,
    setGenieOpen: vi.fn(),
    consoleOpen: false,
    setConsoleOpen: vi.fn(),
  }),
}));
vi.mock('../HealthProvider', () => ({ useHealth: () => ({ health: null, connection: 'online', warehouseResumingSince: null }) }));
vi.mock('../FootprintProvider', () => ({ useFootprint: () => ({ usingFallback: false }) }));
vi.mock('./IdentityMenu', () => ({ IdentityMenu: () => null }));

import { LenderMark } from './LenderMark';
import { Topbar } from './Topbar';

const MARK_URL = '/branding/lender-mark.png?v=0a1b2c3d';
const LENDER = 'Fixture Test Lending';
const BUILDING = 'svg path[d^="M4 21V5l8-3"]';

function setMetas(metas: Record<string, string>): void {
  document.head.innerHTML = Object.entries(metas).map(([name, content]) => `<meta name="${name}" content="${content}">`).join('');
}

function client(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe('LenderMark', () => {
  beforeEach(() => {
    session.fn.mockReset();
    setMetas({ 'mip-lender-mark': MARK_URL, 'mip-lender-mark-lender': LENDER });
  });
  afterEach(() => {
    document.head.innerHTML = '';
  });

  it('draws one decorative mark when the session lender is the build lender', async () => {
    session.fn.mockResolvedValue({ lender_name: ` ${LENDER} ` });
    const { container } = await mount(<QueryClientProvider client={client()}><LenderMark iconSize={12} /></QueryClientProvider>);
    await settle();
    const images = container.querySelectorAll('img');
    expect(images).toHaveLength(1);
    expect(images[0].className).toBe('lender-mark');
    expect(images[0].getAttribute('alt')).toBe('');
    expect(images[0].getAttribute('src')).toBe(MARK_URL);
    expect([images[0].getAttribute('width'), images[0].getAttribute('height')]).toEqual(['16', '16']);
    expect(container.querySelector(BUILDING)).toBeNull();
  });

  it('keeps the building glyph while loading, on a mismatch, on an error and after a failed image load', async () => {
    session.fn.mockReturnValue(new Promise(() => undefined));
    const loading = await mount(<QueryClientProvider client={client()}><LenderMark iconSize={12} /></QueryClientProvider>);
    expect(loading.container.querySelector('img')).toBeNull();
    expect(loading.container.querySelector(BUILDING)).not.toBeNull();

    session.fn.mockResolvedValue({ lender_name: 'Summit Mortgage' });
    const mismatch = await mount(<QueryClientProvider client={client()}><LenderMark iconSize={12} /></QueryClientProvider>);
    await settle();
    expect(mismatch.container.querySelector('img')).toBeNull();
    expect(mismatch.container.querySelector(BUILDING)).not.toBeNull();

    session.fn.mockRejectedValue(new Error('session down'));
    const failed = await mount(<QueryClientProvider client={client()}><LenderMark iconSize={12} /></QueryClientProvider>);
    await settle();
    expect(failed.container.querySelector('img')).toBeNull();
    expect(failed.container.querySelector(BUILDING)).not.toBeNull();

    session.fn.mockResolvedValue({ lender_name: LENDER });
    const broken = await mount(<QueryClientProvider client={client()}><LenderMark iconSize={12} /></QueryClientProvider>);
    await settle();
    const image = broken.container.querySelector('img');
    expect(image).not.toBeNull();
    act(() => {
      image?.dispatchEvent(new Event('error'));
    });
    expect(broken.container.querySelector('img')).toBeNull();
    expect(broken.container.querySelector(BUILDING)).not.toBeNull();
  });

  it('without the build meta draws the building glyph and never reads the session', async () => {
    document.head.innerHTML = '';
    // No QueryClientProvider either: the default build needs none.
    const { container } = await mount(<LenderMark iconSize={10} />);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector(BUILDING)?.closest('svg')?.getAttribute('width')).toBe('10');
    expect(session.fn).not.toHaveBeenCalled();
    // A mark URL without its lender is no mark either.
    setMetas({ 'mip-lender-mark': MARK_URL });
    const { container: unbound } = await mount(<LenderMark iconSize={10} />);
    expect(unbound.querySelector('img')).toBeNull();
    expect(session.fn).not.toHaveBeenCalled();
  });

  it('keeps the tenant pill aria-label and tooltip copy in the Topbar', async () => {
    session.fn.mockResolvedValue({ lender_name: LENDER });
    const { container } = await mount(
      <QueryClientProvider client={client()}><MemoryRouter><Topbar /></MemoryRouter></QueryClientProvider>,
    );
    await settle();
    const pill = container.querySelector<HTMLElement>('.topbar__pill[aria-label^="Configured tenant"]');
    expect(pill?.getAttribute('aria-label')).toBe(`Configured tenant: ${LENDER}`);
    expect(pill?.querySelector('img.lender-mark')).not.toBeNull();
    expect(pill?.querySelector('.topbar__pill-tenant')?.textContent).toBe(LENDER);
    const described = (pill?.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)
      .map((id) => document.getElementById(id)?.textContent).join(' | ');
    expect(described).toBe(`Configured tenant · ${LENDER}. Lender configuration is applied server-side.`);
  });

  it('is the only module that reads the lender mark URL (AppContext never exposes it)', () => {
    const src = join(process.cwd(), 'src');
    const readers: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) && /\blenderMarkUrl\b/.test(readFileSync(full, 'utf8'))) {
          readers.push(full.slice(src.length + 1));
        }
      }
    };
    walk(src);
    expect(readers.sort()).toEqual(['components/layout/LenderMark.tsx', 'lib/themePreference.ts']);
  });
});
