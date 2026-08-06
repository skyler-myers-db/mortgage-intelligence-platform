/**
 * @vitest-environment happy-dom
 *
 * GenieProgress: live-lifecycle rendering (stage rail, translated process
 * steps, SQL preview, elapsed ticker) plus the legacy status-string mapping
 * for callers without the async flow. Unknown / absent state stays honestly
 * indeterminate.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { GenieLiveProgress } from '../../lib/api';
import { GenieProgress, genieStatusLabel } from './GenieProgress';

describe('genieStatusLabel', () => {
  it('maps observed Genie statuses to human progress copy', () => {
    expect(genieStatusLabel('SUBMITTED')).toBe('Question submitted to Genie');
    expect(genieStatusLabel('IN_PROGRESS')).toBe('Genie is processing the question');
    expect(genieStatusLabel('EXECUTING_QUERY')).toBe('Genie is running the generated query');
    expect(genieStatusLabel('ASKING_AI')).toBe('Genie is drafting a governed SQL plan');
  });

  it('returns null for unknown or absent statuses', () => {
    expect(genieStatusLabel('SOMETHING_ELSE')).toBeNull();
    expect(genieStatusLabel(null)).toBeNull();
    expect(genieStatusLabel(undefined)).toBeNull();
  });
});

const LIVE_EXECUTING: GenieLiveProgress = {
  status: 'EXECUTING_QUERY',
  stage: 'executing',
  stage_label: 'Running the governed query',
  terminal: false,
  failed: false,
  reasoning_trace: [
    { kind: 'context', content: 'Selected the governed mortgage context for this question.' },
    { kind: 'query', content: 'Prepared a governed query plan over approved data assets.' },
    // Duplicate category copy — must render once.
    { kind: 'query', content: 'Prepared a governed query plan over approved data assets.' },
  ],
  sql_preview: 'SELECT COUNT(*) FROM mip.gold.borrower_360',
  error_hint: null,
};

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
    expect(container.querySelector('.genie-progress__trace')).toBeNull();
    expect(container.querySelector('.genie-progress__sql')).toBeNull();
  });

  it('renders the live stage rail with done/active states', () => {
    act(() => root.render(<GenieProgress progress={LIVE_EXECUTING} />));
    expect(container.querySelector('.genie-progress__head')!.textContent).toContain(
      'Running the governed query',
    );
    const stages = Array.from(container.querySelectorAll('.genie-progress__stage'));
    expect(stages).toHaveLength(4);
    expect(stages[0].className).toContain('is-done');
    expect(stages[1].className).toContain('is-done');
    expect(stages[2].className).toContain('is-active');
    expect(stages[2].getAttribute('aria-current')).toBe('step');
    expect(stages[3].className).toContain('is-pending');
  });

  it('renders deduped public process steps and the SQL preview', () => {
    act(() => root.render(<GenieProgress progress={LIVE_EXECUTING} />));
    const steps = Array.from(container.querySelectorAll('.genie-progress__trace-step'));
    expect(steps).toHaveLength(2);
    expect(steps[0].textContent).toContain('Selected the governed mortgage context');
    const sql = container.querySelector('.genie-progress__sql pre');
    expect(sql!.textContent).toBe('SELECT COUNT(*) FROM mip.gold.borrower_360');
    // Implementation plumbing ("Live from the Databricks Genie Conversation
    // API") is not end-user copy — the progress panel renders no meta line.
    expect(container.querySelector('.genie-progress__meta')).toBeNull();
  });

  it('keeps the stage rail indeterminate for an unknown live stage', () => {
    act(() =>
      root.render(
        <GenieProgress
          progress={{ ...LIVE_EXECUTING, stage: 'working', stage_label: 'Genie is working' }}
        />,
      ),
    );
    const stages = Array.from(container.querySelectorAll('.genie-progress__stage'));
    expect(stages.every((s) => s.className.includes('is-pending'))).toBe(true);
  });

  it('shows the elapsed ticker when startedAt is provided', () => {
    act(() =>
      root.render(<GenieProgress progress={LIVE_EXECUTING} startedAt={Date.now() - 3_000} />),
    );
    expect(container.querySelector('.genie-progress__elapsed')!.textContent).toMatch(/^\d+s$/);
  });
});
