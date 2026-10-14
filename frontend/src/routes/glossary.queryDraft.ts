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

let stamps = 0;
const nextStamp = (): number => ++stamps;
const stampsIssued = (): number => stamps;

/**
 * [what the field shows and the glossary filters by, set it as typed: the
 * field updates now and ?q= follows (an empty value drops it)].
 *
 * The draft records the location it last accounted for (`key`), the newest
 * stamp already landed or superseded (`landed`) and the newest this field
 * has written (`issued`). A changed location is adjusted for during render;
 * React discards that render's output and renders again before committing.
 */
export function useGlossaryQuery(): [string, (next: string) => void] {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [draft, setDraft] = useState(() => ({
    text: searchParams.get('q') ?? '',
    key: location.key,
    landed: stampsIssued(),
    issued: stampsIssued(),
  }));
  if (draft.key !== location.key) {
    const stamp: unknown = (location.state as Record<string, unknown> | null)?.[WRITE_STAMP];
    setDraft(
      typeof stamp === 'number' && stamp > draft.landed && stamp <= draft.issued
        ? { ...draft, key: location.key, landed: stamp }
        : { text: searchParams.get('q') ?? '', key: location.key, landed: draft.issued, issued: draft.issued },
    );
  }
  return [
    draft.text,
    (next) => {
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
    },
  ];
}
