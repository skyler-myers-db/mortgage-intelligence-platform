import { useEffect, useId, type ElementType, type ReactElement, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

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
 *    hidden description carrying the same text (accessible names untouched);
 *  - `data-tooltip` (the description id) and `data-tooltip-shortcut`.
 * Everything else lives in ui/tooltipController.ts, loaded after the first
 * paint: one delegated listener set and one `role="tooltip"`
 * `popover="manual"` popup.
 * Hover opens after the shared delay (at once inside the reopen grace), a
 * touch pointer never opens it, only keyboard-originated focus opens it at
 * once, it closes on pointer leave / blur / pointer down and on Escape (an
 * escapeStack layer while open; focus stays), and the trigger's
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

/** Load the delegated controller (cached after the first load; a failed load retries on the next mount). */
function loadTooltipController(): void {
  import('./tooltipController').catch(() => undefined);
}

export function Tooltip({ content, shortcut, children }: TooltipProps) {
  // React 19 ids (`_r_1_`) are valid element ids as they are.
  const descId = `tooltip-desc${useId()}`;
  // After the first paint, off the critical path; the shell is accessible
  // without the controller.
  useEffect(loadTooltipController, []);
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
      {typeof document === 'undefined'
        ? null
        : createPortal(
            <span hidden id={descId}>
              <span data-tooltip-text="">{content}</span>
              {shortcut ? ` (${shortcut})` : null}
            </span>,
            document.body,
          )}
    </>
  );
}
