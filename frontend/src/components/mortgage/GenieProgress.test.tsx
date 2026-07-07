/**
 * @vitest-environment happy-dom
 *
 * GenieProgress staged-copy mapping. A known genie_status renders its mapped
 * human label with no fake timer; an unknown / absent status falls back to the
 * generic rotating first step.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { GenieProgress, genieStatusLabel } from './GenieProgress';

describe('genieStatusLabel', () => {
  it('maps known Genie statuses to human staged copy', () => {
    expect(genieStatusLabel('FILTERING_CONTEXT')).toBe('Scoping context');
    expect(genieStatusLabel('PENDING_WAREHOUSE')).toBe('Warming warehouse');
    expect(genieStatusLabel('ASKING_AI')).toBe('Composing answer');
    expect(genieStatusLabel('EXECUTING_QUERY')).toBe('Running governed SQL');
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

  it('renders the mapped staged label when a known status is provided', () => {
    act(() => root.render(<GenieProgress status="EXECUTING_QUERY" />));
    expect(container.querySelector('.genie-progress__head')!.textContent).toContain(
      'Running governed SQL',
    );
  });

  it('falls back to the generic first step for an unknown status', () => {
    act(() => root.render(<GenieProgress status="MYSTERY" />));
    expect(container.querySelector('.genie-progress__head')!.textContent).toContain(
      'Opening a governed Genie turn',
    );
  });

  it('falls back to the generic first step when no status is provided', () => {
    act(() => root.render(<GenieProgress />));
    expect(container.querySelector('.genie-progress__head')!.textContent).toContain(
      'Opening a governed Genie turn',
    );
  });
});
