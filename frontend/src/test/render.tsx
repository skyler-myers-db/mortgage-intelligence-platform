/**
 * mount(): the shared render helper for happy-dom component tests (audit
 * quality-06, the S slice). It replaces the createRoot / act / unmount
 * boilerplate each suite used to hand-roll:
 *
 *   const { container, rerender } = await mount(<Topbar />);
 *   await rerender(<Topbar />);
 *
 * - Rendering and re-rendering run inside `await act(async () => ...)`, so
 *   effects and the microtasks they queue have flushed when it resolves.
 * - Every mounted root is tracked; a module-level afterEach unmounts the ones
 *   a test left mounted and removes the containers mount() created, so the
 *   next test starts with an empty body. A container the caller passed in is
 *   unmounted but left in place: the caller owns it.
 * - The act environment flag is set once for the whole suite in setup.ts.
 *
 * Test-only: production code may not import src/test (eslint.config.js).
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach } from 'vitest';

export interface Mounted {
  container: HTMLElement;
  root: Root;
  rerender(ui: ReactNode): Promise<void>;
  unmount(): void;
}

export interface MountOptions {
  /** Render into this element instead of a fresh `<div>` appended to body. */
  container?: HTMLElement;
}

interface TrackedRoot {
  root: Root;
  container: HTMLElement;
  ownsContainer: boolean;
}

const mounted = new Set<TrackedRoot>();

function teardown(entry: TrackedRoot): void {
  if (!mounted.delete(entry)) return;
  act(() => entry.root.unmount());
  if (entry.ownsContainer) entry.container.remove();
}

afterEach(() => {
  for (const entry of [...mounted]) teardown(entry);
});

export async function mount(ui: ReactNode, options: MountOptions = {}): Promise<Mounted> {
  if (typeof document === 'undefined') {
    throw new Error(
      'mount() needs a DOM: this test runs in the node environment. Add `@vitest-environment happy-dom` to the file header.',
    );
  }
  const ownsContainer = options.container === undefined;
  const container = options.container ?? document.body.appendChild(document.createElement('div'));
  const root = createRoot(container);
  const entry: TrackedRoot = { root, container, ownsContainer };
  mounted.add(entry);
  await act(async () => {
    root.render(ui);
  });
  return {
    container,
    root,
    async rerender(next: ReactNode): Promise<void> {
      await act(async () => {
        root.render(next);
      });
    },
    unmount(): void {
      teardown(entry);
    },
  };
}
