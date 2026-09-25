/**
 * @vitest-environment happy-dom
 *
 * The palette's shell host (audit 2026-09-21 bundle-04): the dialog is a
 * lazy chunk. The host requests it on the first Meta / Control keydown and
 * on ⌘K, and when the chunk cannot load (a stale deploy) the palette stays
 * closed: the failure never throws into the shell (AppShell has no boundary
 * around the palette). Here every import of the dialog fails.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const chunk = vi.hoisted(() => ({ requests: 0 }));

vi.mock('react-router', () => ({ useNavigate: () => vi.fn() }));
vi.mock('./CommandPaletteDialog', () => {
  chunk.requests += 1;
  throw new Error('Failed to fetch dynamically imported module: CommandPaletteDialog-stale.js');
});

import { CommandPalette } from './CommandPalette';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function flush(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe('CommandPalette host', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<CommandPalette />));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('requests the dialog chunk on the first Meta keydown, once', async () => {
    const before = chunk.requests;
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Meta', bubbles: true }));
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Meta', bubbles: true }));
    });
    await flush();
    expect(chunk.requests).toBeGreaterThan(before);
    const afterMeta = chunk.requests;
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Control', bubbles: true }));
    });
    await flush();
    expect(chunk.requests, 'the listener removed itself').toBe(afterMeta);
  });

  it('leaves the palette closed when its chunk cannot load, without an unhandled rejection', async () => {
    const pressMetaK = () => act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true, cancelable: true }));
    });
    pressMetaK();
    await flush();
    expect(chunk.requests).toBeGreaterThan(0);
    expect(container.querySelector('dialog')).toBeNull();
    // A second ⌘K neither throws nor renders a half-open palette.
    pressMetaK();
    await flush();
    expect(container.querySelector('dialog')).toBeNull();
  });
});
