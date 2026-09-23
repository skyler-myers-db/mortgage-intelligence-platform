/**
 * @vitest-environment happy-dom
 *
 * `openGenie({ prompt })` reaches the shell through the dock (audit
 * 2026-09-21 `genie-04`): the request opens the panel and the prefill waits
 * for the chat to consume it. Nothing is submitted.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { consumeGeniePrefill, openGenie } from '../../lib/genieOpen';
import { GenieDock } from './GenieDock';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function ChatProbe() {
  return <div data-testid="genie-chat-probe" />;
}

describe('GenieDock open requests', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onOpen = vi.fn();
  const onWarm = vi.fn();

  beforeEach(() => {
    onOpen.mockReset();
    onWarm.mockReset();
    consumeGeniePrefill();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    consumeGeniePrefill();
  });

  it('opens the panel on openGenie and leaves the prefill queued for the chat', () => {
    act(() => root.render(<GenieDock open={false} onOpen={onOpen} onWarm={onWarm} Chat={ChatProbe} />));
    act(() => openGenie({ prompt: 'Compare mean lead score by current coverage state.' }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(consumeGeniePrefill()).toBe('Compare mean lead score by current coverage state.');
  });

  it('stops listening once unmounted', () => {
    act(() => root.render(<GenieDock open={false} onOpen={onOpen} onWarm={onWarm} Chat={ChatProbe} />));
    act(() => root.unmount());
    root = createRoot(container);
    act(() => openGenie({ prompt: 'anything' }));
    expect(onOpen).not.toHaveBeenCalled();
  });
});
