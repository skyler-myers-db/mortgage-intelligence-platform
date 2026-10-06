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
 * Both fetch the chunk only (the body and its registry prose), never data:
 * the drawer's reads (freshness, the KPI proof, the lineage manifest, admin
 * asset metadata) start only once it is open.
 *
 * The props travel through a context because lazyWithPreload's component
 * takes none; the frame is the only provider.
 */

export type DrawerTab = 'overview' | 'lineage' | 'under-the-hood';

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

/**
 * The registry prose (lib/drawerSourceRegistry.prose, audit `bundle-04` item
 * 3): the descriptions, signals and definitions the slim shell index leaves
 * out. EvidenceDrawerBody imports it statically, so it ships with the body;
 * loading the body (any preload, or the drawer opening) also records it here,
 * so the evidence hover card can read a registry signal synchronously once
 * it has loaded. The chunk only: nothing here fetches data.
 */
type DrawerProseModule = typeof import('../../lib/drawerSourceRegistry.prose');

let loadedProse: DrawerProseModule | null = null;
let proseLoad: Promise<DrawerProseModule> | null = null;

/** The prose module, loaded once (a failed load is retried on the next call). */
export function loadDrawerProse(): Promise<DrawerProseModule> {
  proseLoad ??= import('../../lib/drawerSourceRegistry.prose').then(
    (module) => {
      loadedProse = module;
      return module;
    },
    (error: unknown) => {
      proseLoad = null;
      throw error;
    },
  );
  return proseLoad;
}

/** The prose module once it has loaded, else null (synchronous; never starts a load). */
export function getLoadedDrawerProse(): DrawerProseModule | null {
  return loadedProse;
}

export const LazyEvidenceDrawerBody = lazyWithPreload(() =>
  Promise.all([import('./EvidenceDrawerBody'), loadDrawerProse()]).then(([module]) => ({
    default: module.EvidenceDrawerBody,
  })),
);

/** Intent preload (hover, focus, a recovery retry): the chunk only, failures swallowed. */
export function preloadEvidenceDrawerBody(): void {
  preloadBestEffort(LazyEvidenceDrawerBody.preload);
}

/** Idle preload from the frame's mount effect; returns its cancel. */
export const preloadEvidenceDrawerBodyOnIdle = createIdlePreloader(() => LazyEvidenceDrawerBody.preload(), 5000);
