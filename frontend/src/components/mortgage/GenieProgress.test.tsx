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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieLiveProgress } from '../../lib/api';
import type { GenieTurnProgress } from '../../types/genieJobs';
import {
  GENIE_DEEP_WAIT_LABEL,
  GENIE_VERIFY_WAIT_LABEL,
  GenieProgress,
  genieJobPartsLabel,
  genieProgressLabel,
  genieStatusLabel,
  genieTypicalDurationHint,
} from './GenieProgress';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

/** Genie's own turn is terminal; the client is inside the completion call. */
const LIVE_TERMINAL: GenieLiveProgress = {
  ...LIVE_EXECUTING,
  status: 'COMPLETED',
  stage: 'complete',
  // What an un-upgraded backend still sends. The client must not repeat it.
  stage_label: 'Answer ready — verifying and formatting',
  terminal: true,
};

describe('GenieProgress: the completion wait is named honestly (genie-01 phase 0)', () => {
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

  it('never says "Answer ready" while the governed answer is still being produced', () => {
    act(() => root.render(<GenieProgress progress={LIVE_TERMINAL} startedAt={Date.now()} />));
    expect(container.querySelector('.genie-progress__label')!.textContent).toBe(
      GENIE_VERIFY_WAIT_LABEL,
    );
    expect(container.textContent).not.toContain('Answer ready');
    expect(genieStatusLabel('COMPLETED')).toBe(GENIE_VERIFY_WAIT_LABEL);
  });

  it('labels a deep turn as deep research, on the head and on the active rail stage', () => {
    act(() =>
      root.render(<GenieProgress progress={{ ...LIVE_TERMINAL, deep: true }} startedAt={Date.now()} />),
    );
    expect(container.querySelector('.genie-progress__label')!.textContent).toBe(
      GENIE_DEEP_WAIT_LABEL,
    );
    const active = container.querySelector('.genie-progress__stage.is-active');
    expect(active!.textContent).toContain('Deep research');
    expect(container.textContent).not.toContain('Answer ready');
  });

  it('keeps server stage labels for every non-terminal stage, deep or not', () => {
    expect(genieProgressLabel({ ...LIVE_EXECUTING, deep: true })).toBe('Running the governed query');
    expect(genieProgressLabel(LIVE_EXECUTING)).toBe('Running the governed query');
    // A FAILED terminal turn is not a completion wait.
    expect(
      genieProgressLabel({
        ...LIVE_TERMINAL,
        status: 'FAILED',
        stage: 'failed',
        stage_label: 'Genie could not complete this question',
        failed: true,
      }),
    ).toBe('Genie could not complete this question');
  });
});

describe('GenieProgress: live region discipline (genie-v1 / a11y-06)', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  const liveRegions = () =>
    Array.from(container.querySelectorAll<HTMLElement>('[role="status"], [aria-live]'));

  it('mounts no live region: the ticker, the trace, the SQL and the stages are never spoken from here', () => {
    act(() => root.render(<GenieProgress progress={LIVE_EXECUTING} startedAt={Date.now()} />));

    // Each surface's ONE announcer (useGenieAnnouncer) speaks the stage label;
    // a region in the card would be a second speaker (a11y-06).
    expect(liveRegions()).toHaveLength(0);
    // The card itself is not a (implicitly atomic) status region either.
    expect(container.querySelector('.genie-progress')!.getAttribute('role')).toBeNull();
    const ticker = container.querySelector('.genie-progress__elapsed')!;
    expect(ticker.getAttribute('aria-hidden')).toBe('true');
    for (const selector of [
      '.genie-progress__elapsed',
      '.genie-progress__trace',
      '.genie-progress__sql',
      '.genie-progress__stages',
    ]) {
      expect(container.querySelector(selector)!.closest('[role="status"], [aria-live]')).toBeNull();
    }
    // The label the announcer shares is on screen.
    expect(container.querySelector('.genie-progress__label')!.textContent).toBe(genieProgressLabel(LIVE_EXECUTING));
  });

  it('the clock ticks with no live region anywhere in the card', () => {
    const startedAt = Date.now();
    act(() => root.render(<GenieProgress progress={LIVE_EXECUTING} startedAt={startedAt} />));
    const ticker = container.querySelector('.genie-progress__elapsed')!;
    const before = ticker.textContent;

    act(() => {
      vi.advanceTimersByTime(5_000);
    });

    // The clock really moved, and it still sits in no live region.
    expect(ticker.textContent).not.toBe(before);
    expect(ticker.textContent).toBe('5s');
    expect(liveRegions()).toHaveLength(0);
  });

  it('stops the clock while paused (card hidden) and catches up on resume', () => {
    const startedAt = Date.now();
    const ticker = () => container.querySelector('.genie-progress__elapsed')!;
    act(() => root.render(<GenieProgress progress={LIVE_EXECUTING} startedAt={startedAt} paused />));
    expect(ticker().textContent).toBe('0s');

    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    // No interval drives a hidden card.
    expect(ticker().textContent).toBe('0s');

    act(() =>
      root.render(<GenieProgress progress={LIVE_EXECUTING} startedAt={startedAt} paused={false} />),
    );
    // Shown again: the real elapsed time, not the frozen one.
    expect(ticker().textContent).toBe('5s');
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(ticker().textContent).toBe('6s');
  });

  it('keeps the shared label stable while only the trace and SQL grow, and changes it with the stage', () => {
    // The announcer re-speaks only when this label string changes
    // (useGenieAnnouncer.test.tsx pins the region side).
    const grown: GenieLiveProgress = {
      ...LIVE_EXECUTING,
      reasoning_trace: [
        ...LIVE_EXECUTING.reasoning_trace,
        { kind: 'execute', content: 'Executed the governed query.' },
      ],
      sql_preview: 'SELECT state, COUNT(*) FROM mip.gold.borrower_360 GROUP BY state',
    };
    expect(genieProgressLabel(grown)).toBe(genieProgressLabel(LIVE_EXECUTING));
    expect(genieProgressLabel(LIVE_TERMINAL)).toBe(GENIE_VERIFY_WAIT_LABEL);
  });
});

