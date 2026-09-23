/**
 * @vitest-environment happy-dom
 *
 * <Timestamp> contract (2026-09-21 audit, responsive-07): a `<time>` with a
 * machine-readable `dateTime` and an absolute-UTC `title` around the lib/time
 * label. The rendered freshness chip is pinned at the page layer by
 * tests/e2e/fixture/formatters.fixture.spec.ts.
 */

import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Timestamp } from './Timestamp';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
  it('renders relative age inside <time dateTime title={absolute UTC}>', () => {
    render(<Timestamp value="2026-07-14 12:00:00" now={NOW} />);
    const time = container.querySelector('time');
    expect(time?.textContent).toBe('3 hours ago');
    expect(time?.getAttribute('dateTime')).toBe('2026-07-14T12:00:00.000Z');
    expect(time?.getAttribute('title')).toBe('Jul 14, 2026, 12:00 PM UTC');
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
    expect(date.getAttribute('title')).toBe('Jul 14, 2026');
    expect(dateTime.textContent).toMatch(/^Jul 14, \d{1,2}:00 [AP]M \S+$/);
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
