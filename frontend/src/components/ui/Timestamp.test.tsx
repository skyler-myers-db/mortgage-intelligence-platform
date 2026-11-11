/**
 * @vitest-environment happy-dom
 *
 * <Timestamp> contract (2026-09-21 audit, responsive-07): a `<time>` with a
 * machine-readable `dateTime` and the absolute UTC in a ui/Tooltip (critic-08,
 * W5e: never a native `title`) around the lib/time label. The rendered freshness chip is pinned at the page layer by
 * tests/e2e/fixture/formatters.fixture.spec.ts.
 */

import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { tooltipText } from '../../test/tooltipText';
import { Timestamp } from './Timestamp';

const NOW = Date.parse('2026-07-14T15:00:00Z');

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
  vi.useRealTimers();
});

function render(node: ReactNode): void {
  act(() => root.render(node));
}

describe('Timestamp', () => {
  it('renders relative age inside <time dateTime>, the absolute UTC in its Tooltip', () => {
    render(<Timestamp value="2026-07-14 12:00:00" now={NOW} />);
    const time = container.querySelector('time');
    expect(time?.textContent).toBe('3 hours ago');
    expect(time?.getAttribute('dateTime')).toBe('2026-07-14T12:00:00.000Z');
    expect(time?.hasAttribute('title')).toBe(false);
    expect(tooltipText(time)).toBe('Jul 14, 2026, 12:00 PM UTC');
  });

  it('renders the short date and date-time forms', () => {
    render(
      <>
        <Timestamp value="2026-07-14" format="date" now={NOW} />
        <Timestamp value="2026-07-14T12:00:00Z" format="datetime" now={NOW} />
      </>,
    );
    const [date, dateTime] = Array.from(container.querySelectorAll('time'));
    expect(date.textContent).toBe('Jul 14');
    expect(date.getAttribute('dateTime')).toBe('2026-07-14');
    expect(tooltipText(date)).toBe('Jul 14, 2026');
    expect(dateTime.textContent).toMatch(/^Jul 14, \d{1,2}:00 [AP]M \S+$/);
  });

  it('renders a calendar date in the date-time form as the date, with no invented clock', () => {
    // W5e: the Analytics Executive drawer's "Data as of" took the date-only
    // snapshot_date through format="datetime" and printed the day before at 8:00 PM.
    render(<Timestamp value="2026-07-14" format="datetime" now={NOW} />);
    const time = container.querySelector('time');
    expect(time?.textContent).toBe('Jul 14');
    expect(time?.getAttribute('dateTime')).toBe('2026-07-14');
    expect(tooltipText(time)).toBe('Jul 14, 2026');
  });

  it('renders a sentence label for an age under the "now" bucket, keeping the <time>', () => {
    render(<Timestamp value={NOW - 20_000} nowLabel="just now" now={NOW} />);
    const time = container.querySelector('time');
    expect(time?.textContent).toBe('just now');
    expect(time?.getAttribute('dateTime')).toBe('2026-07-14T14:59:40.000Z');
    expect(tooltipText(time)).toBe('Jul 14, 2026, 2:59 PM UTC');
  });

  it('renders the prototype trigger form in the narrow style', () => {
    render(<Timestamp value="2026-07-12T15:00:00Z" relativeStyle="narrow" now={NOW} />);
    expect(container.querySelector('time')?.textContent).toBe('2d ago');
  });

  it('renders the fallback as plain text for a missing value, never a <time>', () => {
    render(
      <>
        <Timestamp value={null} />
        <Timestamp value="garbage" fallback="Never" />
      </>,
    );
    expect(container.querySelector('time')).toBeNull();
    expect(container.textContent).toBe('—Never');
  });

  it('keeps a relative label true while the page stays open', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    render(<Timestamp value="2026-07-14T14:58:00Z" />);
    expect(container.querySelector('time')?.textContent).toBe('2 minutes ago');
    act(() => {
      vi.advanceTimersByTime(10 * 60_000);
    });
    expect(container.querySelector('time')?.textContent).toBe('12 minutes ago');
  });
});

describe('the shared minute clock', () => {
  // The clock is the minute it is in. It used to tick every 60 s from the
  // first mount, so a first mount 50 s into a minute kept it 50 s to 110 s
  // behind the real time, and a value stamped at that moment read "in 1m"
  // or "in 2m" (PR #338 review: "Fetched in 1m" on a fresh read).
  const LATE_MOUNT = Date.parse('2026-07-14T15:00:50Z');
  const label = () => container.querySelector('time')?.textContent;

  it('labels a value stamped just now as now, however late in its minute the clock started', () => {
    vi.useFakeTimers();
    vi.setSystemTime(LATE_MOUNT);
    render(<Timestamp value={Date.now()} relativeStyle="narrow" />);
    expect(label()).toBe('now');
    act(() => {
      vi.advanceTimersByTime(40_000); // 15:01:30
    });
    render(<Timestamp value={Date.now()} relativeStyle="narrow" />);
    expect(label()).toBe('now');
    act(() => {
      vi.advanceTimersByTime(75_000); // 15:02:45, the last 15 s of a minute
    });
    render(<Timestamp value={Date.now()} relativeStyle="narrow" />);
    expect(label()).toBe('now');
  });

  it("moves on at the wall clock's minute, not 60 s after the first mount", () => {
    vi.useFakeTimers();
    vi.setSystemTime(LATE_MOUNT);
    render(<Timestamp value="2026-07-14T14:58:00Z" />);
    expect(label()).toBe('2 minutes ago');
    act(() => {
      vi.advanceTimersByTime(10_000); // 15:01:00
    });
    expect(label()).toBe('3 minutes ago');
  });

  it("keeps a value in the next minute ahead of the clock", () => {
    // Only the clock's own minute reads "now": 15:01:00 is not in 15:00.
    vi.useFakeTimers();
    vi.setSystemTime(LATE_MOUNT);
    render(<Timestamp value="2026-07-14T15:01:00Z" relativeStyle="narrow" />);
    expect(label()).toBe('in 1m');
  });
});
