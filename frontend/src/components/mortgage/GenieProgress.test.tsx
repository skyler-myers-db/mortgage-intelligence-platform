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
import {
  GENIE_DEEP_WAIT_LABEL,
  GENIE_VERIFY_WAIT_LABEL,
  GenieProgress,
  genieProgressLabel,
  genieStatusLabel,
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

  function observe(regions: HTMLElement[]): MutationObserver {
    const observer = new MutationObserver(() => undefined);
    for (const region of regions) {
      observer.observe(region, { childList: true, characterData: true, subtree: true });
    }
    return observer;
  }

  it('keeps the ticker, the trace and the SQL out of every live region', () => {
    act(() => root.render(<GenieProgress progress={LIVE_EXECUTING} startedAt={Date.now()} />));

    const regions = liveRegions();
    expect(regions).toHaveLength(1);
    // The card itself is not a (implicitly atomic) status region any more.
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
    expect(regions[0].textContent).toBe('Running the governed query');
  });

  it('does not mutate the live region while the clock ticks', () => {
    const startedAt = Date.now();
    act(() => root.render(<GenieProgress progress={LIVE_EXECUTING} startedAt={startedAt} />));
    const observer = observe(liveRegions());
    const ticker = container.querySelector('.genie-progress__elapsed')!;
    const before = ticker.textContent;

    act(() => {
      vi.advanceTimersByTime(5_000);
    });

    // The clock really moved, so a quiet region is not a frozen component.
    expect(ticker.textContent).not.toBe(before);
    expect(ticker.textContent).toBe('5s');
    expect(observer.takeRecords()).toHaveLength(0);
    observer.disconnect();
  });

  it('announces a stage CHANGE exactly once, and nothing for trace or SQL growth', () => {
    const startedAt = Date.now();
    act(() => root.render(<GenieProgress progress={LIVE_EXECUTING} startedAt={startedAt} />));
    const region = liveRegions()[0];
    const observer = observe([region]);

    act(() =>
      root.render(
        <GenieProgress
          startedAt={startedAt}
          progress={{
            ...LIVE_EXECUTING,
            reasoning_trace: [
              ...LIVE_EXECUTING.reasoning_trace,
              { kind: 'execute', content: 'Executed the governed query.' },
            ],
            sql_preview: 'SELECT state, COUNT(*) FROM mip.gold.borrower_360 GROUP BY state',
          }}
        />,
      ),
    );
    expect(observer.takeRecords()).toHaveLength(0);

    act(() => root.render(<GenieProgress startedAt={startedAt} progress={LIVE_TERMINAL} />));
    expect(region.textContent).toBe(GENIE_VERIFY_WAIT_LABEL);
    expect(observer.takeRecords().length).toBeGreaterThan(0);
    observer.disconnect();
  });

  it('renders no live region at all when the surface owns the announcer', () => {
    act(() =>
      root.render(<GenieProgress progress={LIVE_EXECUTING} startedAt={Date.now()} announce={false} />),
    );
    expect(liveRegions()).toHaveLength(0);
  });
});
