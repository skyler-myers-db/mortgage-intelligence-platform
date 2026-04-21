import { useEffect, useRef, useState } from 'react';
import { useApp } from '../AppContext';
import { api } from '../../lib/api';
import { Icon } from '../Icon';
import { Button } from '../Primitives';

/**
 * Floating Genie chat panel — `.genie` BEM from the prototype. Fixed
 * bottom-right, reachable from every page. Falls back to api.genie() which
 * already has a deterministic fallback wired. A floating `.genie__fab` is
 * shown when the panel is closed (bottom-right sparkle) so one click anywhere
 * in the app reaches Genie.
 */

interface ChatMsg { who: 'ai' | 'user'; text: string; sources?: string[] }

const SAMPLE_QUESTIONS = [
  'How many in-the-money borrowers in Travis County?',
  'Which segment has the highest conversion propensity?',
  'Show owners with 2+ properties where any lien is in the money.',
];

export function GenieChat() {
  const { genieOpen, setGenieOpen, lender } = useApp();
  const [msgs, setMsgs] = useState<ChatMsg[]>([
    {
      who: 'ai',
      text: `Hi — I'm Genie, grounded on ${lender}'s Unity Catalog metric views + Cotality semantic models. Ask about borrower segments, triggers, or evidence.`,
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
      const res = await api.genie(q);
      const answer = (res as { answer?: string }).answer ?? '';
      const trusted = (res as { trusted_assets?: string[] }).trusted_assets ?? [];
      setMsgs((m) => [...m, { who: 'ai', text: answer, sources: trusted.slice(0, 4) }]);
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
            <div className="genie__sub">Grounded on UC metric views · {lender}</div>
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
                <div className="bubble">{m.text}</div>
                {m.sources && m.sources.length > 0 && (
                  <div className="sources">
                    {m.sources.map((s, j) => (
                      <span key={j} className="evidence-chip" title={`Source: ${s}`}>
                        <Icon name="link" size={9} className="e-ico" />
                        {s}
                      </span>
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
          <Button variant="primary" size="sm" type="submit" icon="send">Ask</Button>
        </form>
      </div>
    </>
  );
}
