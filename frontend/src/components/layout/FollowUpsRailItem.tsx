import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState, type ComponentType } from 'react';
import { Icon } from '../Icon';
import { useMinuteClock } from '../ui/Timestamp';
import type { FollowupSummary } from '../../lib/apiClients/followUpsSummary';
import { hasDueToday } from '../../lib/followUpsDay';
import { preloadBestEffort } from '../../lib/lazyPreload';
import { createIdlePreloader } from '../../lib/prefetch';
import type { FollowUpsTrayProps } from './FollowUpsTray';

/**
 * The rail's "Follow-ups due" item (D-shell-deviations-a; audit shell-09,
 * wow-power-3; deviation:followups-rail-item). The prototype rail ends with
 * Settings (design_files/Module 0 Prototype.html:1180-1204) and defines a
 * `bell` glyph it never places (:1008); this item places it above the ledger
 * link and the gear, in the rail's empty space, so nothing shifts.
 *
 * A DOT, never a count: the summary is Lakebase-only, while the tray checks
 * every row against gold, so a number could disagree with the tray. The dot
 * shows when something is due today or overdue in the viewer's own day.
 *
 * Its own lazy chunk (the batch's initial-JS cap), rendered by Rail only for
 * an eligible actor. The tray chunk is warmed at idle and on hover / focus:
 * chunks only, never data (the list is read when the tray opens). The
 * accessible name is the visible "Due" plus a visually hidden " follow-ups"
 * (WCAG 2.5.3), so there is no aria-label; the tray renders right after the
 * button, inside the rail nav, only while open.
 */

const NoTray: ComponentType<FollowUpsTrayProps> = () => null;

let trayChunk: Promise<{ default: ComponentType<FollowUpsTrayProps> }> | null = null;
function loadTray(): Promise<{ default: ComponentType<FollowUpsTrayProps> }> {
  trayChunk ??= import('./FollowUpsTray')
    .then((module) => module.default)
    .then(
      (tray: ComponentType<FollowUpsTrayProps> | undefined) => ({ default: tray ?? NoTray }),
      (error: unknown) => {
        trayChunk = null;
        throw error;
      },
    );
  return trayChunk;
}
const preloadTrayAtIdle = createIdlePreloader(loadTray, 5000);
// A tray chunk that cannot load renders nothing, never a shell error.
const FollowUpsTray = lazy(() => loadTray().catch(() => ({ default: NoTray })));

export const DUE_HINT = 'Something is due today or overdue';

export default function FollowUpsRailItem({ summary }: { summary: FollowupSummary }) {
  const now = useMinuteClock();
  const hasDue = hasDueToday(summary.next_due_at, now);
  const [open, setOpen] = useState(false);
  const trayId = useId();
  const hintId = useId();
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => preloadTrayAtIdle(), []);

  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    if (refocus) triggerRef.current?.focus({ preventScroll: true });
  }, []);
  const warm = () => preloadBestEffort(loadTray);

  return (
    <>
      <span id={hintId} hidden>{DUE_HINT}</span>
      <button
        ref={triggerRef}
        type="button"
        className={`rail__item${hasDue ? ' has-due' : ''}`}
        aria-expanded={open}
        aria-controls={open ? trayId : undefined}
        aria-describedby={hasDue ? hintId : undefined}
        onClick={() => setOpen((value) => !value)}
        onPointerEnter={warm}
        onFocus={warm}
      >
        <Icon name="bell" size={18} className="ico" />
        <span className="mod">Due</span>
        <span className="sr-only"> follow-ups</span>
      </button>
      {open && (
        <Suspense fallback={null}>
          <FollowUpsTray id={trayId} triggerRef={triggerRef} onClose={close} />
        </Suspense>
      )}
    </>
  );
}
