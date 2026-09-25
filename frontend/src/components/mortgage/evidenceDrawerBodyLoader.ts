import { createContext } from 'react';
import type { DrawerSource } from '../AppContext';
import type { EvidenceDestination } from '../../lib/drawerSources';
import { lazyWithPreload, preloadBestEffort } from '../../lib/lazyPreload';
import { createIdlePreloader } from '../../lib/prefetch';
import type { TabPanelProps } from '../ui/useTabs';

/**
 * The shell half of the evidence drawer's lazy body (audit 2026-09-21
 * `bundle-04` items 1-2): the handle for EvidenceDrawerBody's chunk, its
 * preloaders, and the channel the frame hands the body its props through.
 *
 * EvidenceDrawer.tsx (the frame: the <dialog>, header, tablist and
 * everything derived from the source alone) stays in the initial closure;
 * the panels and the two governed reads load from their own chunk:
 *   - on idle, from the frame's effect (`preloadEvidenceDrawerBodyOnIdle`,
 *     a no-op under the idle preloader's rules, e.g. Save-Data);
 *   - on intent, from the evidence hover card's hover and focus handlers
 *     (`preloadEvidenceDrawerBody`), which cover EvidenceChip and the Lead
 *     Queue overflow chip.
 * Both fetch the chunk only, never data: the drawer's reads (admin asset
 * metadata, the lineage manifest) start only once it is open.
 *
 * The props travel through a context because lazyWithPreload's component
 * takes none; the frame is the only provider.
 */

export type DrawerTab = 'overview' | 'lineage';

export interface EvidenceDrawerBodyProps {
  /** The source on screen: the live one while open, the retained one while it slides out. */
  source: DrawerSource;
  open: boolean;
  tab: DrawerTab;
  panelProps: (tab: DrawerTab) => TabPanelProps;
  destination: EvidenceDestination;
  /** The asset detail page of a mapped source (admin actions only land there). */
  assetDetailsHref: string | null;
  /** `formatTimestamp(source.eventDate)`, or null without an event date. */
  eventDateLabel: string | null;
  canAccessAdmin: boolean;
  onClose: () => void;
}

export const EvidenceDrawerBodyContext = createContext<EvidenceDrawerBodyProps | null>(null);

export const LazyEvidenceDrawerBody = lazyWithPreload(() =>
  import('./EvidenceDrawerBody').then((module) => ({ default: module.EvidenceDrawerBody })),
);

/** Intent preload (hover, focus, a recovery retry): the chunk only, failures swallowed. */
export function preloadEvidenceDrawerBody(): void {
  preloadBestEffort(LazyEvidenceDrawerBody.preload);
}

/** Idle preload from the frame's mount effect; returns its cancel. */
export const preloadEvidenceDrawerBodyOnIdle = createIdlePreloader(() => LazyEvidenceDrawerBody.preload(), 5000);
