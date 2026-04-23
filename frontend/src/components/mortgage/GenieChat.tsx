import { useEffect, useRef, useState } from 'react';
import { useApp } from '../AppContext';
import { api } from '../../lib/api';
import type { GenieAnswer as GenieAnswerShape } from '../../types';
import { Icon } from '../Icon';
import { Button, EvidenceChip } from '../Primitives';
import { GenieAnswer } from './GenieAnswer';
import { DRAWER_SOURCES } from '../../lib/drawerSources';

/**
 * Map a free-form trusted-asset string (UC path or ruleset id) to the
 * best-fitting drawer entry. Falls through to NBO as a "something is
 * better than nothing" default so a chip click always opens a drawer.
 */
function drawerForAsset(asset: string) {
  if (/itm|rules/i.test(asset)) return DRAWER_SOURCES.itm;
  if (/permit/i.test(asset)) return DRAWER_SOURCES.permit;
  if (/lead_population|population/i.test(asset)) return DRAWER_SOURCES.population;
  if (/config/i.test(asset)) return DRAWER_SOURCES.config;
  return DRAWER_SOURCES.nbo;
}

/**
 * Floating Genie chat panel — `.genie` BEM from the prototype. Fixed
 * bottom-right, reachable from every page. Falls back to api.genie() which
 * already has a deterministic fallback wired. A floating `.genie__fab` is
 * shown when the panel is closed (bottom-right sparkle) so one click anywhere
 * in the app reaches Genie.
 *
 * The AI message shape now holds the full GenieAnswer payload so
 * metric_value / table_rows / follow_up_questions all render in the bubble
 * via the shared <GenieAnswer> subcomponent.
 */

type ChatMsg =
  | { who: 'user'; text: string }
  | { who: 'ai'; payload: GenieAnswerShape; sources?: string[] };

const SAMPLE_QUESTIONS = [
  'How many in-the-money borrowers in Travis County?',
  'Top 10 borrowers by score',
  'Show HELOC candidates by state.',
];

export function GenieChat() {
  const { genieOpen, setGenieOpen, lender } = useApp();
  const [msgs, setMsgs] = useState<ChatMsg[]>([
    {
      who: 'ai',
      payload: {
        answer: `Ask about ${lender}'s borrower segments, triggers, or evidence. Answers run against Unity Catalog metric views.`,
        trusted_assets: ['UC.metrics'],
      },
      sources: ['UC.metrics'],
    },
  ]);
  const [input, setInput] = useState('');
  const [typing, setTyping] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [msgs, typing, genieOpen]);

  const ask = async (q: string) => {
    if (!q.trim()) return;
    setMsgs((m) => [...m, { who: 'user', text: q }]);
    setInput('');
    setTyping(true);
    try {
      const res = (await api.genie(q)) as GenieAnswerShape;
      const trusted = res.trusted_assets ?? [];
      setMsgs((m) => [...m, { who: 'ai', payload: res, sources: trusted.slice(0, 4) }]);
    } finally {
      setTyping(false);
    }
  };

  return (
    <>
      <button
        className={`genie__fab ${genieOpen ? 'is-hidden' : ''}`}
        onClick={() => setGenieOpen(true)}
        aria-label="Open Genie"
        type="button"
      >
        <Icon name="sparkle" size={22} />
      </button>
      <div className={`genie ${genieOpen ? 'is-open' : ''}`} role="dialog" aria-label="Genie chat" aria-hidden={!genieOpen}>
        <div className="genie__hdr">
          <div className="genie__avatar" />
          <div style={{ flex: 1 }}>
            <div className="genie__title">Ask Genie</div>
            <div className="genie__sub">Unity Catalog metric views · {lender}</div>
          </div>
          <button
            className="drawer__close"
            onClick={() => setGenieOpen(false)}
            aria-label="Close Genie"
            type="button"
          >
            <Icon name="close" size={14} />
          </button>
        </div>
        <div className="genie__body" ref={bodyRef}>
          {msgs.map((m, i) =>
            m.who === 'user' ? (
              <div key={i} className="genie__msg genie__msg--user">{m.text}</div>
            ) : (
              <div key={i} className="genie__msg genie__msg--ai">
                <div className="bubble">
                  <GenieAnswer payload={m.payload} onFollowUp={ask} dense />
                </div>
                {m.sources && m.sources.length > 0 && (
                  <div className="sources">
                    {m.sources.map((s, j) => (
                      <EvidenceChip key={j} source={drawerForAsset(s)} title={`Source: ${s}`}>
                        {s}
                      </EvidenceChip>
                    ))}
                  </div>
                )}
              </div>
            )
          )}
          {typing && (
            <div className="genie__msg genie__msg--ai">
              <div className="bubble"><div className="typing-dots"><span/><span/><span/></div></div>
            </div>
          )}
          {msgs.length <= 1 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
              {SAMPLE_QUESTIONS.map((s) => (
                <button key={s} className="filter" style={{ alignSelf: 'flex-start', textAlign: 'left' }} onClick={() => ask(s)} type="button">
                  <Icon name="sparkle" size={11} /> {s}
                </button>
              ))}
            </div>
          )}
        </div>
        <form
          className="genie__input"
          onSubmit={(e) => {
            e.preventDefault();
            ask(input);
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about borrowers, segments, triggers…"
            aria-label="Ask Genie"
          />
          <Button variant="primary" size="sm" type="submit" icon="send" aria-label="Ask">Ask</Button>
        </form>
      </div>
    </>
  );
}
