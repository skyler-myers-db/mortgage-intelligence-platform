/**
 * The audit explorer's filter controls and applied-filter chips (audit
 * flow-04 phase 1 / tables-10). The API already accepted actor, a time
 * window and a correlation id; the explorer only offered three free-text
 * boxes. Controls reuse the product's own primitives: `FilterSelect` for the
 * event type (fed by the labels registry, one option per code) and native
 * inputs styled `.form-input`, including native date inputs for the window.
 *
 * The form edits a draft; Apply hands the whole set to the parent, which
 * writes it to the URL. A URL change made elsewhere (a deep link, Back, a
 * chip removal) replaces the draft with the new applied set.
 */
import { useState, type FormEvent } from 'react';
import { Chip } from '../Primitives';
import { FilterSelect } from '../ui/FilterSelect';
import {
  ALL_EVENT_TYPES_OPTION,
  eventTypeCodeForOption,
  eventTypeCodeLabel,
  eventTypeFilterOptions,
} from './AdminAuditExplorer.labels';
import {
  EMPTY_AUDIT_FILTERS,
  auditFilterDraftError,
  auditFiltersKey,
  hasActiveAuditFilters,
  normalizeAuditEntity,
  type AuditExplorerFilters,
} from './AdminAuditExplorer.params';

const ERROR_ID = 'audit-filter-error';

interface FilterFormProps {
  applied: AuditExplorerFilters;
  onApply: (next: AuditExplorerFilters) => void;
}

export function AuditExplorerFilterForm({ applied, onApply }: FilterFormProps) {
  const [draft, setDraft] = useState<AuditExplorerFilters>(applied);
  const [entityError, setEntityError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  // A new applied set from outside the form (a deep link, a chip removal,
  // Back) replaces the draft. Adjusted during render rather than by
  // remounting, so the control that submitted keeps keyboard focus.
  const appliedKey = auditFiltersKey(applied);
  const [syncedKey, setSyncedKey] = useState(appliedKey);
  if (syncedKey !== appliedKey) {
    setSyncedKey(appliedKey);
    setDraft(applied);
    setEntityError(null);
    setFormError(null);
  }
  const edit = (key: keyof AuditExplorerFilters, value: string) => {
    setDraft((current) => ({ ...current, [key]: value }));
    if (key === 'entity') setEntityError(null);
    setFormError(null);
  };
  const draftDirty = hasActiveAuditFilters(draft);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const entity = normalizeAuditEntity(draft.entity);
    if (entity.error) {
      setEntityError(entity.error);
      return;
    }
    const next: AuditExplorerFilters = {
      entity: entity.value,
      action: draft.action.trim(),
      eventType: draft.eventType,
      actor: draft.actor.trim(),
      since: draft.since,
      until: draft.until,
      correlationId: draft.correlationId.trim(),
      // Applying the form leaves the single-row deep link behind: the user is
      // asking a new question of the ledger.
      eventId: '',
    };
    const error = auditFilterDraftError(next);
    if (error) {
      setFormError(error);
      return;
    }
    setDraft(next);
    onApply(next);
  };

  const clear = () => {
    setDraft(EMPTY_AUDIT_FILTERS);
    setEntityError(null);
    setFormError(null);
    onApply(EMPTY_AUDIT_FILTERS);
  };

  const error = entityError ?? formError;
  const eventTypeValue = draft.eventType ? eventTypeCodeLabel(draft.eventType) : ALL_EVENT_TYPES_OPTION;

  return (
    <form className="filter-row filter-row--audit" onSubmit={submit} aria-label="Audit filters">
      <label className="filter-row__group">
        <span className="field__label">ENTITY ID</span>
        <input
          className="form-input"
          value={draft.entity}
          onChange={(event) => edit('entity', event.target.value)}
          placeholder="B-... or approval UUID"
          aria-invalid={Boolean(entityError)}
          aria-describedby={entityError ? ERROR_ID : undefined}
        />
      </label>
      <label className="filter-row__group">
        <span className="field__label">ACTOR</span>
        <input
          className="form-input"
          value={draft.actor}
          onChange={(event) => edit('actor', event.target.value)}
          placeholder="approver@lender.example"
          autoComplete="off"
          spellCheck={false}
        />
      </label>
      <div className="filter-row__group">
        <FilterSelect
          label="Event type"
          value={eventTypeValue}
          options={eventTypeFilterOptions(draft.eventType || null)}
          onChange={(option) => edit('eventType', eventTypeCodeForOption(option, draft.eventType || null) ?? '')}
        />
      </div>
      <label className="filter-row__group">
        <span className="field__label">ACTION</span>
        <input
          className="form-input"
          value={draft.action}
          onChange={(event) => edit('action', event.target.value)}
          placeholder="outreach.approve"
          spellCheck={false}
        />
      </label>
      <label className="filter-row__group filter-row__group--date">
        <span className="field__label">SINCE</span>
        <input
          type="date"
          className="form-input"
          value={draft.since}
          max={draft.until || undefined}
          onChange={(event) => edit('since', event.target.value)}
        />
      </label>
      <label className="filter-row__group filter-row__group--date">
        <span className="field__label">UNTIL</span>
        <input
          type="date"
          className="form-input"
          value={draft.until}
          min={draft.since || undefined}
          onChange={(event) => edit('until', event.target.value)}
        />
      </label>
      <label className="filter-row__group">
        <span className="field__label">CORRELATION ID</span>
        <input
          className="form-input mono"
          value={draft.correlationId}
          onChange={(event) => edit('correlationId', event.target.value)}
          placeholder="request correlation id"
          spellCheck={false}
          aria-invalid={Boolean(formError)}
          aria-describedby={formError ? ERROR_ID : undefined}
        />
      </label>
      <div className="admin-filter-actions">
        <button type="submit" className="btn btn--default btn--sm">
          Apply filters
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={clear}
          disabled={!hasActiveAuditFilters(applied) && !draftDirty}
        >
          Clear
        </button>
      </div>
      {error && (
        <span id={ERROR_ID} className="filter-row__hint filter-row__hint--full text-danger" role="alert">
          {error}
        </span>
      )}
    </form>
  );
}

