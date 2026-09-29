/**
 * @vitest-environment happy-dom
 *
 * Dragging and resizing the floating Genie panel (audit 2026-09-21
 * runtime-v1, responsive-v1 item 2), on the rendered panel under the
 * production React Compiler preset (vite.config's babel preset runs in
 * vitest too). A React Profiler around <GenieChat/> counts commits:
 *   - pointermoves commit NOTHING: each move stores its target and schedules
 *     at most one animation frame, and each frame writes the panel once;
 *   - pointerup commits exactly once, with one localStorage write each for
 *     the size and the position;
 *   - with the Console open, a drag into the right zone stops clear of the
 *     Console's box, and the drag starts from the panel's rendered box.
 */
import { Profiler, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { createMipQueryClient } from '../../lib/queryClient';
import { installLocalStorage } from '../../test/installLocalStorage';

const mocks = vi.hoisted(() => ({
  genieStart: vi.fn(),
  genieSessions: vi.fn(),
  setGenieOpen: vi.fn(),
}));
const appState = vi.hoisted(() => ({ consoleOpen: false }));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { genieStart: mocks.genieStart, genieSessions: mocks.genieSessions },
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({
    genieOpen: true,
    setGenieOpen: mocks.setGenieOpen,
    lender: 'Test Lender',
    refreshWorkspace: vi.fn(),
    setDrawer: vi.fn(),
    consoleOpen: appState.consoleOpen,
  }),
}));

import { GenieChat } from './GenieChat';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SIZE_KEY = 'mip-genie-chat-size-v1';
const POSITION_KEY = 'mip-genie-chat-pos-v1';
const VIEWPORT = { w: 1440, h: 900 };
const PANEL = { w: 420, h: 640 };
/** The docked panel's box at 1440 x 900 with the Console closed. */
const DOCKED_BOX = { left: VIEWPORT.w - 16 - PANEL.w, top: VIEWPORT.h - 16 - PANEL.h };
/** The Console card: fixed at right 16, --console-w 300 (09-console-and-layout.css). */
const CONSOLE_LEFT = VIEWPORT.w - 16 - 300;

function testQueryClient(): QueryClient {
  const client = createMipQueryClient();
  client.setDefaultOptions({ queries: { ...client.getDefaultOptions().queries, retry: false } });
  return client;
}

/** A frame queue: requestAnimationFrame callbacks run only when a test says so. */
const frames = { queue: [] as FrameRequestCallback[], requested: 0 };

function runFrame(): void {
  const due = frames.queue.splice(0);
  act(() => {
    for (const callback of due) callback(performance.now());
  });
}

function pointer(type: string, target: Element, x: number, y: number): void {
  const Ctor = typeof PointerEvent === 'function' ? PointerEvent : MouseEvent;
  target.dispatchEvent(new Ctor(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, pointerId: 1 } as PointerEventInit));
}

function setViewport(width: number, height: number): void {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
}

