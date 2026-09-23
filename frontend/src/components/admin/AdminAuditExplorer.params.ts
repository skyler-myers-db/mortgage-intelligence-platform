/**
 * Audit explorer URL state (audit flow-04 phase 1 / tables-10, 2026-09-21).
 *
 * The explorer's filters used to live in useState, so no view could be
 * shared, bookmarked or linked to. They now live in the admin-config URL
 * under an `audit_` prefix (the page hosts other panels), parsed and
 * serialized here the way `routes/lead-queue.filters.ts` does it for the
 * Lead Queue: every value is validated on the way in, so a hand-edited URL
 * can only ever narrow the query to something the API accepts.
 *
 * `audit_event_id` is the deep-link parameter every audit id in the product
 * links to (lib/auditLinks.ts, the Decision receipt, the export receipt).
 */
import { AUDIT_CORRELATION_ID_PARAM, AUDIT_EVENT_ID_PARAM } from '../../lib/auditLinks';
import { isAuditEventCode } from './AdminAuditExplorer.labels';

export interface AuditExplorerFilters {
  /** `B-…` borrower reference or another entity id (approval UUID); '' = any. */
  entity: string;
  /** Workflow action string, e.g. `outreach.approve`; '' = any. */
  action: string;
  /** One event-type code, e.g. `LEAD_EXPORT`; '' = any. */
  eventType: string;
  /** Actor principal (staff email); '' = any. */
  actor: string;
  /** Inclusive local calendar days, `YYYY-MM-DD`; '' = open. */
  since: string;
  until: string;
  correlationId: string;
  /** One ledger row (the deep link); '' = none. */
  eventId: string;
}

export const AUDIT_FILTER_PARAMS = {
  entity: 'audit_entity',
  action: 'audit_action',
  eventType: 'audit_event_type',
  actor: 'audit_actor',
  since: 'audit_since',
  until: 'audit_until',
  correlationId: AUDIT_CORRELATION_ID_PARAM,
  eventId: AUDIT_EVENT_ID_PARAM,
} as const satisfies Record<keyof AuditExplorerFilters, string>;

export const EMPTY_AUDIT_FILTERS: AuditExplorerFilters = {
  entity: '',
  action: '',
  eventType: '',
  actor: '',
  since: '',
  until: '',
  correlationId: '',
  eventId: '',
};

const FILTER_KEYS = Object.keys(AUDIT_FILTER_PARAMS) as Array<keyof AuditExplorerFilters>;
const BORROWER_RE = /^B-[A-Z0-9]+$/;
const ENTITY_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const ACTION_RE = /^[a-z][a-z0-9_.-]{0,127}$/i;
const ACTOR_RE = /^[^\s,;<>"']{1,256}$/;
/** Mirrors the backend's `_CORRELATION_ID_PATTERN` (observability.py). */
const CORRELATION_RE = /^[A-Za-z0-9._-]{1,128}$/;
/** Mirrors `AUDIT_EVENT_ID_PATTERN` (backend/services/audit_store_receipt.py). */
const EVENT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,63}$/;
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

export type EntityParse = { value: string; error: null } | { value: ''; error: string };

/** `b-abc` -> `B-ABC`; a malformed `B-` reference is an error, not a guess. */
export function normalizeAuditEntity(raw: string): EntityParse {
  const trimmed = raw.trim();
  if (!trimmed) return { value: '', error: null };
  if (/^b-/i.test(trimmed)) {
    const upper = trimmed.toUpperCase();
    return BORROWER_RE.test(upper)
      ? { value: upper, error: null }
      : { value: '', error: 'Borrower reference must use B- followed by letters and numbers only.' };
  }
  return ENTITY_RE.test(trimmed)
    ? { value: trimmed, error: null }
    : { value: '', error: 'Entity id may use letters, numbers and . _ : - only.' };
}

export function isBorrowerEntity(entity: string): boolean {
  return BORROWER_RE.test(entity);
}

/** A real calendar day in `YYYY-MM-DD` form, or ''. */
export function parseAuditDay(raw: string | null | undefined): string {
  const value = (raw ?? '').trim();
  const match = DATE_RE.exec(value);
  if (!match) return '';
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  const real = date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
  return real ? value : '';
}

function pick(raw: string | null, pattern: RegExp): string {
  const value = (raw ?? '').trim();
  return pattern.test(value) ? value : '';
}

