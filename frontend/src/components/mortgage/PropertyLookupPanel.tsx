import { useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { ApiError, api } from '../../lib/api';
import type { PropertyLoanLookupResponse } from '../../types';
import { segmentName } from '../../lib/segmentMetadata';
import { Button, Chip } from '../Primitives';
import { Icon } from '../Icon';
import { ScoreBadge } from './ScoreBadge';
import { formatUsd, ratePct } from '../../lib/formatters';
import { SurfaceTitle } from '../ui/SurfaceTitle';

/**
 * PropertyLookupPanel — the visible app-UI consumer of the governed
 * `POST /api/v1/lookup/property-loan` endpoint (closes the external-audit
 * "no visible UI consumer" blocker).
 *
 * The operator types a street address + ZIP (optionally city/state) and the
 * panel resolves it to a masked CLIP + Owner Link, loan facts, lead score,
 * segments, and a deep link into the governed borrower dossier.
 *
 * Honesty / governance rules baked in here:
 *   - The submitted address lives in component state ONLY. It is never
 *     written to localStorage, the URL, or any query param, and the response
 *     never echoes it — so the result region never renders the address.
 *   - Every lookup is audited server-side; the audit id is surfaced as a
 *     caption on both the match and no-match result (the audit lands even
 *     when nothing matched).
 *   - Boundary is stated plainly: exact address+ZIP matching against the
 *     refreshed Cotality share, NOT fuzzy CLIP mastering.
 */

type LookupState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'done'; result: PropertyLoanLookupResponse }
  | { status: 'validation'; message: string }
  | { status: 'degraded'; dependency: string | null }
  | { status: 'error'; message: string };

function isZip5(value: string): boolean {
  return /^[0-9]{5}$/.test(value.trim());
}

export interface PropertyLookupPanelProps {
  /** Fires when an internal navigation link is followed (e.g. Console closes). */
  onNavigate?: () => void;
  /** Compact chrome for the narrow Console rail (drops the sub-header copy). */
  compact?: boolean;
  /** Title heading level (a11y-03): 2 on a page (default), 3 inside a Console group. */
  headingLevel?: 2 | 3;
}

export function PropertyLookupPanel({ onNavigate, compact, headingLevel = 2 }: PropertyLookupPanelProps) {
  const [addressLine, setAddressLine] = useState('');
  const [zip5, setZip5] = useState('');
  const [city, setCity] = useState('');
  const [state, setState] = useState('');
  const [lookup, setLookup] = useState<LookupState>({ status: 'idle' });

  const canSubmit =
    addressLine.trim().length >= 4 &&
    isZip5(zip5) &&
    lookup.status !== 'loading';

  async function runLookup() {
    setLookup({ status: 'loading' });
    try {
      const result = await api.propertyLookup({
        address_line: addressLine.trim(),
        zip5: zip5.trim(),
        city: city.trim() || null,
        state: state.trim().toUpperCase() || null,
      });
      setLookup({ status: 'done', result });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422) {
          setLookup({ status: 'validation', message: err.message });
          return;
        }
        if (err.status === 503) {
          setLookup({ status: 'degraded', dependency: err.dependency });
          return;
        }
        setLookup({ status: 'error', message: err.message });
        return;
      }
      setLookup({
        status: 'error',
        message: err instanceof Error ? err.message : 'Lookup failed.',
      });
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;
    void runLookup();
  }

  return (
    <section
      // `--compact` lets the header wrap its audit chip and stacks the field
      // grid so the panel fits the Console's ~266px track (09-console-and-layout.css).
      className={`surface property-lookup${compact ? ' property-lookup--compact' : ''}`}
      aria-busy={lookup.status === 'loading'}
    >
      <div className="surface__hdr">
        <Icon name="search" size={14} className="icon-accent" />
        <div>
          <SurfaceTitle level={headingLevel}>Property lookup</SurfaceTitle>
          {!compact && (
            <div className="muted fs-12">
              Resolve a street address + ZIP to its masked CLIP, loan facts, and governed dossier.
            </div>
          )}
        </div>
        <div className="spacer" />
        <Chip variant="neutral" icon="audit">Every lookup audited</Chip>
      </div>
      <div className="surface__body surface__body--stack-sm">
        <form className="stack-sm" onSubmit={onSubmit}>
          <label className="stack-sm">
            <span className="field__label">Street address</span>
            <input
              className="form-input"
              aria-label="Property lookup — street address"
              autoComplete="off"
              placeholder="123 Main St"
              value={addressLine}
              onChange={(event) => setAddressLine(event.target.value)}
            />
          </label>
          <div className="field-grid">
            <label className="stack-sm">
              <span className="field__label">ZIP</span>
              <input
                className="form-input"
                aria-label="Property lookup — ZIP"
                inputMode="numeric"
                autoComplete="off"
                placeholder="60614"
                maxLength={5}
                value={zip5}
                onChange={(event) => setZip5(event.target.value.replace(/[^0-9]/g, ''))}
              />
            </label>
            <label className="stack-sm">
              <span className="field__label">State (optional)</span>
              <input
                className="form-input"
                aria-label="Property lookup — state"
                autoComplete="off"
                placeholder="IL"
                maxLength={2}
                value={state}
                onChange={(event) => setState(event.target.value.replace(/[^A-Za-z]/g, ''))}
              />
            </label>
          </div>
          <label className="stack-sm">
            <span className="field__label">City (optional)</span>
            <input
              className="form-input"
              aria-label="Property lookup — city"
              autoComplete="off"
              placeholder="Chicago"
              value={city}
              onChange={(event) => setCity(event.target.value)}
            />
          </label>
          <div className="section-actions">
            <Button
              type="submit"
              variant="primary"
              icon="search"
              disabled={!canSubmit}
            >
              {lookup.status === 'loading' ? 'Looking up…' : 'Look up property'}
            </Button>
          </div>
          <div className="muted fs-11">
            Exact address + ZIP match against the refreshed Cotality share. Boundary: not fuzzy CLIP mastering.
          </div>
        </form>

        {lookup.status === 'validation' && (
          <div className="status-callout status-callout--danger" role="alert">
            {lookup.message}
          </div>
        )}

        {lookup.status === 'degraded' && (
          <div className="status-callout status-callout--danger" role="alert">
            <span>
              A data dependency
              {lookup.dependency ? ` (${lookup.dependency})` : ''} is warming up or unavailable. The lookup was not run.
            </span>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => void runLookup()}
              aria-label="Retry property lookup"
            >
              Retry
            </button>
          </div>
        )}

        {lookup.status === 'error' && (
          <div className="status-callout status-callout--danger" role="alert">
            <span>Couldn't complete the lookup: {lookup.message}</span>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => void runLookup()}
              aria-label="Retry property lookup"
            >
              Retry
            </button>
          </div>
        )}

        {lookup.status === 'done' && (
          <PropertyLookupResult result={lookup.result} onNavigate={onNavigate} />
        )}
      </div>
    </section>
  );
}

