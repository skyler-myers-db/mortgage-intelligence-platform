import { Link } from 'react-router-dom';
import { useApp, type Accent, type Density, type Theme } from '../AppContext';
import { Icon } from '../Icon';
import { Chip } from '../Primitives';
import { PropertyLookupPanel } from '../mortgage/PropertyLookupPanel';
import { offerDisplayLabel } from '../../lib/offerLanguage';

/**
 * Console — the right-side tweaks panel from the prototype. Theme, accent,
 * density, evidence / signal-strength toggles, configured tenant. Opens from the
 * topbar tweak icon. Uses `.tweaks` BEM from the prototype so a single class
 * controls positioning + animation.
 */

const ACCENT_SWATCHES: Accent[] = ['bright', 'teal', 'navy', 'red'];

export function Console() {
  const {
    consoleOpen, setConsoleOpen,
    theme, setTheme,
    accent, setAccent,
    density, setDensity,
    lender,
    showEvidence, setShowEvidence,
    showConfidence, setShowConfidence,
    setGenieOpen,
    savedLeads,
    savedDrafts,
    workspaceStatus,
    workspaceError,
    refreshWorkspace,
  } = useApp();
  const savedLeadItems = Object.values(savedLeads)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 4);
  const savedDraftItems = Object.values(savedDrafts)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 3);
  const savedLeadCount = Object.keys(savedLeads).length;
  const savedDraftCount = Object.keys(savedDrafts).length;

  return (
    <aside
      id="workspace-console"
      className={`tweaks ${consoleOpen ? 'is-open' : ''}`}
      role="complementary"
      aria-label="Workspace console"
      aria-hidden={!consoleOpen}
    >
      <div className="tweaks__hdr">
        <Icon name="tweak" size={14} className="tweaks__hdr-icon" />
        <div className="tweaks__title">Console</div>
        <button
          className="drawer__close"
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
          <div className="segmented" role="group" aria-label="Theme">
            {(['dark', 'light'] as Theme[]).map((t) => (
              <button
                key={t}
                className={theme === t ? 'is-active' : ''}
                onClick={() => setTheme(t)}
                type="button"
                aria-pressed={theme === t}
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
                key={a}
                className={`sw sw--${a} ${accent === a ? 'is-active' : ''}`}
                onClick={() => setAccent(a)}
                aria-label={`Accent ${a}`}
                type="button"
                aria-pressed={accent === a}
              />
            ))}
          </div>
        </div>
        <div className="tweak-row">
          <label>Density</label>
          <div className="segmented" role="group" aria-label="Density">
            {(['comfortable', 'compact'] as Density[]).map((d) => (
              <button
                key={d}
                className={density === d ? 'is-active' : ''}
                onClick={() => setDensity(d)}
                type="button"
                aria-pressed={density === d}
              >
                {d === 'comfortable' ? 'Comfortable' : 'Compact'}
              </button>
            ))}
          </div>
        </div>
        <div className="tweak-row">
          <label>Property lookup</label>
          <PropertyLookupPanel compact onNavigate={() => setConsoleOpen(false)} />
        </div>
        <div className="tweak-row">
          <label>Configured tenant</label>
          <div className="stack-sm">
            <Chip variant="neutral" icon="building">{lender}</Chip>
            <div className="muted fs-12">
              Read-only in Module 0; lender configuration is applied server-side.
            </div>
          </div>
        </div>
        <div className="tweak-row">
          <label>Saved workspace</label>
          <div className="saved-workspace">
            <div className="saved-workspace__summary">
              <span>{savedLeadCount} saved lead{savedLeadCount === 1 ? '' : 's'}</span>
              <span>{savedDraftCount} draft{savedDraftCount === 1 ? '' : 's'}</span>
            </div>
            {workspaceStatus === 'loading' && (
              <div className="muted fs-12">Loading Lakebase workspace…</div>
            )}
            {workspaceError && (
              <div className="stack-sm">
                <div className="text-danger fs-12">{workspaceError}</div>
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={refreshWorkspace}
                >
                  Retry workspace
                </button>
              </div>
            )}
            {workspaceStatus !== 'loading' && savedLeadItems.length === 0 && savedDraftItems.length === 0 && (
              <div className="muted fs-12">No saved leads or drafts yet.</div>
            )}
            {savedLeadItems.map((lead) => (
              <Link
                key={`lead-${lead.borrower_id}`}
                className="saved-workspace__item"
                to={`/borrower-360/${lead.borrower_id}`}
                onClick={() => setConsoleOpen(false)}
              >
                <Icon name="tag" size={12} />
                <span className="saved-workspace__body">
                  <span className="mono">{lead.borrower_id}</span>
                  <span>{lead.city}, {lead.state} · {offerDisplayLabel(null, lead.recommended_offer)}</span>
                </span>
              </Link>
            ))}
            {savedDraftItems.map((draft) => (
              <Link
                key={`draft-${draft.borrower_id}-${draft.channel}`}
                className="saved-workspace__item"
                to={`/offer-orchestrator/${draft.borrower_id}`}
                onClick={() => setConsoleOpen(false)}
              >
                <Icon name="doc" size={12} />
                <span className="saved-workspace__body">
                  <span className="mono">{draft.borrower_id}</span>
                  <span>Draft saved · {draft.channel}</span>
                </span>
              </Link>
            ))}
          </div>
        </div>
        <div className="tweak-row">
          <div className="row">
            <label>Show evidence chips</label>
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
            <label>Show signal meters</label>
            <button
              className={`switch ${showConfidence ? 'on' : ''}`}
              onClick={() => setShowConfidence(!showConfidence)}
              aria-pressed={showConfidence}
              aria-label="Toggle signal strength meters"
              type="button"
            />
          </div>
        </div>
        <div className="tweak-row">
          <div className="row">
            <label>Ask Genie</label>
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
