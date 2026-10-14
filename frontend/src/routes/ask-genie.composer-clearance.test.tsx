// @vitest-environment happy-dom

import { useRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '../test/render';
import { COMPOSER_BLOCK_SIZE_PROPERTY, useComposerScrollClearance } from './ask-genie.composer-clearance';

/**
 * The Ask tab's composer clearance (visual-07) measures the composer only:
 * the route nav's block is the shell's (useRouteNavDock writes
 * --route-nav-block on `.main`, report 12.4 #5), so this hook must write
 * nothing for the nav, even with one rendered in `.main`.
 */
function Harness() {
  const dockRef = useRef<HTMLFormElement>(null);
  useComposerScrollClearance(dockRef);
  return (
    <main className="main">
      <nav className="route-nav" />
      <form ref={dockRef} className="genie-composer" />
    </main>
  );
}

describe('useComposerScrollClearance', () => {
  beforeEach(() => {
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function height(this: HTMLElement) {
      if (this.classList.contains('genie-composer')) return 132;
      return this.classList.contains('route-nav') ? 57 : 0;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('writes the composer block on .main and nothing for the route nav', async () => {
    const view = await mount(<Harness />);
    const main = document.querySelector<HTMLElement>('.main') as HTMLElement;
    const written = Array.from({ length: main.style.length }, (_, index) => main.style.item(index)).filter((name) => name.startsWith('--'));
    expect(written).toEqual([COMPOSER_BLOCK_SIZE_PROPERTY]);
    expect(main.style.getPropertyValue(COMPOSER_BLOCK_SIZE_PROPERTY)).toBe('132px');
    view.unmount();
    expect(main.style.getPropertyValue(COMPOSER_BLOCK_SIZE_PROPERTY)).toBe('');
  });
});
