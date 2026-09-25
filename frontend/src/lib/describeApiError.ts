/**
 * describeApiError — the one buyer-safe vocabulary for a failed request
 * (audit 2026-09-21 `states-04` slice 1, PR #255 follow-on).
 *
 * Routes used to print `error.message`, which carried transport jargon
 * ("500 Internal Server Error", "Failed to fetch") and server detail strings.
 * This maps status, reason and dependency to a title, a body and ONE action,
 * and NEVER interpolates `error.message`, `detail` or a status text. The only
 * interpolations are the caller's subject constant, the friendly dependency
 * name, the 422 validation issue text the filters already show, and the
 * fixed Growth Agent cohort-proof sentences (whitelisted, never passed
 * through).
 *
 * Imported only by route and Console chunks (never by lib/api or the shell),
 * and never by a Genie module: Genie keeps its own governed refusal and error
 * copy (describeApiError.test.ts gates that).
 */
import { ApiError, COHORT_PROOF_MESSAGES, clientFailureReason, isAbortError } from './apiTransport';
import { CLIENT_FAILURE_MESSAGES } from './apiFailure';
import { friendlyDependencyName } from '../components/healthRecovery';

export type ApiErrorKind =
  | 'session_expired'
  | 'offline'
  | 'unreachable'
  | 'unreadable_response'
  | 'forbidden'
  | 'not_found'
  | 'conflict'
  | 'cohort_proof'
  | 'invalid_request'
  | 'rate_limited'
  | 'warming_up'
  | 'breaker_open'
  | 'retries_exhausted'
  | 'dependency_saturated'
  | 'permission_denied'
  | 'server_error'
  | 'aborted'
  | 'unknown';

export type ApiErrorTone = 'danger' | 'warning' | 'neutral';
export type ApiErrorAction = 'retry' | 'clear-filters' | 'reload' | 'none';

interface DescriptionFields {
  tone: ApiErrorTone;
  title: string;
  body: string;
  action: ApiErrorAction;
  /** Support reference, shown as a copyable mono chip; never part of the prose. */
  correlationId: string | null;
  /** The server's wait before another attempt (a 429's countdown). */
  retryAfterMs: number | null;
  /** Internal dependency name ("warehouse"), for telemetry and the banner rule. */
  dependency: string | null;
}

/** Discriminated on `kind`, so a caller can switch on the cause. */
export type ApiErrorDescription = { [K in ApiErrorKind]: DescriptionFields & { kind: K } }[ApiErrorKind];

export interface DescribeContext {
  /** What failed to load, a caller constant: "Ranked borrowers", "Portfolio KPIs". */
  subject: string;
}

const COHORT_PROOF_SENTENCES: ReadonlySet<string> = new Set(Object.values(COHORT_PROOF_MESSAGES));
const COHORT_PROOF_FALLBACK = 'The Growth Agent handoff could not be verified. Run the workflow again before reviewing leads.';
const INVALID_FILTERS_HINT = 'Clear filters or choose a supported filter value.';

/** "Ranked borrowers" at the start of a sentence. */
function sentenceSubject(subject: string): string {
  return subject.charAt(0).toUpperCase() + subject.slice(1);
}

/** "ranked borrowers" inside a sentence; an acronym-led subject ("KPIs") keeps its case. */
function inlineSubject(subject: string): string {
  const firstWord = subject.split(' ', 1)[0] ?? '';
  if (firstWord.length > 1 && firstWord === firstWord.toUpperCase()) return subject;
  return subject.charAt(0).toLowerCase() + subject.slice(1);
}

function build(kind: ApiErrorKind, fields: Omit<DescriptionFields, 'correlationId' | 'retryAfterMs' | 'dependency'>, error: unknown): ApiErrorDescription {
  const api = error instanceof ApiError ? error : null;
  return {
    kind,
    ...fields,
    correlationId: api?.correlationId ?? null,
    retryAfterMs: api?.retryAfterMs ?? null,
    dependency: api?.dependency ?? null,
  } as ApiErrorDescription;
}

function dependencyName(error: ApiError): string {
  return error.dependency ? friendlyDependencyName(error.dependency.toLowerCase()) : 'service';
}