describe('GenieProgress: the completion job speaks with the server stage (genie-01)', () => {
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

  const RESEARCHING: GenieTurnProgress = {
    ...LIVE_TERMINAL,
    deep: true,
    job: { stage: 'researching', stage_label: 'Running governed sub-analyses', parts_done: 3, parts_planned: 7 },
  };

  it('labels the wait with the job stage, and the announcer text never carries the count', () => {
    expect(genieProgressLabel(RESEARCHING)).toBe('Running governed sub-analyses');
    expect(genieProgressLabel({ ...RESEARCHING, job: { ...RESEARCHING.job!, parts_done: 4 } })).toBe(
      'Running governed sub-analyses',
    );
    expect(genieJobPartsLabel(RESEARCHING)).toBe('3 of 7 sub-analyses finished');
  });

  it('shows "k of n sub-analyses finished" inside the label span, on the last rail dot', () => {
    act(() => root.render(<GenieProgress progress={RESEARCHING} startedAt={Date.now()} />));

    const label = container.querySelector('.genie-progress__label')!;
    expect(label.textContent).toBe('Running governed sub-analyses · 3 of 7 sub-analyses finished');
    const stages = Array.from(container.querySelectorAll('.genie-progress__stage'));
    expect(stages).toHaveLength(4);
    expect(stages[3].className).toContain('is-active');
    expect(stages[3].textContent).toContain('Deep research');
    expect(container.textContent).not.toContain('Answer ready');
  });

  it('shows no count outside the sweep', () => {
    const verifying: GenieTurnProgress = {
      ...LIVE_TERMINAL,
      job: { stage: 'verifying', stage_label: 'Verifying the answer against its rows', parts_done: null, parts_planned: null },
    };
    act(() => root.render(<GenieProgress progress={verifying} />));

    expect(container.querySelector('.genie-progress__label')!.textContent).toBe('Verifying the answer against its rows');
    expect(genieJobPartsLabel(verifying)).toBeNull();
  });

  it('appends the typical duration after the parts, inside the label span, and never to the announced label', () => {
    const typical: GenieTurnProgress = { ...RESEARCHING, job: { ...RESEARCHING.job!, typical_seconds: 170 } };
    act(() => root.render(<GenieProgress progress={typical} startedAt={Date.now()} />));

    expect(container.querySelector('.genie-progress__label')!.textContent).toBe(
      'Running governed sub-analyses · 3 of 7 sub-analyses finished · usually about 3 min',
    );
    expect(genieProgressLabel(typical)).toBe('Running governed sub-analyses');
    expect(container.querySelectorAll('.genie-progress__label')).toHaveLength(1);
  });

  it.each([
    [42, 'usually under a minute'],
    [59, 'usually under a minute'],
    [60, 'usually about 1 min'],
    [89, 'usually about 1 min'],
    [90, 'usually about 2 min'],
    [170, 'usually about 3 min'],
  ])('rounds %s s to "%s"', (seconds, hint) => {
    expect(genieTypicalDurationHint({ ...RESEARCHING, job: { ...RESEARCHING.job!, typical_seconds: seconds } })).toBe(hint);
  });

  it('shows no hint when the field is absent or null', () => {
    for (const job of [RESEARCHING.job!, { ...RESEARCHING.job!, typical_seconds: null }]) {
      act(() => root.render(<GenieProgress progress={{ ...RESEARCHING, job }} />));
      expect(container.querySelector('.genie-progress__label')!.textContent).not.toContain('usually');
      expect(genieTypicalDurationHint({ ...RESEARCHING, job })).toBeNull();
    }
    expect(genieTypicalDurationHint(LIVE_TERMINAL)).toBeNull();
  });
});
