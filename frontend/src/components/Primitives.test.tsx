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
