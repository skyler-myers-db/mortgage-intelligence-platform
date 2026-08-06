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
  COMPLETED: 'Answer ready — verifying and formatting',
};

export function genieStatusLabel(status: string | null | undefined): string | null {
  if (!status) return null;
  return GENIE_STATUS_LABELS[status] ?? null;
}

/** Ordered lifecycle rail. `stage` values come from the backend's bounded
 * vocabulary; anything unknown ("working") keeps the rail indeterminate. */
const STAGES: Array<{ key: string; label: string }> = [
  { key: 'understanding', label: 'Understand' },
  { key: 'drafting', label: 'Draft SQL' },
  { key: 'executing', label: 'Execute' },
  { key: 'complete', label: 'Format' },
];

function stageIndex(stage: string | null | undefined): number {
  if (!stage) return -1;
  return STAGES.findIndex((s) => s.key === stage);
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
  return (
    <span className="genie-progress__elapsed mono" aria-label={`elapsed ${seconds} seconds`}>
      {seconds}s
    </span>
  );
}

export function GenieProgress({
  dense = false,
  status = null,
  progress = null,
  startedAt = null,
}: {
  dense?: boolean;
  /** A raw Genie message status for callers without the live lifecycle. */
  status?: string | null;
  /** Live lifecycle payload from `/api/genie/message/progress`. */
  progress?: GenieLiveProgress | null;
  /** Epoch ms when the ask started; renders the elapsed ticker when set. */
  startedAt?: number | null;
}) {
  const label =
    progress?.stage_label ?? genieStatusLabel(progress?.status ?? status) ?? INDETERMINATE_LABEL;
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
    <div
      className={`genie-progress ${dense ? 'genie-progress--dense' : ''}`}
      role="status"
      aria-live="polite"
    >
      <div className="genie-progress__head">
        <Icon name="sparkle" size={12} className="icon-accent" />
        <span className="genie-progress__label">{label}</span>
        {startedAt != null ? <ElapsedTicker startedAt={startedAt} /> : null}
      </div>

      <ol className="genie-progress__stages" aria-label="Genie lifecycle stages">
        {STAGES.map((stage, idx) => {
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