function PropertyLookupResult({
  result,
  onNavigate,
}: {
  result: PropertyLoanLookupResponse;
  onNavigate?: () => void;
}) {
  const loan = result.loan;
  const segments = result.segment ?? [];
  return (
    <div
      className="surface surface--inset stack-sm"
      role="status"
      aria-live="polite"
      data-testid="property-lookup-result"
    >
      <div className="surface__body surface__body--stack-sm">
        {result.matched ? (
          <>
            <div className="chip-row">
              <Chip variant="success" icon="check">Exact match</Chip>
              {result.clip_ref && (
                <Chip variant="neutral" icon="db">
                  <span className="mono">CLIP {result.clip_ref}</span>
                </Chip>
              )}
              {result.owner_link_ref && (
                <Chip variant="neutral" icon="link">
                  <span className="mono">Owner Link {result.owner_link_ref}</span>
                </Chip>
              )}
              {typeof result.lead_score === 'number' && <ScoreBadge value={result.lead_score} />}
            </div>

            {segments.length > 0 && (
              <div className="chip-row">
                {segments.map((code) => (
                  <Chip key={code} variant="neutral" icon="tag">{segmentName(code)}</Chip>
                ))}
              </div>
            )}

            {loan && (
              <div className="field-grid">
                {loan.lender_brand && (
                  <div>
                    <div className="field__label">Servicing lender</div>
                    <div className="field__value">{loan.lender_brand}</div>
                  </div>
                )}
                <div>
                  <div className="field__label">Current rate</div>
                  <div className="field__value mono">{ratePct(loan.current_rate)}</div>
                </div>
                <div>
                  <div className="field__label">Lien balance</div>
                  <div className="field__value mono">{formatUsd(loan.current_lien_balance)}</div>
                </div>
                <div>
                  <div className="field__label">LTV</div>
                  <div className="field__value mono">{loan.ltv}%</div>
                </div>
                <div>
                  <div className="field__label">Open lien</div>
                  <div className="field__value">
                    {loan.has_open_lien ? 'Yes' : 'No'}
                  </div>
                </div>
              </div>
            )}

            {result.dossier_path && (
              <div className="section-actions">
                <Link
                  className="btn btn--primary btn--sm"
                  to={result.dossier_path}
                  onClick={onNavigate}
                >
                  <Icon name="doc" size={14} />
                  Open dossier
                </Link>
              </div>
            )}
          </>
        ) : (
          <div className="stack-sm">
            <div className="chip-row">
              <Chip variant="warning" icon="info">No exact match</Chip>
            </div>
            <p className="muted fs-12">
              No exact match in the refreshed coverage — exact address+ZIP matching; fuzzy mastering is Cotality CLIP resolution.
            </p>
          </div>
        )}

        <div className="muted fs-11" data-testid="property-lookup-audit">
          Lookup audited · {result.audit_event_id}
        </div>
      </div>
    </div>
  );
}
