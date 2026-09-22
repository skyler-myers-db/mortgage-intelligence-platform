/**
 * @vitest-environment happy-dom
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { escapeLayerCount, pushEscapeLayer } from './escapeStack';

function pressEscape(target: EventTarget = window): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
  target.dispatchEvent(event);
  return event;
}

describe('escapeStack', () => {
  const pops: Array<() => void> = [];
  const push = (handler: Parameters<typeof pushEscapeLayer>[0]) => {
    const pop = pushEscapeLayer(handler);
    pops.push(pop);
    return pop;
  };

  afterEach(() => {
    while (pops.length > 0) pops.pop()?.();
    expect(escapeLayerCount()).toBe(0);
  });

  it('runs only the topmost layer and consumes the keypress', () => {
    const lower = vi.fn();
    const upper = vi.fn();
    const bystander = vi.fn();
    window.addEventListener('keydown', bystander);
    push(lower);
    push(upper);

    const event = pressEscape();

    expect(upper).toHaveBeenCalledOnce();
    expect(lower).not.toHaveBeenCalled();
    // Consumed: no other window listener sees the same Escape.
    expect(bystander).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(true);
    window.removeEventListener('keydown', bystander);
  });

  it('hands the next keypress to the layer below once the top layer pops', () => {
    const lower = vi.fn();
    const upper = vi.fn();
    push(lower);
    const popUpper = push(upper);

    pressEscape();
    popUpper();
    pressEscape();

    expect(upper).toHaveBeenCalledOnce();
    expect(lower).toHaveBeenCalledOnce();
  });

  it('falls through a layer that declines, and leaves the key alone when every layer declines', () => {
    const lower = vi.fn();
    const declining = vi.fn(() => false as const);
    const bystander = vi.fn();
    window.addEventListener('keydown', bystander);
    const popLower = push(lower);
    push(declining);

    pressEscape();
    expect(declining).toHaveBeenCalledOnce();
    expect(lower).toHaveBeenCalledOnce();
    expect(bystander).not.toHaveBeenCalled();

    popLower();
    const untouched = pressEscape();
    expect(declining).toHaveBeenCalledTimes(2);
    expect(untouched.defaultPrevented).toBe(false);
    expect(bystander).toHaveBeenCalledOnce();
    window.removeEventListener('keydown', bystander);
  });

  it('ignores other keys and tolerates a double pop', () => {
    const handler = vi.fn();
    const pop = push(handler);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(handler).not.toHaveBeenCalled();

    pop();
    pop();
    expect(escapeLayerCount()).toBe(0);
    pressEscape();
    expect(handler).not.toHaveBeenCalled();
  });

  it('survives a handler that pops itself while the stack is being walked', () => {
    const lower = vi.fn();
    push(lower);
    const pop = push(() => {
      pop();
    });

    pressEscape();

    expect(lower).not.toHaveBeenCalled();
    expect(escapeLayerCount()).toBe(1);
  });
});
