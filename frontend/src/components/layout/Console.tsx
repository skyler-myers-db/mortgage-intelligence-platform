import { useApp, type Accent, type Density, type Theme } from '../AppContext';
import { Icon } from '../Icon';

/**
 * Console — the right-side tweaks panel from the prototype. Theme, accent,
 * density, evidence / confidence toggles, demo-lender input. Opens from the
 * topbar tweak icon. Uses `.tweaks` BEM from the prototype so a single class
 * controls positioning + animation.
 */

const ACCENT_SWATCHES: Array<{ k: Accent; color: string }> = [
  { k: 'bright', color: '#66C5FF' },
  { k: 'teal',   color: '#5CE1E6' },
  { k: 'navy',   color: '#025080' },
  { k: 'red',    color: '#FF3621' },
];

export function Console() {
  const {
    consoleOpen, setConsoleOpen,
    theme, setTheme,
    accent, setAccent,
    density, setDensity,
    lender, setLender,
    showEvidence, setShowEvidence,
    showConfidence, setShowConfidence,
    setGenieOpen,
  } = useApp();

  return (
    <aside className={`tweaks ${consoleOpen ? 'is-open' : ''}`} role="dialog" aria-label="Console" aria-hidden={!consoleOpen}>
      <div className="tweaks__hdr">
        <Icon name="tweak" size={14} style={{ marginRight: 8, color: 'var(--accent)' }} />
        <div className="tweaks__title">Console</div>
        <button
          className="drawer__close"
          style={{ marginLeft: 'auto' }}
          onClick={() => setConsoleOpen(false)}
          aria-label="Close console"
          type="button"
        >
          <Icon name="close" size={14} />
        </button>
      </div>
      <div className="tweaks__body">
        <div className="tweak-row">
          <label>Theme</label>
          <div className="segmented">
            {(['dark', 'light'] as Theme[]).map((t) => (
              <button
                key={t}
                className={theme === t ? 'is-active' : ''}
                onClick={() => setTheme(t)}
                type="button"
              >
                {t === 'dark' ? 'Dark' : 'Light'}
              </button>
            ))}
          </div>
        </div>
        <div className="tweak-row">
          <label>Accent</label>
          <div className="swatches">
            {ACCENT_SWATCHES.map((a) => (
              <button
                key={a.k}
                className={`sw ${accent === a.k ? 'is-active' : ''}`}
                style={{ background: a.color }}
                onClick={() => setAccent(a.k)}
                aria-label={`Accent ${a.k}`}
                type="button"
              />
            ))}
          </div>
        </div>
        <div className="tweak-row">
          <label>Density</label>
          <div className="segmented">
            {(['comfortable', 'compact'] as Density[]).map((d) => (
              <button
                key={d}
                className={density === d ? 'is-active' : ''}
                onClick={() => setDensity(d)}
                type="button"
              >
                {d === 'comfortable' ? 'Comfortable' : 'Compact'}
              </button>
            ))}
          </div>
        </div>
        <div className="tweak-row">
          <label>Demo lender</label>
          <input type="text" value={lender} onChange={(e) => setLender(e.target.value)} />
        </div>
        <div className="tweak-row">
          <div className="row">
            <label style={{ margin: 0 }}>Show evidence chips</label>
            <button
              className={`switch ${showEvidence ? 'on' : ''}`}
              onClick={() => setShowEvidence(!showEvidence)}
              aria-pressed={showEvidence}
              aria-label="Toggle evidence chips"
              type="button"
            />
          </div>
        </div>
        <div className="tweak-row">
          <div className="row">
            <label style={{ margin: 0 }}>Show confidence meters</label>
            <button
              className={`switch ${showConfidence ? 'on' : ''}`}
              onClick={() => setShowConfidence(!showConfidence)}
              aria-pressed={showConfidence}
              aria-label="Toggle confidence meters"
              type="button"
            />
          </div>
        </div>
        <div className="tweak-row">
          <div className="row">
            <label style={{ margin: 0 }}>Ask Genie</label>
            <button
              className="btn btn--sm"
              onClick={() => { setGenieOpen(true); setConsoleOpen(false); }}
              type="button"
            >
              Open Genie
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}
