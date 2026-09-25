import { Suspense, useEffect, useRef, useState } from 'react';
import { useApp } from '../AppContext';
import { Icon } from '../Icon';
import { assetDetailHref, evidenceDestinationFor } from '../../lib/drawerSources';
import { useExitRetained } from '../../hooks/useExitRetained';
import { useModalDialog } from '../../hooks/useModalDialog';
import { useTabs } from '../ui/useTabs';
import { formatTimestamp } from '../../lib/time';
import {
  EvidenceDrawerBodyContext,
  LazyEvidenceDrawerBody,
  preloadEvidenceDrawerBodyOnIdle,
  type DrawerTab,
  type EvidenceDrawerBodyProps,
} from './evidenceDrawerBodyLoader';

/**
 * Data source / evidence drawer — the shell FRAME (audit 2026-09-21
 * `bundle-04` items 1-2). What a chip click needs at once stays here, in the
 * initial closure: the native modal <dialog> (useModalDialog), the exit
 * (useExitRetained keeps the closing source rendered while it slides out),
 * the header, the Overview / Lineage tablist, and everything derived from
 * the source alone (its destination, the asset detail link, the event date
 * label). Those are real uses that keep lib/drawerSources, the source
 * registry, lib/time and useTabs in the initial closure, so the routes that
 * share them do not each re-bundle them.
 *
 * The panels and the two governed reads live in EvidenceDrawerBody's own
 * chunk, preloaded on idle from this frame and on hover / focus of an
 * evidence chip (evidenceDrawerBodyLoader). Until it arrives the body shows
 * a loading status; a chunk that fails to load throws into the drawer's
 * panel boundary, which renders its recovery frame.
 */

const DRAWER_TABS: readonly DrawerTab[] = ['overview', 'lineage'];

function BodyLoading() {
  return (
    <div className="drawer__body">
      <div className="source-card" role="status">Loading evidence…</div>
    </div>
  );
}

export function EvidenceDrawer() {
  const { drawer, setDrawer, canAccessAdmin } = useApp();
  // `open` follows the LIVE source: the focus trap releases and focus returns
  // to the trigger the moment the exit starts.
  const open = !!drawer;
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  const drawerRef = useRef<HTMLDialogElement | null>(null);
  // `d` is what the panel shows. setDrawer(null) used to empty the body in the
  // same commit that started the slide-out, so the drawer animated out blank
  // (2026-09-21 audit css-03). The closing source stays rendered until the
  // panel's own exit transition ends.
  const d = useExitRetained(drawer, drawerRef);
  const [tab, setTab] = useState<DrawerTab>('overview');
  const tabs = useTabs({ tabs: DRAWER_TABS, selected: tab, onSelect: setTab, idBase: 'drawer' });
  // Every drawer open starts on Overview — a lineage deep-dive on one
  // source must not leak into the next source's drawer. Keyed on the live
  // source and skipped on close, so the retained body does not flip tabs
  // while it slides out.
  useEffect(() => {
    if (drawer) setTab('overview');
  }, [drawer]);
  // Warm the body chunk (never data) once the shell is idle.
  useEffect(() => preloadEvidenceDrawerBodyOnIdle(), []);

  const destination = evidenceDestinationFor(d);
  const assetHref = d?.assetKey ? assetDetailHref(d.assetKey) : null;
  // /data-estate/assets/:key is served by the same AdminDep-gated metadata
  // read as the body's metadata query, so the actions that land there are
  // gated exactly like that read. For the buyer personas (not admins) the
  // drawer's primary action used to end on a 403 page (2026-09-21 audit
  // critic-03); the proof itself — explanation, signals, governed assets,
  // lineage — stays.
  const assetDetailsHref =
    canAccessAdmin && destination.kind === 'unity_catalog' ? assetHref : null;
  const eventDateLabel = d?.eventDate ? formatTimestamp(d.eventDate) : null;
  const close = () => setDrawer(null);
  const body: EvidenceDrawerBodyProps | null = d
    ? {
        source: d,
        open,
        tab,
        panelProps: tabs.panelProps,
        destination,
        assetDetailsHref,
        eventDateLabel,
        canAccessAdmin,
        onClose: close,
      }
    : null;

  // A native modal <dialog> (audit stack-05 / a11y-07 / css-03): showModal()
  // makes the page, the Console and the Genie panel inert behind it, the
  // `::backdrop` is the prototype scrim, and focus goes back to the chip.
  useModalDialog({
    open,
    dialogRef: drawerRef,
    initialFocusRef: closeBtnRef,
    onDismiss: close,
    backdrop: 'outside',
  });

  return (
    <dialog
      ref={drawerRef}
      className={`drawer ${open ? 'is-open' : ''}`}
      aria-labelledby={d ? 'evidence-drawer-title' : undefined}
      aria-label={d ? undefined : 'Data source and lineage'}
      aria-hidden={!open || undefined}
      // The closing source stays rendered while the panel slides out; inert
      // keeps that retained copy out of the tab order and the pointer path.
      inert={!open}
    >
      <div className="drawer__hdr">
        <div className="drawer__source-icon">
          <Icon name="db" size={16} />
        </div>
        <div className="drawer__hdr-main">
          <div className="drawer__title" id="evidence-drawer-title">{d?.title ?? 'Data source'}</div>
          <div className="drawer__subtitle">{d?.short ?? d?.assetPath ?? 'Source proof'}</div>
        </div>
        <button ref={closeBtnRef} className="drawer__close" onClick={close} aria-label="Close drawer" type="button">
          <Icon name="close" size={14} />
        </button>
      </div>
      {d && (
        <div className="drawer__tabs" {...tabs.tabListProps} aria-label="Evidence detail views">
          <button {...tabs.tabProps('overview')} className={`drawer__tab ${tab === 'overview' ? 'is-active' : ''}`}>
            Overview
          </button>
          <button {...tabs.tabProps('lineage')} className={`drawer__tab ${tab === 'lineage' ? 'is-active' : ''}`}>
            Lineage
          </button>
        </div>
      )}
      {body ? (
        <EvidenceDrawerBodyContext value={body}>
          <Suspense fallback={<BodyLoading />}>
            <LazyEvidenceDrawerBody />
          </Suspense>
        </EvidenceDrawerBodyContext>
      ) : (
        <div className="drawer__body">
          <p className="muted">Tap any evidence chip or KPI source line to inspect the lineage.</p>
        </div>
      )}
    </dialog>
  );
}
