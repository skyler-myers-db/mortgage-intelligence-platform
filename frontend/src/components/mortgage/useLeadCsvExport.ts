/**
 * useLeadCsvExport — the audited CSV download (audit tables-08, wave 1a).
 *
 * Order of operations is the contract:
 *   1. build the CSV text once (its bytes are what gets hashed AND downloaded);
 *   2. hash the text and the borrower-id list in the browser;
 *   3. POST the declaration to /api/leads/export-receipt and WAIT;
 *   4. only when the LEAD_EXPORT row exists, hand the same text to the
 *      anchor download and show the audit id next to the button.
 *
 * A refused receipt (422: the declaration did not describe the ids) or any
 * other failure means NO download and a visible error. Nothing here retries
 * a write: the transport retries only the backend's retryable bodies, where
 * no row was written.
 */
import { useEffect, useRef, useState } from 'react';
import { api, ApiError, isAbortError } from '../../lib/api';
import {
  buildLeadExportDeclaration,
  LEAD_EXPORT_DIGEST_MISMATCH_DETAIL,
  type LeadExportReceipt,
} from '../../lib/apiClients/leadExport';
import {
  buildLeadCsv,
  describeLeadCsvExport,
  downloadLeadCsv,
  type LeadCsvExportPlan,
} from './LeadTable.csv';
import type { LeadExportContext } from './LeadTable.types';

export type LeadCsvExportState =
  | { status: 'idle' }
  | { status: 'pending'; rowCount: number }
  /** `notice` is the confirmation strip; null once it has retired. */
  | { status: 'done'; rowCount: number; notice: string | null; receipt: LeadExportReceipt }
  | { status: 'error'; message: string };

export interface LeadCsvExportRequest {
  plan: LeadCsvExportPlan;
  approvals: Record<string, string | undefined>;
  exportContext: LeadExportContext | undefined;
  /** On-screen order, e.g. `rank` or `equity desc`. */
  rowOrder: string;
}

const EXPORT_NOT_DOWNLOADED = 'Nothing was downloaded.';

/**
 * How long the confirmation strip stays up, as before the receipt existed
 * (the sales toast in the same table retires the same way). The receipt
 * line with the audit id is the durable record and stays until the next
 * export.
 */
export const LEAD_EXPORT_NOTICE_MS = 8000;

export function describeLeadExportFailure(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 422) {
      // Only the server's digest check means the receipt and the file
      // disagreed; a schema or metadata-policy 422 refused the request itself.
      return error.message === LEAD_EXPORT_DIGEST_MISMATCH_DETAIL
        ? `Export refused: the audit receipt did not match the file. ${EXPORT_NOT_DOWNLOADED}`
        : `Export refused: the audit ledger would not record this export. ${EXPORT_NOT_DOWNLOADED}`;
    }
    if (error.status === 401 || error.status === 403) {
      return `Export not recorded: your session has no audit identity. ${EXPORT_NOT_DOWNLOADED}`;
    }
    return `Export not recorded: ${error.message}. ${EXPORT_NOT_DOWNLOADED}`;
  }
  if (error instanceof Error && error.message) {
    return `Export not recorded: ${error.message}. ${EXPORT_NOT_DOWNLOADED}`;
  }
  return `Export not recorded. ${EXPORT_NOT_DOWNLOADED}`;
}

/** How long an export waits for the offer-rules version before stamping 'unknown'. */
export const RULES_VERSION_TIMEOUT_MS = 4000;

/**
 * The offer-rules version for this export, or null (stamped 'unknown').
 * Never throws and never blocks past RULES_VERSION_TIMEOUT_MS: the download
 * and its LEAD_EXPORT receipt proceed either way.
 */
export function resolveExportRulesVersion(context: LeadExportContext | undefined): Promise<string | null> {
  const resolve = context?.resolveRulesVersion;
  if (!resolve) return Promise.resolve(context?.rulesVersion ?? null);
  const signal = AbortSignal.timeout(RULES_VERSION_TIMEOUT_MS);
  const timedOut = new Promise<null>((settle) => {
    signal.addEventListener('abort', () => settle(null), { once: true });
  });
  const resolved = new Promise<string | null>((settle) => {
    settle(resolve(signal));
  }).catch(() => null);
  return Promise.race([resolved, timedOut]);
}

export function useLeadCsvExport() {
  const [state, setState] = useState<LeadCsvExportState>({ status: 'idle' });
  // One receipt per click: a second click while the first is in flight would
  // otherwise write a second LEAD_EXPORT row for the same file.
  const inflight = useRef(false);

  // The confirmation strip retires on its own; the receipt line does not.
  useEffect(() => {
    if (state.status !== 'done' || state.notice === null) return undefined;
    const timer = window.setTimeout(() => {
      setState((current) => (current.status === 'done' ? { ...current, notice: null } : current));
    }, LEAD_EXPORT_NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, [state]);

  async function exportCsv({ plan, approvals, exportContext, rowOrder }: LeadCsvExportRequest): Promise<void> {
    if (inflight.current || plan.rows.length === 0) return;
    // Placeholder rows belong to the previous filters: never declare them
    // under the new ones.
    if (exportContext?.exportBlockedReason) return;
    inflight.current = true;
    setState({ status: 'pending', rowCount: plan.rows.length });
    try {
      // Stamped before the bytes are built and hashed: the receipt's digest
      // covers the rules_version line the file carries.
      const rulesVersion = await resolveExportRulesVersion(exportContext);
      const csv = buildLeadCsv(plan.rows, approvals, {
        ...exportContext,
        rulesVersion,
        scope: plan.scope,
        rowOrder,
      });
      const declaration = await buildLeadExportDeclaration(csv, plan, exportContext?.filters);
      const receipt = await api.leadExportReceipt(declaration);
      downloadLeadCsv(csv);
      setState({
        status: 'done',
        rowCount: plan.rows.length,
        notice: describeLeadCsvExport(plan, rowOrder),
        receipt,
      });
    } catch (error) {
      if (isAbortError(error)) {
        setState({ status: 'idle' });
        return;
      }
      setState({ status: 'error', message: describeLeadExportFailure(error) });
    } finally {
      inflight.current = false;
    }
  }

  return { state, exportCsv };
}
