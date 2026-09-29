import { Icon } from '../components/Icon';
import { Button, SurfaceTitle } from '../components/Primitives';
import { Skeleton } from '../components/ui/Skeleton';

/**
 * The conversation deep link's non-ok states (audit 2026-09-21 `shell-03`
 * remainder; routes/ask-genie.deep-link.tsx owns the grammar and the read).
 * A lazy chunk, loaded only while `/ask-genie/:conversationId` is open.
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
export const GENIE_LINK_NOT_FOUND_DETAIL =
  'Open Ask Genie to ask a new question, or pick one of your conversations from History.';
export const GENIE_LINK_UNAVAILABLE_COPY = "This conversation can't be loaded right now.";
export const GENIE_LINK_UNAVAILABLE_DETAIL = 'Nothing was changed. Try again in a moment.';

function Header() {
  return (
    <div className="surface__hdr">
      <Icon name="sparkle" size={14} className="icon-accent" />
      <SurfaceTitle>Conversation</SurfaceTitle>
    </div>
  );
}

export default function GenieConversationLinkState({
  state,
  onOpenAskGenie,
  onRetry,
  retrying,
}: GenieConversationLinkStateProps) {
  if (state === 'loading') {
    return (
      <div className="surface" aria-busy="true" data-genie-link="loading">
        <Header />
        <div className="surface__body stack-grid">
          <span className="sr-only">Loading the conversation</span>
          <Skeleton width="45%" />
          <Skeleton width="90%" />
          <Skeleton width="70%" />
        </div>
      </div>
    );
  }
  const notFound = state === 'not-found';
  return (
    <div className="surface" data-genie-link={state}>
      <Header />
      <div className="surface__body stack-grid">
        <p>{notFound ? GENIE_LINK_NOT_FOUND_COPY : GENIE_LINK_UNAVAILABLE_COPY}</p>
        <p className="muted fs-12">{notFound ? GENIE_LINK_NOT_FOUND_DETAIL : GENIE_LINK_UNAVAILABLE_DETAIL}</p>
        <div className="chip-row">
          {!notFound && (
            <Button type="button" variant="primary" onClick={onRetry} disabled={retrying} aria-busy={retrying || undefined}>
              {retrying ? 'Retrying…' : 'Retry'}
            </Button>
          )}
          <Button type="button" variant={notFound ? 'primary' : 'ghost'} onClick={onOpenAskGenie}>
            Open Ask Genie
          </Button>
        </div>
      </div>
    </div>
  );
}
