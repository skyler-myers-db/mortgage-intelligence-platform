/**
 * The Saved views panel (audit tables-09 phase 2, flow-08 slice 1): the
 * actor's named Lead Queue views, each a link to its query, a delete per
 * view, and "save the current view" under a name.
 *
 * deviation:lead-queue-saved-views. A `.filter-menu`-styled group (not a
 * listbox: it holds links, buttons and a form) anchored to the Saved views
 * pill; the prototype (design_files/index.html:822-838) has presets only.
 *
 * Its own lazy chunk (lead-queue.views.tsx loads it on the first open), so
 * the queue's load path is unchanged. It derives the savable view itself
 * from the queue's own filter and share grammar (lead-queue.filters /
 * lead-queue.request, and the cached config options): a saved view is
 * exactly what Copy link would carry, minus the campaign binding. Measured:
 * with these imports the chunk reaches the same shared modules as the route,
 * so it splits no initial chunk; passing the draft in from the route instead
 * split the initial queryKeys chunk in two (+0.5 KiB br of initial JS).
 * The list read is audit-free; save and delete are pessimistic (nothing
 * reads as saved or deleted before the server's audited write returns).
 * Errors are fixed copy and never echo the name that was typed.
 */
import { useEffect, useEffectEvent, useState, type FormEvent, type RefObject } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import { Icon } from '../components/Icon';
import { Field } from '../components/ui/Field';
import { createSavedView, deleteSavedView, fetchSavedViews } from '../lib/apiClients/savedViews';
import { ApiError } from '../lib/apiTransport';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { pushEscapeLayer } from '../lib/escapeStack';
import { queryKeys } from '../lib/queryKeys';
import type { SavedView } from '../types/leadFilters';
import { leadQueueShareParams } from './lead-queue.filters';
import { leadQueueFilterInputFromSearchParams, leadQueueLenderRefs } from './lead-queue.request';
import './lead-queue.savedViews.css';

export const SAVED_VIEW_NAME_MAX = 60;
/** The campaign binding rides Copy link but never a saved view. */
const CAMPAIGN_BINDING = new Set(['campaign_id', 'variant_name']);

export const SAVED_VIEW_COPY = {
  name: 'Use a name without personal details, emails, phone numbers or IDs.',
  params: 'These filters cannot be saved as a view.',
  duplicate: 'A saved view with this name already exists',
  limit: 'Saved view limit reached (25)',
  changed: 'Saved views changed while saving; try again.',
  unavailable: 'Saved views unavailable. Try again.',
  deleteFailed: 'Could not delete that saved view. Try again.',
} as const;

export interface SavedViewDraft {
  /** The canonical query to save, no leading `?`; '' when nothing is shareable. */
  params: string;
  /** What the view leaves out, in plain words. */
  omitted: string[];
}

/** The current queue as a savable view: the Copy-link grammar minus the campaign binding. */
export function savedViewDraft(searchParams: URLSearchParams, allowedLenderRefs: readonly string[]): SavedViewDraft {
  const share = leadQueueShareParams(searchParams, leadQueueFilterInputFromSearchParams(searchParams, allowedLenderRefs));
  const params = new URLSearchParams(share.search);
  const omitted = [...share.omitted];
  if ([...CAMPAIGN_BINDING].some((key) => params.has(key))) omitted.push('the campaign binding');
  for (const key of CAMPAIGN_BINDING) params.delete(key);
  return { params: params.toString(), omitted };
}

function entries(query: string): string {
  return [...new URLSearchParams(query)]
    .filter(([key]) => !CAMPAIGN_BINDING.has(key))
    .map(([key, value]) => `${key}=${value}`)
    .sort()
    .join('&');
}

/** True when a saved view's canonical entries equal the current share params (order-free). */
export function isCurrentSavedView(savedParams: string, currentParams: string): boolean {
  return entries(savedParams) === entries(currentParams);
}

/** Fixed copy for a failed save; never the server's text, never the name. */
export function saveErrorCopy(error: unknown): string {
  if (error instanceof ApiError && error.status === 422) {
    return error.validationIssues.some((issue) => issue.location.includes('name') || issue.field === 'name')
      ? SAVED_VIEW_COPY.name
      : SAVED_VIEW_COPY.params;
  }
  if (error instanceof ApiError && error.status === 409) {
    if (error.message === SAVED_VIEW_COPY.duplicate) return SAVED_VIEW_COPY.duplicate;
    if (error.message === SAVED_VIEW_COPY.limit) return SAVED_VIEW_COPY.limit;
    return SAVED_VIEW_COPY.changed;
  }
  return SAVED_VIEW_COPY.unavailable;
}

