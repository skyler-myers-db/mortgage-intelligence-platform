import { useState } from 'react';
import { useLocation, useSearchParams } from 'react-router';

/**
 * The glossary search field's text (W5d critic-07, fix round 2;
 * deviation:glossary-search).
 *
 * The data router applies setSearchParams inside startTransition (app.tsx),
 * so an input controlled straight from ?q= is reset to the OLD value after
 * every keystroke until the transition commits: fast typing loses
 * characters and a mid-word edit throws the caret to the end. The field is
 * therefore controlled by a synchronous local draft, and ?q= is written from
 * it on every keystroke ({replace, preventScrollReset}: no history entries).
 *
 * Telling our own writes from everything else. Each write is stamped in the
 * history entry's state with a module-wide, ever-increasing number. When the
 * location changes, a stamp this field issued and has not yet seen land is
 * its own write catching up: the draft (what the person has typed since)
 * stays. Anything else (Back or Forward to an older entry, whose stamp has
 * already landed; a link or a category chip, which carry none) came from
 * outside, so the draft takes the URL's ?q=. Writes land in call order, so
 * an outside navigation also supersedes every write issued before it.
 */
const WRITE_STAMP = 'glossaryQueryWrite';

let lastStamp = 0;

function nextStamp(): number {
  lastStamp += 1;
  return lastStamp;
}

function stampsIssued(): number {
  return lastStamp;
}

function stampOf(state: unknown): number | null {
  if (typeof state !== 'object' || state === null) return null;
  const stamp = (state as Record<string, unknown>)[WRITE_STAMP];
  return typeof stamp === 'number' ? stamp : null;
}

interface Draft {
  text: string;
  /** The location the draft last accounted for. */
  locationKey: string;
  /** The newest stamp already landed or superseded. */
  landed: number;
  /** The newest stamp this field has written. */
  issued: number;
}

export interface GlossaryQuery {
  /** What the field shows and the glossary filters by. */
  text: string;
  /** Type: update the field now and write ?q= (an empty value drops it). */
  setText: (next: string) => void;
}

export function useGlossaryQuery(): GlossaryQuery {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const committed = searchParams.get('q') ?? '';
  const [draft, setDraft] = useState<Draft>(() => ({
    text: committed,
    locationKey: location.key,
    landed: stampsIssued(),
    issued: stampsIssued(),
  }));

  let current = draft;
  if (draft.locationKey !== location.key) {
    const stamp = stampOf(location.state);
    current = stamp !== null && stamp > draft.landed && stamp <= draft.issued
      ? { ...draft, locationKey: location.key, landed: stamp }
      : { text: committed, locationKey: location.key, landed: draft.issued, issued: draft.issued };
    setDraft(current);
  }

  const setText = (next: string): void => {
    const stamp = nextStamp();
    setDraft((previous) => ({ ...previous, text: next, issued: stamp }));
    setSearchParams(
      (params) => {
        const written = new URLSearchParams(params);
        if (next) written.set('q', next);
        else written.delete('q');
        return written;
      },
      { replace: true, preventScrollReset: true, state: { [WRITE_STAMP]: stamp } },
    );
  };

  return { text: current.text, setText };
}