function describeUnavailable(error: ApiError, subject: string): ApiErrorDescription {
  const dep = dependencyName(error);
  const reason = typeof error.reason === 'string' ? error.reason : null;
  if (reason === 'permission_denied') {
    return build('permission_denied', {
      tone: 'danger',
      title: `${sentenceSubject(subject)} isn't available to the app`,
      body: `The ${dep} refused the app access to a required object. An administrator must grant it; retrying will not help.`,
      action: 'none',
    }, error);
  }
  if (reason === 'breaker_open') {
    return build('breaker_open', {
      tone: 'danger',
      title: `The ${dep} is recovering`,
      body: 'Requests paused after repeated failures; it is probed again shortly.',
      action: 'retry',
    }, error);
  }
  if (reason === 'retries_exhausted') {
    return build('retries_exhausted', {
      tone: 'danger',
      title: `The ${dep} is unavailable`,
      body: 'The app already retried; try again shortly.',
      action: 'retry',
    }, error);
  }
  if (reason === 'dependency_saturated') {
    return build('dependency_saturated', {
      tone: 'danger',
      title: `The ${dep} is busy`,
      body: 'Too many requests are running against it; try again shortly.',
      action: 'retry',
    }, error);
  }
  if (error.retryable && (reason === null || reason === 'warming_up')) {
    return build('warming_up', {
      tone: 'danger',
      title: `The ${dep} is still starting`,
      body: 'It is taking longer than usual; try again shortly.',
      action: 'retry',
    }, error);
  }
  return describeServerError(error, subject);
}

function describeServerError(error: unknown, subject: string): ApiErrorDescription {
  return build('server_error', {
    tone: 'danger',
    title: `Couldn't load ${inlineSubject(subject)}`,
    body: 'The server hit an unexpected error.',
    action: 'retry',
  }, error);
}

function describeUnknown(error: unknown, subject: string): ApiErrorDescription {
  return build('unknown', {
    tone: 'danger',
    title: `Couldn't load ${inlineSubject(subject)}`,
    body: 'Something went wrong.',
    action: 'retry',
  }, error);
}

function describeInvalid(error: ApiError, subject: string): ApiErrorDescription {
  const issues = error.validationIssues.map((issue) => `${issue.field}: ${issue.message}`).join('; ');
  return build('invalid_request', {
    tone: 'danger',
    title: `${sentenceSubject(subject)}: filters are invalid`,
    body: issues ? `${issues}. ${INVALID_FILTERS_HINT}` : INVALID_FILTERS_HINT,
    action: 'clear-filters',
  }, error);
}

function describeClientFailure(error: ApiError, subject: string): ApiErrorDescription | null {
  const reason = clientFailureReason(error);
  if (reason === 'session_expired' || (reason === null && error.status === 401)) {
    return build('session_expired', {
      tone: 'warning',
      title: 'Your session ended',
      body: CLIENT_FAILURE_MESSAGES.session_expired,
      action: 'reload',
    }, error);
  }
  if (reason === null) return null;
  return build(reason, {
    tone: 'warning',
    title: reason === 'offline' ? 'You are offline' : `Couldn't load ${inlineSubject(subject)}`,
    body: CLIENT_FAILURE_MESSAGES[reason],
    action: 'retry',
  }, error);
}

export function describeApiError(error: unknown, ctx: DescribeContext): ApiErrorDescription {
  const { subject } = ctx;
  if (isAbortError(error)) {
    return build('aborted', { tone: 'neutral', title: '', body: '', action: 'none' }, error);
  }
  if (!(error instanceof ApiError)) return describeUnknown(error, subject);
  const client = describeClientFailure(error, subject);
  if (client) return client;
  if (error.reason === 'cohort_proof') {
    return build('cohort_proof', {
      tone: 'danger',
      title: "Couldn't verify the Growth Agent cohort",
      body: COHORT_PROOF_SENTENCES.has(error.message) ? error.message : COHORT_PROOF_FALLBACK,
      action: 'none',
    }, error);
  }
  switch (error.status) {
    case 403:
      return build('forbidden', {
        tone: 'danger',
        title: `Your role can't open ${inlineSubject(subject)}`,
        body: 'Ask an administrator for access.',
        action: 'none',
      }, error);
    case 404:
      return build('not_found', {
        tone: 'warning',
        title: `${sentenceSubject(subject)} wasn't found`,
        body: 'It may have been removed, or the link is out of date.',
        action: 'none',
      }, error);
    case 409:
      return build('conflict', {
        tone: 'warning',
        title: `${sentenceSubject(subject)} changed since this page loaded`,
        body: 'Reload to read the current version.',
        action: 'reload',
      }, error);
    case 422:
      return describeInvalid(error, subject);
    case 429:
      return build('rate_limited', {
        tone: 'warning',
        title: 'Too many requests right now',
        body: 'The app is pacing requests.',
        action: 'retry',
      }, error);
    case 503:
      return describeUnavailable(error, subject);
    default:
      return error.status !== null && error.status >= 500
        ? describeServerError(error, subject)
        : describeUnknown(error, subject);
  }
}