export function LeadQueueSavedViewsPanel({
  id,
  searchParams,
  rootRef,
  onClose,
}: {
  id: string;
  searchParams: URLSearchParams;
  /** The trigger and this panel: a pointer-down outside it closes. */
  rootRef: RefObject<HTMLElement | null>;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const configOptions = useConfigOptionsQuery();
  const draft = savedViewDraft(searchParams, leadQueueLenderRefs(configOptions.data?.target_lender_refs));
  const list = useQuery({
    queryKey: queryKeys.savedViews(),
    queryFn: ({ signal }) => fetchSavedViews(signal),
    staleTime: 30_000,
    retry: false,
  });
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [deleting, setDeleting] = useState<ReadonlySet<string>>(new Set());
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const onEscape = useEffectEvent(() => onClose());
  const onOutside = useEffectEvent((event: PointerEvent) => {
    if (event.target instanceof Node && rootRef.current?.contains(event.target)) return;
    onClose();
  });
  useEffect(() => pushEscapeLayer(() => {
    onEscape();
  }), []);
  useEffect(() => {
    const listener = (event: PointerEvent) => onOutside(event);
    window.addEventListener('pointerdown', listener);
    return () => window.removeEventListener('pointerdown', listener);
  }, []);

  const refetchList = () => queryClient.invalidateQueries({ queryKey: queryKeys.savedViews() });

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (saving || !draft.params || !name.trim()) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    // No `finally`: the React Compiler cannot lower it (react_compiler_coverage).
    try {
      await createSavedView({ name: name.trim(), params: draft.params });
      await refetchList();
      setName('');
      setSaved(true);
    } catch (error) {
      setSaveError(saveErrorCopy(error));
    }
    setSaving(false);
  };

  const remove = async (view: SavedView) => {
    if (deleting.has(view.view_id)) return;
    setDeleting((current) => new Set(current).add(view.view_id));
    setDeleteError(null);
    try {
      await deleteSavedView(view.view_id);
      await refetchList();
    } catch {
      setDeleteError(SAVED_VIEW_COPY.deleteFailed);
    }
    setDeleting((current) => {
      const next = new Set(current);
      next.delete(view.view_id);
      return next;
    });
  };

  const views = list.data?.saved_views ?? [];
  const hint = draft.params
    ? `Saves the filters, sort and columns.${draft.omitted.length > 0 ? ` Left out: ${draft.omitted.join(', ')}.` : ''}`
    : 'Choose a filter, a sort or a column preset to save a view.';
  return (
    <div id={id} className="filter-menu lead-queue-saved-views" role="group" aria-label="Saved views" data-testid="lead-queue-saved-views-panel">
      {list.isPending ? (
        <p className="lead-queue-saved-views__status">Loading saved views…</p>
      ) : list.isError ? (
        <div className="lead-queue-saved-views__status">
          <span>Saved views unavailable</span>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => void list.refetch()}>
            Retry
          </button>
        </div>
      ) : views.length === 0 ? (
        <p className="lead-queue-saved-views__status">No saved views yet.</p>
      ) : (
        <ul className="lead-queue-saved-views__list" aria-label="Your saved views">
          {views.map((view) => {
            const current = isCurrentSavedView(view.params, draft.params);
            const busy = deleting.has(view.view_id);
            return (
              <li key={view.view_id} className="lead-queue-saved-views__item">
                <Link
                  to={{ search: `?${view.params}` }}
                  className={`filter-menu__item lead-queue-saved-views__link${current ? ' is-selected' : ''}`}
                  aria-current={current ? 'true' : undefined}
                  onClick={onClose}
                >
                  {view.name}
                </Link>
                <button
                  type="button"
                  className="filter__remove"
                  aria-label={`Delete saved view: ${view.name}`}
                  aria-disabled={busy || undefined}
                  onClick={() => void remove(view)}
                >
                  <Icon name="cross" size={9} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {deleteError && <p className="field__error">{deleteError}</p>}
      <form className="lead-queue-saved-views__form" onSubmit={(event) => void save(event)}>
        <Field label="View name" hint={hint} error={saveError}>
          {(control) => (
            <input
              {...control}
              className="form-input"
              type="text"
              maxLength={SAVED_VIEW_NAME_MAX}
              autoComplete="off"
              value={name}
              onChange={(event) => {
                setName(event.currentTarget.value);
                setSaveError(null);
                setSaved(false);
              }}
              data-testid="lead-queue-saved-views-name"
            />
          )}
        </Field>
        <button
          type="submit"
          className="btn btn--primary btn--sm"
          disabled={!draft.params || !name.trim() || saving}
          data-testid="lead-queue-saved-views-save"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <span className="sr-only" role="status">{saved ? 'Saved view added.' : ''}</span>
      </form>
    </div>
  );
}