interface ChipLaneProps {
  applied: AuditExplorerFilters;
  summary: string;
  onRemove: (key: keyof AuditExplorerFilters) => void;
}

const CHIP_LABELS: Record<keyof AuditExplorerFilters, string> = {
  entity: 'entity',
  action: 'action',
  eventType: 'event',
  actor: 'actor',
  since: 'since',
  until: 'until',
  correlationId: 'correlation',
  eventId: 'audit event',
};

function chipValue(key: keyof AuditExplorerFilters, value: string): string {
  return key === 'eventType' ? `${eventTypeCodeLabel(value)} · ${value}` : value;
}

/** The applied filters as removable chips, one per URL parameter. */
export function AuditAppliedFilterChips({ applied, summary, onRemove }: ChipLaneProps) {
  const active = hasActiveAuditFilters(applied);
  const keys = (Object.keys(CHIP_LABELS) as Array<keyof AuditExplorerFilters>).filter((key) => applied[key]);
  return (
    <div
      className={`audit-filter-chip-lane chip-row mt-3 ${active ? '' : 'is-empty'}`}
      aria-label="Applied audit filters"
      aria-hidden={!active}
    >
      {active && (
        <>
          {keys.map((key) => (
            <Chip
              key={key}
              variant="neutral"
              icon={key === 'eventId' ? 'audit' : undefined}
              onRemove={() => onRemove(key)}
              removeLabel={`Remove ${CHIP_LABELS[key]} filter`}
            >
              {CHIP_LABELS[key]} = {chipValue(key, applied[key])}
            </Chip>
          ))}
          <span className="muted fs-12">{summary}</span>
        </>
      )}
    </div>
  );
}
