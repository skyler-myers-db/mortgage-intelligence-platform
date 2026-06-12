import { Link } from 'react-router-dom';
import { Icon } from '../Icon';
import { usePinnedInsights } from '../../lib/pinnedInsights';

/**
 * PinnedInsights (re-audit Buyer-Wow #9) — the Home surface that completes
 * the question → insight → standing artifact loop. Renders the operator's
 * pinned Genie answers (a personal, client-side bookmark list). Renders
 * nothing when there are no pins, so it never adds empty chrome to Home.
 */
export function PinnedInsights() {
  const { pins, unpin } = usePinnedInsights();
  if (pins.length === 0) return null;

  return (
    <section className="pinned-insights" aria-label="Pinned insights">
      <div className="pinned-insights__hdr">
        <div className="pinned-insights__ico"><Icon name="pin" size={14} /></div>
        <div className="pinned-insights__hdr-main">
          <div className="pinned-insights__eyebrow">Pinned insights</div>
          <div className="muted fs-12">Genie answers you pinned for the day.</div>
        </div>
        <Link className="pinned-insights__cta" to="/ask-genie">
          Ask Genie
          <Icon name="chevright" size={13} />
        </Link>
      </div>
      <ul className="pinned-insights__list">
        {pins.map((pin) => (
          <li key={pin.id} className="pinned-insights__item">
            <div className="pinned-insights__item-body">
              <div className="pinned-insights__q">{pin.question}</div>
              <div className="pinned-insights__a">{pin.summary}</div>
              {pin.source && (
                <div className="pinned-insights__src mono">
                  <Icon name="db" size={9} /> {pin.source}
                </div>
              )}
            </div>
            <button
              type="button"
              className="pinned-insights__unpin"
              onClick={() => unpin(pin.id)}
              aria-label={`Unpin: ${pin.question}`}
              title="Unpin"
            >
              <Icon name="cross" size={12} />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
