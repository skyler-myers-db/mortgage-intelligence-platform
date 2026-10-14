// @vitest-environment happy-dom

import { act, useRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '../test/render';
import { useRouteNavDock } from './useRouteNavDock';

/**
 * useRouteNavDock (report 12.4 #5): the nav docks while its measured border
 * box is at most a sixth of `.main`'s content box. happy-dom has no layout,
 * so the two sizes are stubbed and the ResizeObserver is a fake whose
 * entries the test delivers; what is asserted is the DOM the hook writes
 * (`data-docked` on the nav, `--route-nav-block` on `.main`).
 */

interface Observed {
  target: Element;
  box: ResizeObserverBoxOptions | undefined;
}

class FakeResizeObserver {
  static instances: FakeResizeObserver[] = [];
  observed: Observed[] = [];
  disconnected = false;

  constructor(private readonly callback: ResizeObserverCallback) {
    FakeResizeObserver.instances.push(this);
  }

  observe(target: Element, options?: ResizeObserverOptions): void {
    this.observed.push({ target, box: options?.box });
  }

  unobserve(): void {}

  disconnect(): void {
    this.disconnected = true;
  }

  /** Deliver one batch: each entry names its target and its block sizes. */
  deliver(entries: Array<{ target: Element; contentBlock?: number; borderBlock?: number }>): void {
    const shaped = entries.map(({ target, contentBlock = 0, borderBlock = 0 }) => ({
      target,
      contentRect: { height: contentBlock },
      contentBoxSize: [{ blockSize: contentBlock, inlineSize: 0 }],
      borderBoxSize: [{ blockSize: borderBlock, inlineSize: 0 }],
      devicePixelContentBoxSize: [],
    }));
    act(() => this.callback(shaped as unknown as ResizeObserverEntry[], this as unknown as ResizeObserver));
  }
}

let navHeight = 57;
let mainClientHeight = 564;

function Harness({ inMain = true }: { inMain?: boolean }) {
  const ref = useRef<HTMLElement>(null);
  useRouteNavDock(ref);
  const nav = <nav ref={ref} className="route-nav" />;
  return inMain ? <main className="main">{nav}</main> : nav;
}

const nav = () => document.querySelector<HTMLElement>('.route-nav') as HTMLElement;
const main = () => document.querySelector<HTMLElement>('.main') as HTMLElement;
const observer = () => FakeResizeObserver.instances[FakeResizeObserver.instances.length - 1];

describe('useRouteNavDock', () => {
  beforeEach(() => {
    navHeight = 57;
    mainClientHeight = 564;
    FakeResizeObserver.instances = [];
    vi.stubGlobal('ResizeObserver', FakeResizeObserver);
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function height(this: HTMLElement) {
      return this.classList.contains('route-nav') ? navHeight : 0;
    });
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function height(this: HTMLElement) {
      return this.classList.contains('main') ? mainClientHeight : 0;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('docks a nav that fits a sixth of the scrollport and writes its block on .main', async () => {
    await mount(<Harness />);
    expect(nav().hasAttribute('data-docked')).toBe(true);
    expect(main().style.getPropertyValue('--route-nav-block')).toBe('57px');
  });

  it('measures the nav border box and .main content box, and undocks a nav that wraps past a sixth', async () => {
    await mount(<Harness />);
    expect(observer().observed).toEqual([
      { target: nav(), box: 'border-box' },
      { target: main(), box: 'content-box' },
    ]);
    observer().deliver([{ target: nav(), borderBlock: 97 }]);
    expect(nav().hasAttribute('data-docked')).toBe(false);
    expect(main().style.getPropertyValue('--route-nav-block')).toBe('97px');
    observer().deliver([{ target: main(), contentBlock: 604 }]);
    expect(nav().hasAttribute('data-docked')).toBe(true);
  });

  it('reads the content box of .main: a content-box shrink below six navs undocks however tall its border box is', async () => {
    await mount(<Harness />);
    expect(nav().hasAttribute('data-docked')).toBe(true);
    // The Console bottom sheet pads .main: the border box keeps 712px, the content box is 300px.
    observer().deliver([{ target: main(), contentBlock: 300, borderBlock: 712 }]);
    expect(nav().hasAttribute('data-docked')).toBe(false);
  });

  it('writes nothing when a re-measure finds the same values', async () => {
    await mount(<Harness />);
    const setProperty = vi.spyOn(main().style, 'setProperty');
    const toggle = vi.spyOn(nav(), 'toggleAttribute');
    observer().deliver([
      { target: nav(), borderBlock: 57 },
      { target: main(), contentBlock: 564 },
    ]);
    // A scrollport change that leaves the docked state alone writes nothing either.
    observer().deliver([{ target: main(), contentBlock: 700 }]);
    expect(setProperty).not.toHaveBeenCalled();
    expect(toggle).not.toHaveBeenCalled();
    observer().deliver([{ target: nav(), borderBlock: 58 }]);
    expect(setProperty).toHaveBeenCalledTimes(1);
    expect(toggle).not.toHaveBeenCalled();
  });

  it('leaves a nav outside .main alone: no observer, no attribute, no property', async () => {
    await mount(<Harness inMain={false} />);
    expect(FakeResizeObserver.instances).toEqual([]);
    expect(nav().hasAttribute('data-docked')).toBe(false);
    expect(document.querySelector('[style]')).toBeNull();
  });

  it('cleans up on unmount: disconnects, removes the attribute and the property', async () => {
    const view = await mount(<Harness />);
    const navElement = nav();
    const mainElement = main();
    view.unmount();
    expect(observer().disconnected).toBe(true);
    expect(navElement.hasAttribute('data-docked')).toBe(false);
    expect(mainElement.style.getPropertyValue('--route-nav-block')).toBe('');
  });

  it('re-measures on a window resize where there is no ResizeObserver', async () => {
    vi.stubGlobal('ResizeObserver', undefined);
    await mount(<Harness />);
    expect(nav().hasAttribute('data-docked')).toBe(true);
    mainClientHeight = 300;
    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    expect(nav().hasAttribute('data-docked')).toBe(false);
  });
});