describe('dragging and resizing the Genie panel', () => {
  let container: HTMLDivElement;
  let root: Root;
  let commits: number;
  let setItem: MockInstance<(key: string, value: string) => void>;

  beforeEach(() => {
    installLocalStorage();
    setItem = vi.spyOn(window.localStorage, 'setItem');
    mocks.genieStart.mockResolvedValue({ conversation_id: null, trusted_assets: [], sample_questions: [] });
    mocks.genieSessions.mockResolvedValue([]);
    appState.consoleOpen = false;
    setViewport(VIEWPORT.w, VIEWPORT.h);
    frames.queue = [];
    frames.requested = 0;
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      frames.requested += 1;
      frames.queue.push(callback);
      return frames.requested;
    });
    vi.stubGlobal('cancelAnimationFrame', () => {
      frames.queue = [];
    });
    for (const name of ['setPointerCapture', 'releasePointerCapture'] as const) {
      Object.defineProperty(Element.prototype, name, { configurable: true, writable: true, value: () => undefined });
    }
    Object.defineProperty(Element.prototype, 'hasPointerCapture', { configurable: true, writable: true, value: () => true });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    commits = 0;
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    setItem.mockRestore();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  async function mount(): Promise<HTMLElement> {
    const client = testQueryClient();
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <Profiler id="genie" onRender={() => { commits += 1; }}>
              <GenieChat />
            </Profiler>
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    // Let the mount settle (the shared /api/genie/start query lands in a
    // later task): a gesture's commits are counted from a quiet panel.
    let settled = -1;
    while (settled !== commits) {
      settled = commits;
      await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
    }
    const panel = container.querySelector<HTMLElement>('.genie[role="dialog"]');
    if (!panel) throw new Error('panel not rendered');
    return panel;
  }

  function placePanel(panel: HTMLElement, left: number, top: number): void {
    panel.getBoundingClientRect = () => ({
      left, top, right: left + PANEL.w, bottom: top + PANEL.h, width: PANEL.w, height: PANEL.h, x: left, y: top, toJSON: () => ({}),
    });
  }

  function translateOf(panel: HTMLElement): { dx: number; dy: number } {
    const value = panel.style.getPropertyValue('translate');
    const [dx = '0', dy = '0'] = value.split(/\s+/);
    return { dx: Number.parseFloat(dx) || 0, dy: Number.parseFloat(dy) || 0 };
  }

  it('60 pointermoves commit nothing and write once per frame; pointerup commits once and saves once each', async () => {
    const panel = await mount();
    placePanel(panel, DOCKED_BOX.left, DOCKED_BOX.top);
    const header = panel.querySelector<HTMLElement>('.genie__hdr');
    if (!header) throw new Error('header not rendered');
    const writes = vi.spyOn(panel.style, 'setProperty');
    setItem.mockClear();
    commits = 0;

    act(() => pointer('pointerdown', header, 1200, 300));
    // 60 moves, three per frame: 20 frames, one write each, no commit.
    for (let frame = 0; frame < 20; frame += 1) {
      for (let step = 1; step <= 3; step += 1) {
        const move = frame * 3 + step;
        act(() => pointer('pointermove', header, 1200 - move * 5, 300 - move * 2));
      }
      runFrame();
    }
    expect(commits, 'no React commit while the pointer moves').toBe(0);
    expect(frames.requested, 'at most one frame per batch of moves').toBe(20);
    expect(writes.mock.calls.filter(([name]) => name === 'translate')).toHaveLength(20);
    expect(translateOf(panel)).toEqual({ dx: -300, dy: -120 });
    expect(setItem).not.toHaveBeenCalled();

    act(() => pointer('pointerup', header, 900, 180));
    expect(commits, 'one commit for the whole gesture').toBe(1);
    expect(setItem.mock.calls.filter(([key]) => key === SIZE_KEY)).toHaveLength(1);
    expect(setItem.mock.calls.filter(([key]) => key === POSITION_KEY)).toHaveLength(1);
    expect(JSON.parse(window.localStorage.getItem(POSITION_KEY) ?? '{}')).toEqual({
      pos: { x: DOCKED_BOX.left - 300, y: DOCKED_BOX.top - 120 },
    });
    // The committed left / top replace the offset in the same commit.
    expect(panel.style.left).toBe(`${DOCKED_BOX.left - 300}px`);
    expect(panel.style.getPropertyValue('translate')).toBe('');
    expect(panel.classList.contains('is-undocked')).toBe(true);
  });

  it('a resize writes the size per frame and commits size and position once, on pointerup', async () => {
    window.localStorage.setItem(POSITION_KEY, JSON.stringify({ pos: { x: 400, y: 150 } }));
    const panel = await mount();
    const edge = panel.querySelector<HTMLElement>('.genie__resize-edge--w');
    if (!edge) throw new Error('resize edge not rendered');
    setItem.mockClear();
    commits = 0;

    act(() => pointer('pointerdown', edge, 400, 400));
    for (let move = 1; move <= 30; move += 1) {
      act(() => pointer('pointermove', edge, 400 - move * 2, 400));
      if (move % 3 === 0) runFrame();
    }
    expect(commits).toBe(0);
    expect(frames.requested).toBe(10);
    expect(panel.style.width).toBe(`${PANEL.w + 60}px`);
    expect(translateOf(panel)).toEqual({ dx: -60, dy: 0 });

    act(() => pointer('pointerup', edge, 340, 400));
    expect(commits).toBe(1);
    expect(setItem.mock.calls.filter(([key]) => key === SIZE_KEY)).toHaveLength(1);
    expect(setItem.mock.calls.filter(([key]) => key === POSITION_KEY)).toHaveLength(1);
    expect(JSON.parse(window.localStorage.getItem(SIZE_KEY) ?? '{}')).toEqual({ w: PANEL.w + 60, h: PANEL.h });
    expect(panel.style.left).toBe('340px');
    expect(panel.style.getPropertyValue('translate')).toBe('');
  });

  it('with the Console open, a drag into the right zone stops clear of the Console (1440 x 900)', async () => {
    appState.consoleOpen = true;
    const panel = await mount();
    // The docked panel sits left of the open Console (CSS); the drag starts
    // from that rendered box, so undocking does not jump.
    const dockedLeft = CONSOLE_LEFT - 16 - PANEL.w;
    placePanel(panel, dockedLeft, DOCKED_BOX.top);
    const header = panel.querySelector<HTMLElement>('.genie__hdr');
    if (!header) throw new Error('header not rendered');

    act(() => pointer('pointerdown', header, 1000, 400));
    act(() => pointer('pointermove', header, 995, 300));
    runFrame();
    expect(translateOf(panel)).toEqual({ dx: -5, dy: -100 });
    act(() => pointer('pointermove', header, 1400, 300));
    runFrame();
    // Clamped at the Console's left edge less the gutter: never over its box.
    const { dx } = translateOf(panel);
    expect(dockedLeft + dx + PANEL.w).toBeLessThanOrEqual(CONSOLE_LEFT - 16);

    act(() => pointer('pointerup', header, 1400, 300));
    const saved = JSON.parse(window.localStorage.getItem(POSITION_KEY) ?? '{}') as { pos: { x: number } };
    expect(saved.pos.x + PANEL.w).toBeLessThanOrEqual(CONSOLE_LEFT - 16);
    expect(Number.parseFloat(panel.style.left) + PANEL.w).toBeLessThanOrEqual(CONSOLE_LEFT);
  });

  it('opening the Console re-clamps an undocked panel at the far right', async () => {
    window.localStorage.setItem(POSITION_KEY, JSON.stringify({ pos: { x: VIEWPORT.w - 16 - PANEL.w, y: 100 } }));
    const panel = await mount();
    expect(panel.style.left).toBe(`${VIEWPORT.w - 16 - PANEL.w}px`);

    appState.consoleOpen = true;
    await mount();
    expect(Number.parseFloat(panel.style.left) + PANEL.w).toBeLessThanOrEqual(CONSOLE_LEFT - 16);
    expect(panel.style.top).toBe('100px');
  });
});
