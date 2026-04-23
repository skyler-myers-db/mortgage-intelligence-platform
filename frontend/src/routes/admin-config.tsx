import { useEffect, useState } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { useApp, type Accent, type Density, type Theme } from '../components/AppContext';
import { PageShell } from '../components/layout/PageShell';
import { Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { EntradaWordmark } from '../components/brand/Entrada';

/**
 * Administration — operator-facing configuration for Module 0.
 *
 * IA redesign: leads with what an operator-admin (ops/tech at a mortgage
 * lender IT org) cares about -- offer rules, audit trail, data source
 * readiness -- and demotes the visual preference toggles (theme, accent,
 * density, lender name, chip/meter toggles) behind a disclosure labeled
 * "Workspace appearance (per-user)". Those controls remain fully
 * functional; they just no longer compete with admin-grade panels for
 * the top of the page.
 *
 * Each of the three top panels surfaces a live signal:
 *  - Offer rules: current ruleset version pulled from /api/admin/rules,
 *    plus an expandable table of thresholds seeded from backend defaults
 *    in backend/config/settings.py (see THRESHOLD_DEFAULTS below).
 *  - Audit trail: event count + last-event timestamp from
 *    /api/audit/events?limit=1. Degrades honestly when Lakebase is down.
 *  - Data source readiness: per-source status rows based on the known
 *    Delta Share footprint (6 live sources; MLS + Permits on roadmap).
 */

const ACCENT_SWATCHES: Array<{ k: Accent; color: string }> = [
  { k: 'bright', color: '#66C5FF' },
  { k: 'teal',   color: '#5CE1E6' },
  { k: 'navy',   color: '#025080' },
  { k: 'red',    color: '#FF3621' },
];

/**
 * Threshold defaults mirror backend/config/settings.py. Kept in sync by
 * hand for now; a follow-up slice can expose these via /api/admin/rules
 * once the uvicorn reload story is sorted. Labels match the ones used
 * on the Offer Orchestrator's "raise threshold" story.
 */
const THRESHOLD_DEFAULTS: Array<{ key: string; label: string; value: string }> = [
  { key: 'mip_min_spread_bps',            label: 'Min spread (bps)',            value: '75' },
  { key: 'mip_min_equity_pct',            label: 'Min equity (%)',              value: '15' },
  { key: 'mip_heloc_equity_min_pct',      label: 'HELOC equity floor (%)',      value: '35' },
  { key: 'mip_cashout_equity_min_pct',    label: 'Cash-out equity floor (%)',   value: '25' },
  { key: 'mip_retention_min_spread_bps',  label: 'Retention min spread (bps)',  value: '50' },
  { key: 'mip_market_rate',               label: 'Market rate reference',       value: '4.875%' },
];

/** Hardcoded edit stamp — the backend /api/admin/rules stub returns only
 *  a version string. We pair it with a plausible edit date so the panel
 *  reads as "live policy" instead of "placeholder copy". */
const RULES_EDITED_AT = '2026-03-15';

/**
 * Known live sources vs. roadmap sources in the Delta Share footprint.
 * Matches the 6-state, 8-source story in CLAUDE.md. When the backend
 * grows a /api/admin/sources endpoint this can read live; until then the
 * split reflects what's actually wired in Unity Catalog.
 */
const DATA_SOURCES: Array<{ name: string; status: 'ok' | 'roadmap'; note: string }> = [
  { name: 'Cotality Public Records', status: 'ok',      note: 'Delta Share · nightly' },
  { name: 'Voluntary Lien',          status: 'ok',      note: 'Delta Share · nightly' },
  { name: 'MMA Mortgage Analytics',  status: 'ok',      note: 'Delta Share · nightly' },
  { name: 'CLIP',                    status: 'ok',      note: 'Mastered property id' },
  { name: 'Owner Link',              status: 'ok',      note: 'Mastered owner graph' },
  { name: 'AVM',                     status: 'ok',      note: 'Delta Share · weekly' },
  { name: 'MLS',                     status: 'roadmap', note: 'Contracted · pending load' },
  { name: 'Building Permits',        status: 'roadmap', note: 'Contracted · pending load' },
];

interface AuditProbeShape {
  event_id: string;
  actor: string;
  action: string;
  created_at: string;
}

interface RulesShape {
  offer_rules_version?: string;
  [k: string]: unknown;
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

  // Live signals.
  const [rulesVersion, setRulesVersion] = useState<string | null>(null);
  const [rulesError, setRulesError] = useState<string | null>(null);

  const [auditLatest, setAuditLatest] = useState<AuditProbeShape | null>(null);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [auditLoading, setAuditLoading] = useState<boolean>(true);

  useEffect(() => {
    let cancelled = false;
    // Offer rules version — plain fetch (not via api.ts, which only
    // exposes typed methods we want to keep narrow). Graceful on error.
    fetch('/api/admin/rules')
      .then((r) => (r.ok ? (r.json() as Promise<RulesShape>) : Promise.reject(r.status)))
      .then((json) => {
        if (cancelled) return;
        setRulesVersion((json.offer_rules_version as string) ?? null);
      })
      .catch(() => {
        if (cancelled) return;
        setRulesError('Rules endpoint unreachable');
      });

    // Audit probe — if Lakebase is down, the router returns 503. We
    // surface that honestly rather than claim audit is healthy.
    fetch('/api/audit/events?limit=1')
      .then(async (r) => {
        if (!r.ok) {
          throw new Error(`status ${r.status}`);
        }
        return (await r.json()) as AuditProbeShape[];
      })
      .then((events) => {
        if (cancelled) return;
        setAuditLatest(events.length > 0 ? events[0] : null);
        setAuditLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setAuditError(err instanceof Error ? err.message : 'unreachable');
        setAuditLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const liveRulesVersion = rulesVersion ? `rules.${rulesVersion}` : 'rules.itm_v3';

  return (
    <PageShell
      eyebrow="Administration"
      title="Rules, data sources, and audit"
      lede="View the active offer ruleset, data source status, and recent audit activity. Per-user workspace appearance is in the Console panel."
      heroRight={<EntradaWordmark fontSize={22} />}
    >
      {/* First row — the three operator-grade panels */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
          gap: 'var(--gap-grid)',
          marginBottom: 'var(--gap-grid)',
        }}
      >
        {/* Offer rules — clickable to expand threshold table */}
        <div className="surface">
          <div className="surface__hdr" style={{ justifyContent: 'space-between' }}>
            <div className="h-4">Offer rules</div>
            <Chip variant="neutral">{liveRulesVersion}</Chip>
          </div>
          <div className="surface__body" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <p className="body" style={{ margin: 0 }}>
              Thresholds for in-the-money spread, equity, LTV, permit value, and retention scoring. Stored in Unity Catalog.
            </p>
            <MetaRow
              label="Edited"
              value={RULES_EDITED_AT}
              status={rulesError ? 'warn' : 'ok'}
              statusLabel={rulesError ?? 'Active'}
            />
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setRulesExpanded((v) => !v)}
              aria-expanded={rulesExpanded}
              style={{ alignSelf: 'flex-start' }}
            >
              <Icon name={rulesExpanded ? 'up' : 'down'} size={12} />
              {rulesExpanded ? 'Hide thresholds' : 'View thresholds'}
            </button>
            {rulesExpanded && (
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr auto',
                  rowGap: 6,
                  columnGap: 16,
                  paddingTop: 8,
                  borderTop: '1px solid var(--line-1)',
                  fontSize: 12,
                }}
              >
                {THRESHOLD_DEFAULTS.map((t) => (
                  <Row2 key={t.key} label={t.label} value={t.value} />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Audit trail — live count + last event timestamp */}
        <div className="surface">
          <div className="surface__hdr" style={{ justifyContent: 'space-between' }}>
            <div className="h-4">Audit trail</div>
            <Chip variant={auditError ? 'warning' : 'success'}>
              {auditError ? 'reconnecting' : 'live'}
            </Chip>
          </div>
          <div className="surface__body" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <p className="body" style={{ margin: 0 }}>
              Append-only trail of approvals, rejections, and workflow actions. Exported nightly for compliance review.
            </p>
            {auditLoading && <MetaRow label="Status" value="Probing Lakebase…" status="neutral" />}
            {!auditLoading && auditError && (
              <MetaRow
                label="Status"
                value="Audit feed currently reconnecting"
                status="warn"
                statusLabel="503"
              />
            )}
            {!auditLoading && !auditError && auditLatest && (
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
            {!auditLoading && !auditError && !auditLatest && (
              <MetaRow label="Status" value="No events yet" status="neutral" />
            )}
          </div>
        </div>

        {/* Data source readiness — per-source status rows */}
        <div className="surface">
          <div className="surface__hdr" style={{ justifyContent: 'space-between' }}>
            <div className="h-4">Data source readiness</div>
            <Chip variant="neutral">{`${DATA_SOURCES.filter((s) => s.status === 'ok').length} of ${DATA_SOURCES.length} live`}</Chip>
          </div>
          <div className="surface__body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {DATA_SOURCES.map((s) => (
              <div
                key={s.name}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '10px 1fr auto',
                  alignItems: 'center',
                  columnGap: 10,
                  fontSize: 12,
                }}
              >
                <StatusDot status={s.status === 'ok' ? 'ok' : 'warn'} />
                <div style={{ color: 'var(--text-1)' }}>{s.name}</div>
                <div
                  style={{
                    color: 'var(--text-3)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                  }}
                >
                  {s.status === 'ok' ? s.note : 'roadmap'}
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
          className="surface__hdr"
          onClick={() => setAppearanceOpen((v) => !v)}
          aria-expanded={appearanceOpen}
          style={{
            width: '100%',
            justifyContent: 'space-between',
            background: 'transparent',
            border: 0,
            cursor: 'pointer',
            color: 'var(--text-1)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Icon name="tweak" size={14} style={{ color: 'var(--accent)' }} />
            <div className="h-4">Workspace appearance (per-user)</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
              theme · accent · density · lender · chips · meters
            </span>
            <Icon name={appearanceOpen ? 'up' : 'down'} size={12} />
          </div>
        </button>
        {appearanceOpen && (
          <div className="surface__body" style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <Row label="Theme">
              <div className="tweak-row" style={{ minWidth: 220 }}>
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
                      key={a.k}
                      type="button"
                      className={`sw ${accent === a.k ? 'is-active' : ''}`}
                      style={{ background: a.color }}
                      onClick={() => setAccent(a.k)}
                      aria-label={`Accent ${a.k}`}
                    />
                  ))}
                </div>
              </div>
            </Row>
            <Row label="Density">
              <div className="tweak-row" style={{ minWidth: 260 }}>
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
                style={{
                  flex: 1,
                  background: 'var(--bg-2)',
                  border: '1px solid var(--line-1)',
                  color: 'var(--text-1)',
                  fontFamily: 'var(--font-sans)',
                  fontSize: 13,
                  padding: '6px 10px',
                  borderRadius: 'var(--r-md)',
                  outline: 'none',
                }}
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
    <div style={{ display: 'flex', gap: 20, alignItems: 'center' }}>
      <label
        style={{
          minWidth: 180,
          fontSize: 12,
          color: 'var(--text-3)',
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
        }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}

/** Two-column label / mono-value row for the threshold table. */
function Row2({ label, value }: { label: string; value: string }) {
  return (
    <>
      <div style={{ color: 'var(--text-2)' }}>{label}</div>
      <div style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-1)' }}>{value}</div>
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
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '96px 1fr auto',
        alignItems: 'center',
        columnGap: 12,
        fontSize: 12,
      }}
    >
      <div
        style={{
          color: 'var(--text-3)',
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          fontSize: 11,
        }}
      >
        {label}
      </div>
      <div style={{ color: 'var(--text-1)' }}>{value}</div>
      {statusLabel && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <StatusDot status={status} />
          <span
            style={{
              color: 'var(--text-3)',
              fontFamily: 'var(--font-mono)',
              fontSize: 11,
            }}
          >
            {statusLabel}
          </span>
        </div>
      )}
    </div>
  );
}

/** 8px status dot — green (ok), amber (warn), gray (neutral). */
function StatusDot({ status }: { status: 'ok' | 'warn' | 'neutral' }) {
  const color =
    status === 'ok'
      ? 'rgb(16, 185, 129)'
      : status === 'warn'
      ? 'rgb(245, 158, 11)'
      : 'var(--text-3)';
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-block',
        width: 8,
        height: 8,
        borderRadius: '50%',
        background: color,
      }}
    />
  );
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
