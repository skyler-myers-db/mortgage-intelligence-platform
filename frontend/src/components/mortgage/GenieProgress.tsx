import { useEffect, useState } from 'react';

import type { GenieLiveProgress } from '../../lib/api';
import { Icon } from '../Icon';

const INDETERMINATE_LABEL = 'Waiting for Genie response';

/**
 * Genie message statuses → human progress copy. Server-owned stage labels
 * (progress.stage_label) win when the live lifecycle is active; this map
 * remains for callers that only hold a raw status string.
 */
const GENIE_STATUS_LABELS: Record<string, string> = {
  SUBMITTED: 'Question submitted to Genie',
  FETCHING_METADATA: 'Genie is reading governed table metadata',
  FILTERING_CONTEXT: 'Genie is selecting the governed mortgage context',
  ASKING_AI: 'Genie is drafting a governed SQL plan',
  PENDING_WAREHOUSE: 'Genie is waking the SQL warehouse',
  IN_PROGRESS: 'Genie is processing the question',
  EXECUTING_QUERY: 'Genie is running the generated query',
  // Genie finishing its turn is NOT the answer being ready: the governed
  // completion (claims verification, output policy, audit) still runs.
  COMPLETED: 'Verifying the answer against its rows',
};

/**
 * Honest copy for the wait AFTER Genie's own turn is terminal and BEFORE the
 * governed answer is renderable (audit 2026-09-21 `genie-01` phase 0). That
 * wait is one blocking completion call: seconds for a single turn, 90-200 s
 * for a deep-research turn that fans out into planned sub-analyses. The rail
 * used to say "Answer ready" for all of it. "Answer ready" is reserved for the
 * moment the answer can actually be rendered.
 */
export const GENIE_VERIFY_WAIT_LABEL = 'Verifying the answer against its rows';
export const GENIE_DEEP_WAIT_LABEL = 'Deep research: running governed sub-analyses';

export function genieStatusLabel(status: string | null | undefined): string | null {
  if (!status) return null;
  return GENIE_STATUS_LABELS[status] ?? null;
}

/** True once Genie's own turn finished cleanly and the client is waiting on
 * the governed completion call. */
function awaitingCompletion(progress: GenieLiveProgress | null | undefined): boolean {
  return Boolean(progress?.terminal && !progress.failed);
}

/** The one line that names what is happening right now. Shared with the
 * floating panel's persistent screen-reader announcer so both say the same
 * thing. */
export function genieProgressLabel(
  progress: GenieLiveProgress | null | undefined,
  status?: string | null,
): string {
  if (awaitingCompletion(progress)) {
    return progress?.deep ? GENIE_DEEP_WAIT_LABEL : GENIE_VERIFY_WAIT_LABEL;
  }
  return (
    progress?.stage_label ?? genieStatusLabel(progress?.status ?? status) ?? INDETERMINATE_LABEL
  );
}

/** Ordered lifecycle rail. `stage` values come from the backend's bounded
 * vocabulary; anything unknown ("working") keeps the rail indeterminate. The
 * last stage is the governed completion, named for what it really does. */
function stagesFor(deep: boolean): Array<{ key: string; label: string }> {
  return [
    { key: 'understanding', label: 'Understand' },
    { key: 'drafting', label: 'Draft SQL' },
    { key: 'executing', label: 'Execute' },
    { key: 'complete', label: deep ? 'Deep research' : 'Verify' },
  ];
}

const STAGE_KEYS = stagesFor(false).map((stage) => stage.key);

function stageIndex(stage: string | null | undefined): number {
  if (!stage) return -1;
  return STAGE_KEYS.indexOf(stage);
}

function dedupeTrace(trace: Array<{ kind: string; content: string }>): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const step of trace) {
    const content = step.content.trim();
    if (!content || seen.has(content)) continue;
    seen.add(content);
    out.push(content);
  }
  return out;
}

function ElapsedTicker({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.round((now - startedAt) / 1000));
  // aria-hidden and OUTSIDE every live region (audit 2026-09-21 `genie-v1` /
  // `a11y-06`): a once-a-second text change inside `role="status"` re-queued
  // the whole progress card to screen readers for the full 20-200 s turn.
  return (
    <span className="genie-progress__elapsed mono" aria-hidden="true">
      {seconds}s
    </span>
  );
}

/**
 * Stage-change announcer. Mounts EMPTY and is filled by an effect, because a
 * live region inserted already populated is unreliably spoken; after that the
 * text node only changes when the stage label changes, so a ticking clock or
 * a growing trace never re-announces anything.
 */
