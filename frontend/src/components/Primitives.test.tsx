/**
 * @vitest-environment happy-dom
 */
import { act, useRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount } from '../test/render';
import { Button, Chip, freshnessBucket } from './Primitives';
// Installs the delegated tooltip listeners the shell loads at idle.
import './ui/tooltipController';

vi.mock('./AppContext', () => ({
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true }),
}));

/**
 * Freshness bucketing drives the visible dot beside every evidence chip.
 * These tests pin the 7-day / 30-day thresholds so a refactor can't
 * silently drift the "Fresh" / "Aging" / "Stale" semantics operators
 * read on the Lead Queue and Borrower 360 pages.
 */
describe('freshnessBucket', () => {
  const now = new Date('2026-04-22T12:00:00Z');

  it('returns null when updatedAt is missing', () => {
    expect(freshnessBucket(undefined, now)).toBeNull();
    expect(freshnessBucket('', now)).toBeNull();
  });

  it('returns null for unparseable timestamps (no grey placeholder)', () => {
    expect(freshnessBucket('not a date', now)).toBeNull();
  });

  it('treats <= 7 days as fresh', () => {
    expect(freshnessBucket('2026-04-20 06:12 UTC', now)).toBe('fresh');
    expect(freshnessBucket('2026-04-22T00:00:00Z', now)).toBe('fresh');
  });

  it('treats 7–30 days as aging', () => {
    // 14 days ago
    expect(freshnessBucket('2026-04-08T12:00:00Z', now)).toBe('aging');
    // exactly 30 days ago (boundary — still aging)
    expect(freshnessBucket('2026-03-23T12:00:00Z', now)).toBe('aging');
  });

  it('treats > 30 days as stale', () => {
    expect(freshnessBucket('2026-03-01T12:00:00Z', now)).toBe('stale');
    expect(freshnessBucket('2025-12-01T12:00:00Z', now)).toBe('stale');
  });
});

/**
 * The additive `tooltip` prop (2026-09-21 audit critic-08): Chip and Button
 * wrap themselves in ui/Tooltip when it is set; the `title` passthrough is
 * unchanged in W5a (its call sites migrate in W5d).
 */
describe('Chip and Button tooltip prop', () => {
  afterEach(() => {
    (document.activeElement as HTMLElement | null)?.blur?.();
  });

  function describedText(element: Element): string {
    return (element.getAttribute('aria-describedby') ?? '')
      .split(/\s+/)
      .filter(Boolean)
      .map((id) => document.getElementById(id)?.textContent ?? '')
      .join(' | ');
  }

  it('gives a chip a hover tooltip and a hidden description', async () => {
    const { container } = await mount(<Chip variant="warning" tooltip="Suppressed by DNC list">DNC</Chip>);
    const chip = container.querySelector<HTMLElement>('span.chip.chip--warning')!;

    expect(describedText(chip)).toBe('Suppressed by DNC list');
    expect(chip.hasAttribute('title')).toBe(false);
    expect(chip.querySelector('.chip__label')?.textContent).toBe('DNC');
  });

  it('keeps the chip title passthrough exactly as before', async () => {
    const { container } = await mount(
      <>
        <Chip title="2026-09-29T06:00:00Z">Refreshed</Chip>
        <Chip title="legacy" tooltip="New tooltip">Both</Chip>
      </>,
    );
    const [plain, both] = [...container.querySelectorAll<HTMLElement>('span.chip')];

    expect(plain?.getAttribute('title')).toBe('2026-09-29T06:00:00Z');
    expect(plain?.hasAttribute('aria-describedby')).toBe(false);
    expect(both?.getAttribute('title')).toBe('legacy');
    expect(describedText(both!)).toBe('New tooltip');
  });

  it('gives a button a keyboard-reachable tooltip with a shortcut, keeping its ref and title', async () => {
    let captured: HTMLButtonElement | null = null;
    function WithRef() {
      const ref = useRef<HTMLButtonElement>(null);
      return (
        <Button
          ref={(element) => {
            ref.current = element;
            captured = element;
          }}
          aria-label="Approve"
          title="legacy title"
          tooltip="Approve the draft"
          tooltipShortcut="A"
        >
          Approve
        </Button>
      );
    }
    const { container } = await mount(<WithRef />);
    const button = container.querySelector<HTMLButtonElement>('button.btn')!;

    expect(captured).toBe(button);
    expect(button.getAttribute('title')).toBe('legacy title');
    expect(describedText(button)).toBe('Approve the draft (A)');

    act(() => {
      document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
      button.focus();
    });
    const tip = document.body.querySelector('[role="tooltip"]:not([hidden])');
    expect(tip?.textContent).toBe('Approve the draftA');
    expect(tip?.querySelector('kbd.tooltip__kbd')?.textContent).toBe('A');
  });

  it('renders no tooltip machinery without the prop', async () => {
    const { container } = await mount(<Button>Plain</Button>);
    const button = container.querySelector<HTMLButtonElement>('button.btn')!;

    expect(button.hasAttribute('aria-describedby')).toBe(false);
  });
});