/** Parse the explorer's filters from the page URL, dropping anything malformed. */
export function parseAuditExplorerFilters(params: URLSearchParams): AuditExplorerFilters {
  const entity = normalizeAuditEntity(params.get(AUDIT_FILTER_PARAMS.entity) ?? '');
  const eventType = (params.get(AUDIT_FILTER_PARAMS.eventType) ?? '').trim().toUpperCase();
  return {
    entity: entity.value,
    action: pick(params.get(AUDIT_FILTER_PARAMS.action), ACTION_RE),
    eventType: isAuditEventCode(eventType) ? eventType : '',
    actor: pick(params.get(AUDIT_FILTER_PARAMS.actor), ACTOR_RE),
    since: parseAuditDay(params.get(AUDIT_FILTER_PARAMS.since)),
    until: parseAuditDay(params.get(AUDIT_FILTER_PARAMS.until)),
    correlationId: pick(params.get(AUDIT_FILTER_PARAMS.correlationId), CORRELATION_RE),
    eventId: pick(params.get(AUDIT_FILTER_PARAMS.eventId), EVENT_ID_RE),
  };
}

/**
 * Write the filters into a copy of `base`: every `audit_*` key is replaced,
 * every other parameter on the page is kept, empty filters are omitted.
 */
export function serializeAuditExplorerFilters(
  filters: AuditExplorerFilters,
  base: URLSearchParams = new URLSearchParams(),
): URLSearchParams {
  const next = new URLSearchParams(base);
  for (const key of FILTER_KEYS) next.delete(AUDIT_FILTER_PARAMS[key]);
  for (const key of FILTER_KEYS) {
    const value = filters[key].trim();
    if (value) next.set(AUDIT_FILTER_PARAMS[key], value);
  }
  return next;
}

/** Stable identity of an applied filter set (query and cursor-reset key). */
export function auditFiltersKey(filters: AuditExplorerFilters): string {
  return serializeAuditExplorerFilters(filters).toString();
}

export function hasActiveAuditFilters(filters: AuditExplorerFilters): boolean {
  return FILTER_KEYS.some((key) => filters[key] !== '');
}

/** Local midnight of `day` as an ISO instant; `endOfDay` gives the last millisecond. */
export function auditDayBoundary(day: string, endOfDay: boolean): string | null {
  const match = DATE_RE.exec(day);
  if (!match) return null;
  const [year, month, date] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const start = new Date(year, month - 1, date);
  if (!endOfDay) return start.toISOString();
  return new Date(new Date(year, month - 1, date + 1).getTime() - 1).toISOString();
}

export interface AuditEventPageQuery {
  entity_id: string | null;
  borrower_id: string | null;
  action: string | null;
  event_type: string | null;
  actor: string | null;
  since: string | null;
  until: string | null;
  correlation_id: string | null;
  event_id: string | null;
}

/** The `/api/audit/events/page` filter parameters for an applied filter set. */
export function auditEventPageQuery(filters: AuditExplorerFilters): AuditEventPageQuery {
  const borrower = isBorrowerEntity(filters.entity);
  return {
    entity_id: filters.entity && !borrower ? filters.entity : null,
    borrower_id: filters.entity && borrower ? filters.entity : null,
    action: filters.action || null,
    event_type: filters.eventType || null,
    actor: filters.actor || null,
    since: filters.since ? auditDayBoundary(filters.since, false) : null,
    until: filters.until ? auditDayBoundary(filters.until, true) : null,
    correlation_id: filters.correlationId || null,
    event_id: filters.eventId || null,
  };
}

/** A drafted filter set the form refuses, and the controls at fault. */
export interface AuditFilterError {
  /** The fields to mark `aria-invalid` and point at the message. */
  fields: ReadonlyArray<keyof AuditExplorerFilters>;
  message: string;
}

/** Why a drafted filter set cannot be applied, or null when it can. */
export function auditFilterDraftError(filters: AuditExplorerFilters): AuditFilterError | null {
  // A date input can hold a day the URL parser drops (Chromium accepts a
  // five-digit year); refuse it here rather than let Apply ignore the bound.
  for (const key of ['since', 'until'] as const) {
    if (filters[key] && !parseAuditDay(filters[key])) {
      return { fields: [key], message: `The "${key}" day must be a calendar day written YYYY-MM-DD.` };
    }
  }
  if (filters.since && filters.until && filters.since > filters.until) {
    return { fields: ['since', 'until'], message: 'The "since" day must be on or before the "until" day.' };
  }
  if (filters.actor && !ACTOR_RE.test(filters.actor)) {
    return {
      fields: ['actor'],
      message: 'Actor must be a single principal, e.g. an email address, with no spaces.',
    };
  }
  if (filters.correlationId && !CORRELATION_RE.test(filters.correlationId)) {
    return {
      fields: ['correlationId'],
      message: 'Correlation id may use letters, numbers and . _ - only (up to 128).',
    };
  }
  if (filters.action && !ACTION_RE.test(filters.action)) {
    return { fields: ['action'], message: 'Action must look like outreach.approve (letters, numbers, . _ -).' };
  }
  return null;
}
