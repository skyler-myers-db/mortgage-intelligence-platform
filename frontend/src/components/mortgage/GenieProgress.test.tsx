/**
 * @vitest-environment happy-dom
 *
 * GenieProgress status mapping. A known genie_status renders observed copy;
 * unknown / absent status stays honestly indeterminate without a timer.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { GenieProgress, genieStatusLabel } from './GenieProgress';

describe('genieStatusLabel', () => {
  it('maps observed Genie statuses to human progress copy', () => {
    expect(genieStatusLabel('SUBMITTED')).toBe('Question submitted to Genie');
    expect(genieStatusLabel('IN_PROGRESS')).toBe('Genie is processing the question');
    expect(genieStatusLabel('EXECUTING_QUERY')).toBe('Genie is running the generated query');
  });

  it('returns null for unknown or absent statuses', () => {
    expect(genieStatusLabel('SOMETHING_ELSE')).toBeNull();
    expect(genieStatusLabel(null)).toBeNull();
    expect(genieStatusLabel(undefined)).toBeNull();
  });
});

describe('GenieProgress', () => {
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

  it('renders the mapped label when a known status is provided', () => {
    act(() => root.render(<GenieProgress status="EXECUTING_QUERY" />));
    expect(container.querySelector('.genie-progress__head')!.textContent).toContain(
      'Genie is running the generated query',
    );
  });

  it('uses a static indeterminate label for an unknown status', () => {
    act(() => root.render(<GenieProgress status="MYSTERY" />));
    expect(container.querySelector('.genie-progress__head')!.textContent).toContain(
      'Waiting for Genie response',
    );
  });

  it('uses the same static indeterminate label when no status is provided', () => {
    act(() => root.render(<GenieProgress />));
    expect(container.querySelector('.genie-progress__head')!.textContent).toContain(
      'Waiting for Genie response',
    );
    expect(container.querySelector('.genie-progress__rail')).toBeNull();
  });
});