function StageAnnouncer({ label }: { label: string }) {
  const [announced, setAnnounced] = useState('');
  useEffect(() => {
    setAnnounced(label);
  }, [label]);
  return (
    <div className="sr-only" role="status" aria-live="polite" data-genie-announcer="progress">
      {announced}
    </div>
  );
}

export function GenieProgress({
  dense = false,
  status = null,
  progress = null,
  startedAt = null,
  announce = true,
}: {
  dense?: boolean;
  /** A raw Genie message status for callers without the live lifecycle. */
  status?: string | null;
  /** Live lifecycle payload from `/api/genie/message/progress`. */
  progress?: GenieLiveProgress | null;
  /** Epoch ms when the ask started; renders the elapsed ticker when set. */
  startedAt?: number | null;
  /** Announce stage CHANGES to screen readers from inside this card. The
   * floating panel passes `false`: its own persistent announcer lives outside
   * the panel so it still speaks while the panel is closed. */
  announce?: boolean;
}) {
  const label = genieProgressLabel(progress, status);
  const stages = stagesFor(Boolean(progress?.deep));
  // Monotonic rail: Genie legitimately revisits earlier statuses (e.g. a
  // text-only repair turn re-enters ASKING_AI after EXECUTING_QUERY), but a
  // completed stage dot must never un-complete on screen. Reset per ask via
  // the startedAt identity.
  //
  // The high-water mark lives in state, not a ref: refs must not be read or
  // written during render (it misbehaves under StrictMode's double render and
  // concurrent re-renders). Rendering max(reported, carried) keeps the rail
  // exact with no one-frame lag — the freshly reported stage is already
  // folded in before the effect commits it — and pinning the carried value to
  // the ask identity means a new ask starts from scratch on its first render.
  const askKey = startedAt ?? null;
  const [seen, setSeen] = useState<{ askKey: number | null; maxIdx: number }>({
    askKey,
    maxIdx: -1,
  });
  const reportedIdx = stageIndex(progress?.stage);
  const carriedIdx = seen.askKey === askKey ? seen.maxIdx : -1;
  const activeIdx = Math.max(reportedIdx, carriedIdx);
  useEffect(() => {
    setSeen((prev) => {
      const base = prev.askKey === askKey ? prev.maxIdx : -1;
      const nextIdx = Math.max(base, reportedIdx);
      if (prev.askKey === askKey && prev.maxIdx === nextIdx) return prev;
      return { askKey, maxIdx: nextIdx };
    });
  }, [askKey, reportedIdx]);
  const trace = dedupeTrace(progress?.reasoning_trace ?? []);
  const sql = progress?.sql_preview?.trim() || null;

  return (
    // No role="status" on the card: status regions are implicitly atomic, so
    // the ticker, the trace and the SQL preview were all re-read on every
    // change. Only <StageAnnouncer> is live.
    <div className={`genie-progress ${dense ? 'genie-progress--dense' : ''}`}>
      {announce ? <StageAnnouncer label={label} /> : null}
      <div className="genie-progress__head">
        <Icon name="sparkle" size={12} className="icon-accent" />
        <span className="genie-progress__label">{label}</span>
        {startedAt != null ? <ElapsedTicker startedAt={startedAt} /> : null}
      </div>

      <ol className="genie-progress__stages" aria-label="Genie lifecycle stages">
        {stages.map((stage, idx) => {
          const state =
            activeIdx < 0
              ? 'pending'
              : idx < activeIdx
                ? 'done'
                : idx === activeIdx
                  ? 'active'
                  : 'pending';
          return (
            <li
              key={stage.key}
              className={`genie-progress__stage is-${state}`}
              aria-current={state === 'active' ? 'step' : undefined}
            >
              <span className="genie-progress__stage-dot" aria-hidden="true">
                {state === 'done' ? <Icon name="check" size={9} /> : null}
              </span>
              <span className="genie-progress__stage-label">{stage.label}</span>
            </li>
          );
        })}
      </ol>

      {trace.length > 0 && (
        <ul className="genie-progress__trace" aria-label="Genie public process steps">
          {trace.map((content) => (
            <li key={content} className="genie-progress__trace-step">
              <Icon name="check" size={10} className="icon-accent" />
              <span>{content}</span>
            </li>
          ))}
        </ul>
      )}

      {sql && (
        <details className="genie-progress__sql" open={!dense}>
          <summary>Generated SQL</summary>
          <pre className="mono">{sql}</pre>
        </details>
      )}
    </div>
  );
}
