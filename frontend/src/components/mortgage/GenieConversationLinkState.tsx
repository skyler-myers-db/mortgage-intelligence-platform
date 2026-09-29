import { Icon } from '../Icon';
import { Button, SurfaceTitle } from '../Primitives';
import { Skeleton } from '../ui/Skeleton';

/**
 * The conversation deep link's non-ok states (audit 2026-09-21 `shell-03`
 * remainder; routes/ask-genie.deep-link.tsx owns the grammar and the read).
 * A lazy chunk, loaded only while `/ask-genie/:conversationId` is open. It
 * lives outside src/routes/ on purpose: it is a component chunk, not a
 * route module with a budget of its own.
 *
 * It stands in for the conversation surface, so no thread and no composer
 * ever show under an unverified id, and nothing here writes storage or starts
 * a turn. The copy is fixed and neutral: a 404, a 403 and a malformed id all
 * read the same, and an unavailable read never shows error text. Markup is the
 * prototype's `.surface` / `.surface__hdr` / `.btn` (design_files/index.html);
 * no new CSS.
 */

export type GenieConversationLinkStateKind = 'loading' | 'not-found' | 'unavailable';

export interface GenieConversationLinkStateProps {
  state: GenieConversationLinkStateKind;
  /** Leave the link: replace the URL with `/ask-genie`. */
  onOpenAskGenie: () => void;
  /** Read the conversation again (unavailable only). */
  onRetry: () => void;
  /** A retry is in flight. */
  retrying: boolean;
}

export const GENIE_LINK_NOT_FOUND_COPY = "This conversation isn't available.";
export const GENIE_LINK_UNAVAILABLE_COPY = "This conversation can't be loaded right now. Nothing was changed.";

export default function GenieConversationLinkState({
  state,
  onOpenAskGenie,
  onRetry,
  retrying,
}: GenieConversationLinkStateProps) {
  const failed = state === 'unavailable';
  return (
    <div className="surface" aria-busy={state === 'loading' || undefined} data-genie-link={state}>
      <div className="surface__hdr">
        <Icon name="sparkle" size={14} className="icon-accent" />
        <SurfaceTitle>Conversation</SurfaceTitle>
      </div>
      <div className="surface__body stack-grid">
        {state === 'loading' ? (
          <>
            <span className="sr-only">Loading the conversation</span>
            <Skeleton width="45%" />
            <Skeleton width="85%" />
          </>
        ) : (
          <>
            <p>{failed ? GENIE_LINK_UNAVAILABLE_COPY : GENIE_LINK_NOT_FOUND_COPY}</p>
            <div className="chip-row">
              {failed && (
                <Button type="button" variant="primary" onClick={onRetry} disabled={retrying}>
                  {retrying ? 'Retrying…' : 'Retry'}
                </Button>
              )}
              <Button type="button" variant={failed ? 'ghost' : 'primary'} onClick={onOpenAskGenie}>
                Open Ask Genie
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
