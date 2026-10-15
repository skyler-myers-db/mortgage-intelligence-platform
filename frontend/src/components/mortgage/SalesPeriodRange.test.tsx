/**
 * @vitest-environment happy-dom
 *
 * The Sales ops custom range (critic-10 6a) holds a DRAFT. Chromium fires a
 * date input's `change` on every keyboard edit, so neither `input` nor
 * `change` may apply it: Enter, Apply or focus leaving the range does. The
 * inputs are never re-keyed (an apply keeps the same elements, so focus
 * stays), and a draft that cannot be read stays in the fields with an inline
 * error. The keyboard walk itself is pinned in Chromium by
 * tests/e2e/fixture/sales-loop.fixture.spec.ts (f).
 */
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SalesPeriodRange from './SalesPeriodRange';

type Range = { from: string; to: string };

let root: Root;
const applied = vi.fn<(range: Range) => void>();

function Harness() {
  const [range, setRange] = useState<Range>({ from: '2026-07-01', to: '2026-07-10' });
  return (
    <>
      <button type="button" id="outside">Outside</button>
      <button type="button" id="preset" data-period-preset="">7 days</button>
      <SalesPeriodRange
        from={range.from}
        to={range.to}
        max="2026-07-14"
        onApply={(next) => {
          applied(next);
          setRange(next);
        }}
      />
      <button type="button" id="url" onClick={() => setRange({ from: '2026-06-01', to: '2026-06-30' })}>
        Back
      </button>
    </>
  );
}

const inputs = () => [...document.querySelectorAll<HTMLInputElement>('input[type="date"]')];
const errors = () => [...document.querySelectorAll('.field__error')].map((node) => node.textContent);

/** One keyboard edit as Chromium reports it: an input event AND a change event. */
function edit(input: HTMLInputElement, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

function press(input: HTMLInputElement, key: string) {
  act(() => {
    input.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
  });
}

function focusOut(from: HTMLElement, to: HTMLElement | null) {
  act(() => {
    from.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: to }));
  });
}

describe('SalesPeriodRange', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 6, 14, 11, 0));
    applied.mockReset();
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    act(() => root.render(<Harness />));
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.useRealTimers();
  });

  it('keeps every keyboard edit a draft: input and change events never apply it', () => {
    const [from] = inputs();
    edit(from, '2026-07-02');
    edit(from, '2026-07-03');

    expect(applied).not.toHaveBeenCalled();
    expect(inputs()[0]).toBe(from);
    expect(from.value).toBe('2026-07-03');
  });

  it('applies the draft pair on Enter and keeps the same input elements', () => {
    const [from, to] = inputs();
    edit(from, '2026-07-02');
    press(from, 'Enter');

    expect(applied).toHaveBeenCalledTimes(1);
    expect(applied).toHaveBeenCalledWith({ from: '2026-07-02', to: '2026-07-10' });
    // Never re-keyed: an apply keeps the elements, so a focused field keeps focus.
    expect(inputs()).toEqual([from, to]);
    expect(from.value).toBe('2026-07-02');
  });

  it('keeps a draft it cannot read in the fields with an inline error, and never applies it', () => {
    const [from, to] = inputs();
    edit(from, '2025-07-02');
    press(from, 'Enter');

    expect(applied).not.toHaveBeenCalled();
    expect(errors()).toEqual(['A custom range covers at most 92 days.']);
    expect(from.getAttribute('aria-invalid')).toBe('true');
    expect(to.getAttribute('aria-invalid')).toBeNull();
    expect(from.value).toBe('2025-07-02');

    edit(to, '2026-07-20');
    expect(errors()).toEqual(['Pick today or an earlier day.']);
    expect(to.getAttribute('aria-invalid')).toBe('true');

    // The error follows the draft live once shown, and a readable draft applies.
    edit(from, '2026-07-05');
    edit(to, '2026-07-12');
    expect(errors()).toEqual([]);
    press(to, 'Enter');
    expect(applied).toHaveBeenCalledWith({ from: '2026-07-05', to: '2026-07-12' });
  });

  it('applies when focus leaves the range, not when it moves between the two fields', () => {
    const [from, to] = inputs();
    edit(from, '2026-07-03');
    focusOut(from, to);
    expect(applied).not.toHaveBeenCalled();

    focusOut(to, document.getElementById('outside'));
    expect(applied).toHaveBeenCalledWith({ from: '2026-07-03', to: '2026-07-10' });
  });

  it('applies on the Apply button, and an unchanged draft applies nothing', () => {
    const button = [...document.querySelectorAll('button')].find((node) => node.textContent === 'Apply') as HTMLButtonElement;
    act(() => button.click());
    expect(applied).not.toHaveBeenCalled();

    edit(inputs()[1], '2026-07-11');
    act(() => button.click());
    expect(applied).toHaveBeenCalledWith({ from: '2026-07-01', to: '2026-07-11' });
  });

  it('takes a range that arrives from the URL in place, without remounting the fields', () => {
    const [from, to] = inputs();
    edit(from, '2026-07-04');
    act(() => (document.getElementById('url') as HTMLButtonElement).click());

    expect(inputs()).toEqual([from, to]);
    expect([from.value, to.value]).toEqual(['2026-06-01', '2026-06-30']);
    expect(applied).not.toHaveBeenCalled();
  });

  // W5e C11 (the W5d period-control residuals).
  it('announces a refused apply through the field notice region', () => {
    const [from] = inputs();
    const notices = () => [...document.querySelectorAll('.field__notice')].map((node) => node.textContent);
    expect(notices()).toEqual(['', '']);
    edit(from, '2025-07-02');
    press(from, 'Enter');
    expect(applied).not.toHaveBeenCalled();
    const region = document.querySelector<HTMLElement>(`#${CSS.escape(from.getAttribute('aria-describedby')?.split(' ').find((id) => id.endsWith('notice')) ?? '')}`);
    expect(region?.getAttribute('role')).toBe('status');
    expect(region?.getAttribute('aria-live')).toBe('polite');
    expect(region?.textContent).toBe('Range not applied.');
    // Fixed, it clears.
    edit(from, '2026-07-02');
    expect(notices()).toEqual(['', '']);
  });

  it('applies nothing when focus moves from a dirty draft onto a period preset', () => {
    const [from] = inputs();
    edit(from, '2026-07-03');
    focusOut(from, document.getElementById('preset'));
    expect(applied).not.toHaveBeenCalled();
    expect(from.value).toBe('2026-07-03');
  });
});
