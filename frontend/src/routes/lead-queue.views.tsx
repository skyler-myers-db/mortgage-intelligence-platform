import { Link } from 'react-router';
import { ASSIGNED_TO_ME } from './lead-queue.filters';
import './lead-queue.css';

/**
 * Queue presets (audit tables-09 phase 1): system views that are plain URL
 * presets over params the queue already reads. No new param and never
 * `?view=` (that is the column preset).
 *
 *   All                          none of the three preset params
 *   Pending approval             approval_status=pending
 *   Approved, no outreach >7d    aged_days=7 (the backend AgedDaysParam: approved
 *                                leads aged at least N days with no outreach)
 *   Assigned to me               assigned_to=me, resolved to the signed-in
 *                                actor's email at request time; shown only to a
 *                                listed loan officer (anyone else gets a 422)
 *
 * A preset owns exactly approval_status, aged_days and assigned_to: choosing
 * one sets its key, deletes the other two and a workflow funnel_stage
 * (approved / actioned, which the approval and aging filters replace), keeps
 * every other param (geography, segment, sort, direction, view) and pushes.
 *
 * Markup: a `.filter-row` of `.filter` anchors (design_files/index.html:
 * 822-838). BEM extension: `.lead-queue-views` (the row) and
 * `.lead-queue-views__copy` (its Copy link button), in lead-queue.css.
 */
export type LeadQueuePresetId = 'all' | 'pending' | 'aged' | 'mine';

export const LEAD_QUEUE_PRESET_PARAMS = ['approval_status', 'aged_days', 'assigned_to'] as const;

interface LeadQueuePreset {
  id: LeadQueuePresetId;
  label: string;
  param: readonly [key: (typeof LEAD_QUEUE_PRESET_PARAMS)[number], value: string] | null;
}

export const LEAD_QUEUE_PRESETS: readonly LeadQueuePreset[] = [
  { id: 'all', label: 'All', param: null },
  { id: 'pending', label: 'Pending approval', param: ['approval_status', 'pending'] },
  { id: 'aged', label: 'Approved, no outreach >7d', param: ['aged_days', '7'] },
  { id: 'mine', label: 'Assigned to me', param: ['assigned_to', ASSIGNED_TO_ME] },
];

const WORKFLOW_FUNNEL_STAGES = new Set(['approved', 'actioned']);

export function searchParamsWithPreset(searchParams: URLSearchParams, id: LeadQueuePresetId): URLSearchParams {
  const next = new URLSearchParams(searchParams);
  for (const key of LEAD_QUEUE_PRESET_PARAMS) next.delete(key);
  if (WORKFLOW_FUNNEL_STAGES.has(next.get('funnel_stage')?.trim() ?? '')) next.delete('funnel_stage');
  const preset = LEAD_QUEUE_PRESETS.find((candidate) => candidate.id === id);
  if (preset?.param) next.set(preset.param[0], preset.param[1]);
  return next;
}

/**
 * The preset the URL is showing, or null: a preset is active only when its
 * key holds its value and the other two keys are absent; All when none of
 * the three is present.
 */
export function activeLeadQueuePreset(searchParams: URLSearchParams): LeadQueuePresetId | null {
  const present = LEAD_QUEUE_PRESET_PARAMS.filter((key) => searchParams.has(key));
  if (present.length === 0) return 'all';
  if (present.length > 1) return null;
  const value = searchParams.get(present[0])?.trim().toLowerCase() ?? '';
  return LEAD_QUEUE_PRESETS.find((preset) => preset.param?.[0] === present[0] && preset.param[1] === value)?.id ?? null;
}

export function LeadQueueViews({
  searchParams,
  showAssignedToMe,
  onCopyLink,
}: {
  searchParams: URLSearchParams;
  /** The signed-in actor is a listed loan officer (require_visible_assignee). */
  showAssignedToMe: boolean;
  onCopyLink: () => void;
}) {
  const active = activeLeadQueuePreset(searchParams);
  return (
    <div className="filter-row lead-queue-views" role="group" aria-label="Queue presets" data-testid="lead-queue-presets">
      {LEAD_QUEUE_PRESETS.filter((preset) => preset.id !== 'mine' || showAssignedToMe).map((preset) => {
        const isActive = preset.id === active;
        const search = searchParamsWithPreset(searchParams, preset.id).toString();
        return (
          <Link
            key={preset.id}
            to={{ search: search ? `?${search}` : '' }}
            className={isActive ? 'filter is-active' : 'filter'}
            aria-current={isActive ? 'true' : undefined}
            data-testid={`lead-queue-preset-${preset.id}`}
          >
            {preset.label}
          </Link>
        );
      })}
      <button
        type="button"
        className="btn btn--ghost btn--sm lead-queue-views__copy"
        onClick={onCopyLink}
        data-testid="lead-queue-copy-link"
      >
        Copy link
      </button>
    </div>
  );
}
