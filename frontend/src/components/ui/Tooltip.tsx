import {
  useEffect,
  useId,
  useLayoutEffect,
  useSyncExternalStore,
  type ElementType,
  type ReactElement,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { descriptionHost, descriptionId, retainDescription, retainHost } from './tooltipDescriptions';

/**
 * Tooltip — the design-system tooltip primitive (2026-09-21 audit critic-08;
 * deviation:tooltip, see docs/prototype-deviations.md "Pending rows"). The
 * prototype has no tooltip; the nearest shapes are `.map-tip`
 * (design_files/Module 0 Prototype.html:868-880) and the evidence hover card,
 * whose placement and timing it shares (ui/anchorPlacement.ts).
 *
 * Usage: `<Tooltip content="Toggle theme" shortcut="⌘K">{one element}</Tooltip>`.
 *
 * Split for the initial bundle (the shell renders in the Topbar on every
 * page): THIS module only re-renders the child from its own type and
 * props (cloneElement without the key) with
 *  - `aria-describedby` = the child's existing ids MERGED with a persistent
 *    hidden description carrying the same text (accessible names untouched).
 *    Plain-text content names the ONE shared description for that text
 *    (ui/tooltipDescriptions.ts: ref-counted, in a single hidden host, so a
 *    table of triggers costs one unstyled node per distinct text); a React
 *    node portals its own description into the same host;
 *  - `data-tooltip` (the description id) and `data-tooltip-shortcut`.
 * Everything else lives in ui/tooltipController.ts, loaded after the first
 * paint: one delegated listener set and one `role="tooltip"`
 * `popover="manual"` popup.
 * Hover opens after the shared delay (at once inside the reopen grace), a
 * touch pointer never opens it, only keyboard-originated focus opens it at
 * once, it closes on pointer leave / blur / pointer down and on Escape (an
 * escapeStack layer while open; a keyboard-opened tooltip consumes the key
 * and focus stays, a hover one lets it reach the layer below), and the trigger's
 * `aria-describedby` points at the popup while it is open.
 */

interface TriggerProps {
  'aria-describedby'?: string;
}

export interface TooltipProps {
  /** The tooltip text; newlines render as line breaks. */
  content: ReactNode;
  /** A keyboard shortcut shown in a `<kbd>` after the text. */
  shortcut?: string;
  /** Exactly one element: the trigger. */
  children: ReactElement<TriggerProps>;
}

/** The marker the shell puts on a trigger; the controller delegates on it. */
export const TOOLTIP_TRIGGER_SELECTOR = '[data-tooltip]';

const noSubscription = () => () => undefined;

/** Load the delegated controller (cached after the first load; a failed load retries on the next mount). */
function loadTooltipController(): void {
  import('./tooltipController').catch(() => undefined);
}

export function Tooltip({ content, shortcut, children }: TooltipProps) {
  // Plain text shares one description per distinct text; anything else gets
  // its own (React 19 ids, `_r_1_`, are valid element ids as they are).
  const text = typeof content === 'string' || typeof content === 'number' ? String(content) : null;
  const ownId = `tooltip-desc${useId()}`;
  const descId = text === null ? ownId : descriptionId(text, shortcut);
  // After the first paint, off the critical path; the shell is accessible
  // without the controller.
  useEffect(loadTooltipController, []);
  // In the commit that mounts the trigger, before paint: its description
  // exists whenever it does. A server render runs no effect.
  useLayoutEffect(
    () => (text === null ? retainHost() : retainDescription(text, shortcut)),
    [text, shortcut],
  );
  // A server render (react-dom/server: unit tests render shells to static
  // markup) cannot portal, so a React-node description is client-only.
  const client = useSyncExternalStore(noSubscription, () => true, () => false);
  const Trigger = children.type as ElementType;
  const own = children.props['aria-describedby'];
  return (
    <>
      <Trigger
        {...children.props}
        aria-describedby={own ? `${own} ${descId}` : descId}
        data-tooltip={descId}
        data-tooltip-shortcut={shortcut}
      />
      {text !== null || !client || typeof document === 'undefined'
        ? null
        : createPortal(
            <span hidden id={ownId}>
              <span data-tooltip-text="">{content}</span>
              {shortcut ? ` (${shortcut})` : null}
            </span>,
            descriptionHost(),
          )}
    </>
  );
}
