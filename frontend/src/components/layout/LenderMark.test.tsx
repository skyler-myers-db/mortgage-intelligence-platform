/**
 * @vitest-environment happy-dom
 *
 * LenderMark (audit responsive-10, 12.4 #9; deviation:lender-mark): the
 * reviewed lender mark shows only on a co-branded build whose session lender
 * equals the lender the build validated it for. Everything else is the
 * prototype's building glyph.
 */
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

const app = vi.hoisted(() => ({ lender: 'Fixture Test Lending', sessionStatus: 'ready' as 'loading' | 'ready' | 'error' }));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { borrowerSearch: vi.fn().mockResolvedValue([]) },
}));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    lender: app.lender,
    sessionStatus: app.sessionStatus,
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

describe('LenderMark', () => {
  beforeEach(() => {
    app.lender = LENDER;
    app.sessionStatus = 'ready';
    setMetas({ 'mip-lender-mark': MARK_URL, 'mip-lender-mark-lender': LENDER });
  });
  afterEach(() => {
    document.head.innerHTML = '';
  });

  it('draws one decorative mark when the session lender is the build lender', async () => {
    const { container } = await mount(<LenderMark iconSize={12} sessionLender={` ${LENDER} `} />);
    const images = container.querySelectorAll('img');
    expect(images).toHaveLength(1);
    expect(images[0].className).toBe('lender-mark');
    expect(images[0].getAttribute('alt')).toBe('');
    expect(images[0].getAttribute('src')).toBe(MARK_URL);
    expect([images[0].getAttribute('width'), images[0].getAttribute('height')]).toEqual(['16', '16']);
    expect(container.querySelector(BUILDING)).toBeNull();
  });

  it('keeps the building glyph while the session is unknown, on a mismatch and after a failed image load', async () => {
    for (const sessionLender of [null, '', '   ', 'Summit Mortgage', `${LENDER} Two`]) {
      const { container } = await mount(<LenderMark iconSize={12} sessionLender={sessionLender} />);
      expect(container.querySelector('img'), String(sessionLender)).toBeNull();
      expect(container.querySelector(BUILDING), String(sessionLender)).not.toBeNull();
    }
    const broken = await mount(<LenderMark iconSize={12} sessionLender={LENDER} />);
    const image = broken.container.querySelector('img');
    expect(image).not.toBeNull();
    act(() => {
      image?.dispatchEvent(new Event('error'));
    });
    expect(broken.container.querySelector('img')).toBeNull();
    expect(broken.container.querySelector(BUILDING)).not.toBeNull();
  });

  it('without the build meta (every default build) draws the building glyph', async () => {
    document.head.innerHTML = '';
    const { container } = await mount(<LenderMark iconSize={10} sessionLender={LENDER} />);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector(BUILDING)?.closest('svg')?.getAttribute('width')).toBe('10');
    // A mark URL without its lender is no mark either.
    setMetas({ 'mip-lender-mark': MARK_URL });
    const { container: unbound } = await mount(<LenderMark iconSize={10} sessionLender={LENDER} />);
    expect(unbound.querySelector('img')).toBeNull();
  });

  it('the Topbar pill draws the mark once the session lender matches, keeping its aria-label and tooltip copy', async () => {
    const { container, rerender } = await mount(<MemoryRouter><Topbar /></MemoryRouter>);
    // The Topbar loads the mark lazily, only on a co-branded build.
    await act(async () => {
      await import('./LenderMark');
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const pill = container.querySelector<HTMLElement>('.topbar__pill[aria-label^="Configured tenant"]');
    expect(pill?.getAttribute('aria-label')).toBe(`Configured tenant: ${LENDER}`);
    expect(pill?.querySelector('img.lender-mark')).not.toBeNull();
    expect(pill?.querySelector('.topbar__pill-tenant')?.textContent).toBe(LENDER);
    const described = (pill?.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)
      .map((id) => document.getElementById(id)?.textContent).join(' | ');
    expect(described).toBe(`Configured tenant · ${LENDER}. Lender configuration is applied server-side.`);

    // Before /api/session answers, AppContext's label is not the session's: no mark.
    app.sessionStatus = 'loading';
    await rerender(<MemoryRouter><Topbar /></MemoryRouter>);
    expect(container.querySelector('.topbar__pill img')).toBeNull();
  });

  it('is the only component that reads the lender mark URL (AppContext never exposes it)', () => {
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
    // The Topbar and the Console only ask whether a mark exists, to load LenderMark at all.
    expect(readers.sort()).toEqual(['components/layout/Console.tsx', 'components/layout/LenderMark.tsx', 'components/layout/Topbar.tsx', 'lib/themePreference.ts']);
    expect(readFileSync(join(src, 'components', 'AppContext.tsx'), 'utf8')).not.toMatch(/lenderMark/);
  });
});
