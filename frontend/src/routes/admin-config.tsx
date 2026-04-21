import type { ReactElement } from 'react';
import { useApp, type Accent, type Density, type Theme } from '../components/AppContext';
import { PageShell } from '../components/layout/PageShell';
import { Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';

/**
 * Admin / Config — presenter-facing controls for theme / density / accent
 * (the same Console controls, surfaced as a proper settings page so a demo
 * presenter can flex visuals without opening the right-rail panel), plus
 * placeholders for offer rules and audit settings.
 */

const ACCENT_SWATCHES: Array<{ k: Accent; color: string }> = [
  { k: 'bright', color: '#66C5FF' },
  { k: 'teal',   color: '#5CE1E6' },
  { k: 'navy',   color: '#025080' },
  { k: 'red',    color: '#FF3621' },
];

export default function AdminConfig() {
  const {
    theme, setTheme,
    accent, setAccent,
    density, setDensity,
    lender, setLender,
    showEvidence, setShowEvidence,
    showConfidence, setShowConfidence,
  } = useApp();

  return (
    <PageShell
      eyebrow="Admin Config"
      title="Rules, thresholds, and presentation"
      lede="Presenter controls at the top; rule and audit configuration placeholders below. Everything here is a safe mutation of UI state."
    >
      <div className="layoutA-grid">
        <div className="surface">
          <div className="surface__hdr">
            <Icon name="tweak" size={14} style={{ color: 'var(--accent)' }} />
            <div className="h-4">Presentation controls</div>
          </div>
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
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--gap-grid)' }}>
          <PlaceholderBlock
            title="Offer rules"
            desc="Thresholds for In-the-Money spread, equity, LTV, permit value, and retention scoring. Version-controlled in Unity Catalog."
            chip="rules.itm_v3"
          />
          <PlaceholderBlock
            title="Audit settings"
            desc="Lakebase schema `mip_app.audit_events` · append-only · exported nightly to UC for compliance review."
            chip="mip_app.audit_events"
          />
          <PlaceholderBlock
            title="Data source readiness"
            desc="Public Records · Voluntary Lien · MMA · CLIP · Owner Link · MLS · Building Permits · AVM — all wired via Delta Share in production."
            chip="8 sources · Delta Share"
          />
        </div>
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

function PlaceholderBlock({ title, desc, chip }: { title: string; desc: string; chip: string }) {
  return (
    <div className="surface">
      <div className="surface__hdr" style={{ justifyContent: 'space-between' }}>
        <div className="h-4">{title}</div>
        <Chip variant="neutral">{chip}</Chip>
      </div>
      <div className="surface__body">
        <p className="body" style={{ margin: 0 }}>{desc}</p>
      </div>
    </div>
  );
}
