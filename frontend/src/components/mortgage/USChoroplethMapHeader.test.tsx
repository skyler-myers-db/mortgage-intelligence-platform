/**
 * @vitest-environment happy-dom
 */
/**
 * The map header's colouring toggle (audit wow-stage-1): three aria-pressed
 * buttons in the "Map coloring" group; "Rate scenario" is aria-disabled with
 * its reason under a cohort filter, a click on it then does nothing, and only
 * an available button warms the lazy control chunk.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { USChoroplethMapHeader, type MapColorMode } from './USChoroplethMapHeader';

const lazyMocks = vi.hoisted(() => ({ load: vi.fn(), fail: false }));
vi.mock('./rateScenario.lazy', () => ({
  RATE_SCENARIO_CONTROL: {
    // Counted through the spy but answered by a plain promise: vitest attaches
    // handlers to a vi.fn's returned promise (settledResults), so a rejection
    // it returned could never surface as unhandled.
    load: () => {
      lazyMocks.load();
      return lazyMocks.fail ? Promise.reject(new Error('retired chunk (test)')) : Promise.resolve({});
    },
    current: () => null,
  },
}));
vi.mock('./GenieAskAbout', () => ({ GenieAskAbout: () => null }));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// The frontend tsconfig carries no Node typings; the test runner's process
// reaches the rejection hook structurally (as in routePreloaders.home.test).
interface RejectionEmitter {
  on(event: 'unhandledRejection', listener: (reason: unknown) => void): unknown;
  off(event: 'unhandledRejection', listener: (reason: unknown) => void): unknown;
}
const nodeProcess = (globalThis as unknown as { process: RejectionEmitter }).process;

const REASON = 'Rate scenarios cover the whole book; clear segment and portfolio filters to use them.';

describe('USChoroplethMapHeader colouring toggle', () => {
  let root: Root;
  let setMode: ReturnType<typeof vi.fn<(mode: MapColorMode) => void>>;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    setMode = vi.fn<(mode: MapColorMode) => void>();
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  function renderHeader(mode: MapColorMode, rateAvailable: boolean) {
    act(() =>
      root.render(
        <USChoroplethMapHeader
          drilled={false}
          drillStateUC=""
          drillStateName=""
          onBackToUs={() => undefined}
          coverageZipCount={412}
          drillHint
          zipUnassigned={0}
          mode={mode}
          setMode={setMode}
          rateAvailable={rateAvailable}
          view="map"
          setView={() => undefined}
          campaignPrefillPath={null}
          onStartCampaign={() => undefined}
        />,
      ),
    );
    const group = document.querySelector('[role="group"][aria-label="Map coloring"]');
    const buttons = [...(group?.querySelectorAll('button') ?? [])];
    return { buttons, rate: buttons.find((button) => button.textContent === 'Rate scenario') };
  }

  it('renders three aria-pressed buttons with one pressed', () => {
    const { buttons } = renderHeader('rate', true);
    expect(buttons.map((button) => button.textContent)).toEqual(['Borrowers', 'Unattended leads', 'Rate scenario']);
    expect(buttons.map((button) => button.getAttribute('aria-pressed'))).toEqual(['false', 'false', 'true']);
    act(() => buttons[1].dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(setMode).toHaveBeenCalledWith('unattended');
  });

  it('picks the rate colouring and warms its chunk on pointer-enter or focus when available', () => {
    const { rate } = renderHeader('borrowers', true);
    expect(rate?.getAttribute('aria-disabled')).toBeNull();
    expect(rate?.getAttribute('aria-describedby')).toBeNull();
    act(() => rate?.dispatchEvent(new PointerEvent('pointerover', { bubbles: true })));
    act(() => rate?.focus());
    expect(lazyMocks.load).toHaveBeenCalled();
    act(() => rate?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(setMode).toHaveBeenCalledWith('rate');
  });

  it('swallows a failed warm: a retired or unreachable chunk is no unhandled rejection', async () => {
    const unhandled = vi.fn();
    nodeProcess.on('unhandledRejection', unhandled);
    try {
      lazyMocks.fail = true;
      const { rate } = renderHeader('borrowers', true);
      act(() => rate?.dispatchEvent(new PointerEvent('pointerover', { bubbles: true })));
      act(() => rate?.focus());
      expect(lazyMocks.load).toHaveBeenCalledTimes(2);
      // Node reports an unhandled rejection after the microtask queue drains.
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(unhandled).not.toHaveBeenCalled();
    } finally {
      nodeProcess.off('unhandledRejection', unhandled);
      lazyMocks.fail = false;
    }
  });

  it('is aria-disabled with its reason under a cohort filter, and a click does nothing', () => {
    const { rate, buttons } = renderHeader('borrowers', false);
    expect(rate?.getAttribute('aria-disabled')).toBe('true');
    expect(rate?.getAttribute('aria-pressed')).toBe('false');
    expect(rate?.hasAttribute('disabled')).toBe(false); // stays focusable
    expect(rate?.getAttribute('title')).toBe(REASON);
    const describedBy = rate?.getAttribute('aria-describedby') ?? '';
    expect(document.getElementById(describedBy)?.textContent).toBe(REASON);
    act(() => rate?.focus());
    act(() => rate?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(setMode).not.toHaveBeenCalled();
    expect(lazyMocks.load).not.toHaveBeenCalled();
    expect(buttons[0].getAttribute('aria-pressed')).toBe('true');
  });
});
