import { useState } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { useApp, type Accent, type Density, type Theme } from '../components/AppContext';
import { PageShell } from '../components/layout/PageShell';
import { Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { EntradaWordmark } from '../components/brand/Entrada';
import { api } from '../lib/api';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';

/**
 * Administration — operator-facing configuration for Module 0.
 *
 * Every panel on this page is UC-backed as of the slice13-accuracy
 * follow-up (2026-04-23). No frontend literals pretending to be live
 * data:
 *
 *  - Offer rules + threshold table: GET /api/admin/rules reads
 *    `mip.ref.offer_rules_config`. The "Edited" stamp is the max
 *    last_updated across rows; the version chip is a deterministic
 *    hash of the (key, value) pairs.
 *  - Audit trail: GET /api/audit/events?limit=1 (Lakebase-backed).
 *  - Data source readiness: GET /api/admin/sources returns per-source
 *    rows with status, row counts, and lastModified stamps from
 *    DESCRIBE DETAIL.
 *
 * If a backend call fails we show a muted "temporarily unavailable"
 * message -- never a fabricated literal.
 */

const ACCENT_SWATCHES: Accent[] = ['bright', 'teal', 'navy', 'red'];

// ---------------------------------------------------------------------------
// API shapes
// ---------------------------------------------------------------------------

interface ThresholdRow {
  key: string;
  value: number;
  unit?: string | null;
  label?: string | null;
  description?: string | null;
  sort_order?: number | null;
  last_updated?: string | null;
}

interface RulesResponse {
  offer_rules_version: string;
  rules_edited_at: string | null;
  thresholds: ThresholdRow[];
  legacy_override?: Record<string, string>;
}

type SourceStatus = 'live' | 'roadmap' | 'permission_denied' | 'error';

interface SourceRow {
  name: string;
  status: SourceStatus;
  rows: number | null;
  last_updated: string | null;
  note: string;
}

interface AuditProbeShape {
  event_id: string;
  actor: string;
  action: string;
  created_at: string;
}

/**
 * Format a threshold value for display given its unit hint.
 *   bps            -> integer + " bps"
 *   pct            -> integer + "%"
 *   rate_fraction  -> percentage (0.04875 -> "4.875%")
 *   (anything else) -> the raw numeric string
 */
function formatThresholdValue(t: ThresholdRow): string {
  const unit = (t.unit ?? '').toLowerCase();
  if (unit === 'bps') {
    return `${Math.round(t.value)} bps`;
  }
  if (unit === 'pct') {
    return `${Math.round(t.value)}%`;
  }
  if (unit === 'rate_fraction') {
    return `${(t.value * 100).toFixed(3)}%`;
  }
  return `${t.value}`;
}

export default function AdminConfig() {
  const {
    theme, setTheme,
    accent, setAccent,
    density, setDensity,
    lender, setLender,
    showEvidence, setShowEvidence,
    showConfidence, setShowConfidence,
  } = useApp();

  // Disclosure state for the per-user appearance section. Default closed
  // so the hero panels own the first scroll.
  const [appearanceOpen, setAppearanceOpen] = useState<boolean>(false);
  // Disclosure state for the Offer rules threshold table.
  const [rulesExpanded, setRulesExpanded] = useState<boolean>(false);

  // Per-tile warming-up fetchers. Each tile retries 6 × 5s independently
  // so one cold-starting dependency doesn't block its neighbors.
  const {
    data: rules,
    warmingUp: rulesWarming,
    error: rulesErrorObj,
  } = useWarmingUpRetry<RulesResponse>(
    (signal) => api.adminRules<RulesResponse>(signal),
    [],
  );
  const rulesLoading = rules === null && rulesWarming === null && rulesErrorObj === null;
  const rulesError = rulesErrorObj ? 'Rules endpoint unreachable' : null;

  const {
    data: sources,
    warmingUp: sourcesWarming,
    error: sourcesErrorObj,
  } = useWarmingUpRetry<SourceRow[]>(
    (signal) => api.adminSources<SourceRow[]>(signal),
    [],
  );
  const sourcesLoading =
    sources === null && sourcesWarming === null && sourcesErrorObj === null;
  const sourcesError = sourcesErrorObj
    ? 'Data source readiness temporarily unavailable'
    : null;

  const {
    data: auditEvents,
    warmingUp: auditWarming,
    error: auditErrorObj,
  } = useWarmingUpRetry(
    (signal) => api.auditEvents(1, signal),
    [],
  );
  const auditLoading =
    auditEvents === null && auditWarming === null && auditErrorObj === null;
  const auditError = auditErrorObj
    ? auditErrorObj instanceof Error
      ? auditErrorObj.message
      : 'unreachable'
    : null;
  const auditLatest: AuditProbeShape | null =
    auditEvents && auditEvents.length > 0
      ? (auditEvents[0] as unknown as AuditProbeShape)
      : null;

  // Rules version chip label. When the endpoint is up we render the
  // deterministic hash-based version ("rules.itm_<12hex>"); when it's
  // down we show a muted "unavailable" chip rather than faking it.
  const rulesVersionLabel = rules
    ? `rules.${rules.offer_rules_version}`
    : rulesError
    ? 'rules.unavailable'
    : 'loading…';
  const liveCount = sources ? sources.filter((s) => s.status === 'live').length : 0;
  const totalCount = sources ? sources.length : 0;

  return (
    <PageShell
      eyebrow="Administration"
      title="Rules, data sources, and audit"
      lede="View the active offer ruleset, data source status, and recent audit activity. Per-user workspace appearance is in the Console panel."
      heroRight={<EntradaWordmark height={28} />}
    >
      {/* First row — the three operator-grade panels */}
      <div className="admin-grid">
        {/* Offer rules — clickable to expand threshold table */}
        <div className="surface">
          <div className="surface__hdr surface__hdr--split">
            <div className="h-4">Offer rules</div>
            <Chip variant="neutral">{rulesVersionLabel}</Chip>
          </div>
          <div className="surface__body admin-panel-body">
            <p className="body flush">
              Thresholds for in-the-money spread, equity, LTV, and retention scoring. Stored in Unity Catalog (<span className="mono fs-11">mip.ref.offer_rules_config</span>).
            </p>
            {rulesWarming && (
              <WarmingUpBlock state={rulesWarming} title="Offer rules loading" compact />
            )}
            <MetaRow
              label="Edited"
              value={
                rulesWarming
                  ? 'Warming up…'
                  : rulesLoading
                  ? 'Loading…'
                  : rulesError
                  ? 'Unavailable'
                  : formatEditedAt(rules?.rules_edited_at ?? null)
              }
              status={rulesError ? 'warn' : 'ok'}
              statusLabel={rulesError ?? 'Active'}
            />
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setRulesExpanded((v) => !v)}
              aria-expanded={rulesExpanded}
              disabled={!rules || rules.thresholds.length === 0}
            >
              <Icon name={rulesExpanded ? 'up' : 'down'} size={12} />
              {rulesExpanded ? 'Hide thresholds' : 'View thresholds'}
            </button>
            {rulesExpanded && rules && (
              <div className="admin-thresholds">
                {rules.thresholds.map((t) => (
                  <Row2
                    key={t.key}
                    label={t.label ?? t.key}
                    value={formatThresholdValue(t)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Audit trail — live count + last event timestamp */}
        <div className="surface">
          <div className="surface__hdr surface__hdr--split">
            <div className="h-4">Audit trail</div>
            <Chip variant={auditError ? 'warning' : 'success'}>
              {auditError ? 'reconnecting' : 'live'}
            </Chip>
          </div>
          <div className="surface__body admin-panel-body">
            <p className="body flush">
              Append-only trail of approvals, rejections, and workflow actions. Exported nightly for compliance review.
            </p>
            {auditWarming && (
              <WarmingUpBlock state={auditWarming} title="Audit probe loading" compact />
            )}
            {!auditWarming && auditLoading && (
              <MetaRow label="Status" value="Probing Lakebase…" status="neutral" />
            )}
            {!auditWarming && !auditLoading && auditError && (
              <MetaRow
                label="Status"
                value="Audit feed currently reconnecting"
                status="warn"
                statusLabel="503"
              />
            )}
            {!auditWarming && !auditLoading && !auditError && auditLatest && (
              <>
                <MetaRow
                  label="Last event"
                  value={formatAuditTimestamp(auditLatest.created_at)}
                  status="ok"
                  statusLabel={auditLatest.action}
                />
                <MetaRow label="Last actor" value={auditLatest.actor} status="neutral" />
              </>
            )}
            {!auditWarming && !auditLoading && !auditError && !auditLatest && (
              <MetaRow label="Status" value="No events yet" status="neutral" />
            )}
          </div>
        </div>

        {/* Data source readiness — per-source status rows */}
        <div className="surface">
          <div className="surface__hdr surface__hdr--split">
            <div className="h-4">Data source readiness</div>
            <Chip variant={sourcesError ? 'warning' : 'neutral'}>
              {sourcesWarming
                ? 'warming up…'
                : sourcesError
                ? 'unavailable'
                : sourcesLoading
                ? 'loading…'
                : `${liveCount} of ${totalCount} live`}
            </Chip>
          </div>
          <div className="surface__body surface__body--stack-sm">
            {sourcesWarming && (
              <WarmingUpBlock
                state={sourcesWarming}
                title="Data source readiness loading"
                compact
              />
            )}
            {!sourcesWarming && sourcesLoading && (
              <div className="muted body fs-12">
                Probing Unity Catalog…
              </div>
            )}
            {!sourcesWarming && !sourcesLoading && sourcesError && (
              <div className="muted body fs-12">
                Data source readiness temporarily unavailable. Try again shortly.
              </div>
            )}
            {!sourcesWarming && !sourcesLoading && !sourcesError && sources?.map((s) => (
              <div
                key={s.name}
                className="source-status-row"
              >
                <StatusDot status={sourceStatusTone(s.status)} />
                <div className="source-status-main">
                  {s.name}
                  {s.status === 'live' && s.rows !== null && (
                    <span
                      className="muted source-status-count"
                    >
                      {s.rows.toLocaleString()} rows
                    </span>
                  )}
                </div>
                <div className="source-status-meta">
                  {s.status === 'live'
                    ? formatSourceLastUpdated(s.last_updated) ?? s.note
                    : sourceStatusLabel(s.status)}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Second row — disclosure for per-user appearance controls */}
      <div className="surface">
        <button
          type="button"
          className="surface__hdr appearance-toggle"
          onClick={() => setAppearanceOpen((v) => !v)}
          aria-expanded={appearanceOpen}
        >
          <div className="appearance-toggle__side">
            <Icon name="tweak" size={14} className="icon-accent" />
            <div className="h-4">Workspace appearance (per-user)</div>
          </div>
          <div className="appearance-toggle__side">
            <span className="appearance-toggle__meta">
              theme · accent · density · lender · chips · meters
            </span>
            <Icon name={appearanceOpen ? 'up' : 'down'} size={12} />
          </div>
        </button>
        {appearanceOpen && (
          <div className="surface__body appearance-body">
            <Row label="Theme">
              <div className="tweak-row tweak-row--theme">
                <div className="segmented">
                  {(['dark', 'light'] as Theme[]).map((t) => (
                    <button
                      key={t}
                      type="button"
                      className={theme === t ? 'is-active' : ''}
                      onClick={() => setTheme(t)}
                    >
                      {t === 'dark' ? 'Dark' : 'Light'}
                    </button>
                  ))}
                </div>
              </div>
            </Row>
            <Row label="Accent">
              <div className="tweak-row">
                <div className="swatches">
                  {ACCENT_SWATCHES.map((a) => (
                    <button
                      key={a}
                      type="button"
                      className={`sw sw--${a} ${accent === a ? 'is-active' : ''}`}
                      onClick={() => setAccent(a)}
                      aria-label={`Accent ${a}`}
                    />
                  ))}
                </div>
              </div>
            </Row>
            <Row label="Density">
              <div className="tweak-row tweak-row--density">
                <div className="segmented">
                  {(['comfortable', 'compact'] as Density[]).map((d) => (
                    <button
                      key={d}
                      type="button"
                      className={density === d ? 'is-active' : ''}
                      onClick={() => setDensity(d)}
                    >
                      {d === 'comfortable' ? 'Comfortable' : 'Compact'}
                    </button>
                  ))}
                </div>
              </div>
            </Row>
            <Row label="Lender">
              <input
                type="text"
                aria-label="Lender"
                value={lender}
                onChange={(e) => setLender(e.target.value)}
                className="form-input"
              />
            </Row>
            <Row label="Show evidence chips">
              <button
                className={`switch ${showEvidence ? 'on' : ''}`}
                onClick={() => setShowEvidence(!showEvidence)}
                aria-pressed={showEvidence}
                aria-label="Toggle evidence chips"
                type="button"
              />
            </Row>
            <Row label="Show confidence meters">
              <button
                className={`switch ${showConfidence ? 'on' : ''}`}
                onClick={() => setShowConfidence(!showConfidence)}
                aria-pressed={showConfidence}
                aria-label="Toggle confidence meters"
                type="button"
              />
            </Row>
          </div>
        )}
      </div>
    </PageShell>
  );
}

function Row({ label, children }: { label: string; children: ReactElement }) {
  return (
    <div className="admin-row">
      <label className="admin-row__label">{label}</label>
      {children}
    </div>
  );
}

/** Two-column label / mono-value row for the threshold table. */
function Row2({ label, value }: { label: string; value: string }) {
  return (
    <>
      <div className="row2__label">{label}</div>
      <div className="row2__value">{value}</div>
    </>
  );
}

/** Meta row with a status dot for the three top panels. */
function MetaRow({
  label,
  value,
  status,
  statusLabel,
}: {
  label: string;
  value: ReactNode;
  status: 'ok' | 'warn' | 'neutral';
  statusLabel?: string;
}) {
  return (
    <div className="meta-row">
      <div className="meta-row__label">{label}</div>
      <div className="meta-row__value">{value}</div>
      {statusLabel && (
        <div className="meta-row__status">
          <StatusDot status={status} />
          <span className="meta-row__status-label">{statusLabel}</span>
        </div>
      )}
    </div>
  );
}

/** 8px status dot — green (ok), amber (warn), gray (neutral). */
function StatusDot({ status }: { status: 'ok' | 'warn' | 'neutral' | 'error' }) {
  return (
    <span
      aria-hidden
      className={`status-dot status-dot--${status}`}
    />
  );
}

function sourceStatusTone(status: SourceStatus): 'ok' | 'warn' | 'error' {
  if (status === 'live') return 'ok';
  if (status === 'permission_denied' || status === 'error') return 'error';
  return 'warn';
}

function sourceStatusLabel(status: SourceStatus): string {
  if (status === 'permission_denied') return 'grant needed';
  if (status === 'error') return 'read error';
  return 'roadmap';
}

/** Best-effort ISO-timestamp prettifier. Lakebase returns ISO-8601 strings. */
function formatAuditTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`;
  } catch {
    return iso;
  }
}

/**
 * Rules `rules_edited_at` comes back as the Databricks CAST(timestamp AS
 * STRING) form ("YYYY-MM-DD HH:MM:SS" in UTC). Re-render in the user's
 * local locale.
 */
function formatEditedAt(stamp: string | null): string {
  if (!stamp) return 'Never';
  // Databricks' CAST(timestamp AS STRING) uses a space separator; Date
  // parses ISO-8601 reliably, so coerce to that form first.
  const iso = stamp.includes('T') ? stamp : stamp.replace(' ', 'T') + 'Z';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return stamp;
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return stamp;
  }
}

/** Pretty-print a DESCRIBE DETAIL lastModified stamp (ISO-8601). */
function formatSourceLastUpdated(iso: string | null): string | null {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch {
    return null;
  }
}
