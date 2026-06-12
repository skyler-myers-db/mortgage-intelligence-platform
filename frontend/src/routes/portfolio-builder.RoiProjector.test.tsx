/**
 * @vitest-environment happy-dom
 *
 * Campaign ROI projector render contract (re-audit #4 Buyer-Wow #7).
 * The projector is a pure, deterministic, network-free strip: it must
 * render a dollar headline from the build's lead count and recompute
 * instantly when an assumption is edited — no NaN, no stale headline.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { RoiProjector } from './portfolio-builder.components';

// The projector pulls theme/etc. from nothing — it is self-contained — so
// no providers are needed.

describe('RoiProjector', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount(leads: number) {
    act(() => root.render(<RoiProjector leads={leads} />));
  }
  const gross = () =>
    container.querySelector('[data-testid="roi-gross"]')?.textContent ?? '';

  function setInput(testid: string, value: string) {
    const input = container.querySelector<HTMLInputElement>(`[data-testid="${testid}"]`)!;
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      'value',
    )!.set!;
    act(() => {
      setter.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  it('renders a dollar headline from the lead count with default assumptions', () => {
    mount(1200);
    // 1,200 × 4% × $340k × 1.5% = $244,800 → "$245K".
    expect(gross()).toBe('$245K');
    expect(container.textContent).toContain('1,200 high-intent leads');
  });

  it('recomputes the headline live when an assumption changes', () => {
    mount(1200);
    setInput('roi-response-rate', '8');
    // doubling response rate doubles gross → $489,600 → "$490K".
    expect(gross()).toBe('$490K');
    setInput('roi-avg-balance', '500000');
    expect(gross()).not.toBe('$490K'); // larger balance → larger headline
  });

  it('shows an em-dash and guidance for invalid assumptions instead of NaN', () => {
    mount(1200);
    setInput('roi-response-rate', '');
    expect(gross()).toBe('—');
    expect(container.textContent).toContain('Enter non-negative numbers');
  });
});