/**
 * motion-08 slice 2: the Button `loading` prop. Pending is aria-busy plus
 * aria-disabled, never native disabled (a focused button that turns
 * disabled drops focus), and the label stays in place for the accessible
 * name. Keyboard Enter / Space reach a button as a click, so the click
 * cases cover them; the rendered width, opacity and spinner are pinned in
 * tests/e2e/fixture/theme-white-label.fixture.spec.ts.
 */
describe('Button loading', () => {
  it('marks the button busy and aria-disabled, never natively disabled', async () => {
    const { container } = await mount(<Button variant="primary" icon="check" loading>Approve</Button>);
    const button = container.querySelector<HTMLButtonElement>('button')!;

    expect(button.className).toBe('btn btn--primary btn--loading');
    expect(button.getAttribute('aria-busy')).toBe('true');
    expect(button.getAttribute('aria-disabled')).toBe('true');
    expect(button.hasAttribute('disabled')).toBe(false);
    expect(button.querySelector('.btn__label')?.textContent).toBe('Approve');
    expect(button.querySelector('.btn__label svg')).not.toBeNull();
    expect(button.querySelector('.btn__spinner')?.getAttribute('aria-hidden')).toBe('true');
    // The accessible name is the label's text; the spinner adds none.
    expect(button.textContent).toBe('Approve');
  });

  it('keeps focus on the button when loading turns on, and back off', async () => {
    const { container, rerender } = await mount(<Button>Save</Button>);
    const button = container.querySelector<HTMLButtonElement>('button')!;
    act(() => button.focus());
    await rerender(<Button loading>Save</Button>);
    expect(container.querySelector('button')).toBe(button);
    expect(document.activeElement).toBe(button);
    await rerender(<Button>Save</Button>);
    expect(document.activeElement).toBe(button);
  });

  it('never calls onClick and never submits its form while loading', async () => {
    const onClick = vi.fn();
    const onSubmit = vi.fn((event: SubmitEvent) => event.preventDefault());
    const { container, rerender } = await mount(
      <form onSubmit={(event) => onSubmit(event.nativeEvent as SubmitEvent)}>
        <Button type="submit" onClick={onClick} loading>Save</Button>
      </form>,
    );
    const button = container.querySelector<HTMLButtonElement>('button')!;
    act(() => button.click());
    expect(onClick).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();

    // Non-vacuity: the same button, not loading, clicks and submits.
    await rerender(
      <form onSubmit={(event) => onSubmit(event.nativeEvent as SubmitEvent)}>
        <Button type="submit" onClick={onClick}>Save</Button>
      </form>,
    );
    act(() => button.click());
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it('renders exactly the plain markup when not loading', async () => {
    const { container } = await mount(
      <>
        <Button variant="ghost" icon="check" iconEnd="chevright" onClick={() => undefined}>Open</Button>
        <Button variant="ghost" icon="check" iconEnd="chevright" onClick={() => undefined} loading={false}>Open</Button>
      </>,
    );
    const [plain, unloaded] = [...container.querySelectorAll('button')];
    expect(unloaded?.outerHTML).toBe(plain?.outerHTML);
    expect(plain?.querySelector('.btn__label, .btn__spinner')).toBeNull();
    expect(plain?.hasAttribute('aria-busy')).toBe(false);
  });
});

