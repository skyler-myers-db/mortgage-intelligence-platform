import { useMemo, useSyncExternalStore } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useApp } from '../AppContext';
import { api } from '../../lib/api';
import type { ActorAuditEventSummary } from '../../lib/apiTypes';
import { fetchSavedViews } from '../../lib/apiClients/savedViews';
import {
  ariaKeyShortcuts,
  chordKeycaps,
  keyBindingsVersion,
  listKeyBindings,
  parseChord,
  subscribeKeyBindings,
  survivesSingleKeySwitch,
} from '../../lib/keymap';
import { useSingleKeyShortcuts } from '../../lib/keymapPreference';
import { queryKeys } from '../../lib/queryKeys';
import type { SavedView } from '../../types/leadFilters';
import type { CommandShortcut } from './commandActions';
import {
  paletteAssetGroup,
  paletteSourceGroups,
  type PaletteSourceGroup,
} from './commandPaletteSources';

/**
 * The palette's open-gated reads (2026-09-21 audit shell-07 item 2): every
 * one is audit-free and runs only while the palette is open. Nothing is
 * prefetched, polled or persisted:
 *
 *   - Saved views: the Lead Queue panel's own entry (queryKeys.savedViews),
 *     GET /api/workspace/saved-views.
 *   - Recent borrowers: the actor's own GET /api/audit/my-events, under the
 *     palette's key, staleTime 0 and gcTime 0, and only once the session is
 *     ready. It is never written to storage; an actor change clears it with
 *     the whole cache (the actor gate).
 *   - Data assets: administrators only, after two typed characters, under the
 *     palette's OWN key (never the admin page's ['mip','data-estate'], whose
 *     query function substitutes a fallback estate). Cached 5 minutes.
 */

const SAVED_VIEWS_STALE_MS = 60_000;
const RECENT_EVENTS_LIMIT = 25;
const PALETTE_ASSETS_STALE_MS = 5 * 60_000;
const ASSET_QUERY_MIN_CHARS = 2;

const NO_VIEWS: readonly SavedView[] = [];
const NO_EVENTS: readonly ActorAuditEventSummary[] = [];

export interface CommandPaletteSources {
  /** Saved views, Segments, Glossary, Saved leads and Recent borrowers, in order. */
  groups: readonly PaletteSourceGroup[];
  /** Data assets (administrators only), shown after the live borrowers. */
  assetGroup: PaletteSourceGroup | null;
}

export function useCommandPaletteSources(open: boolean, query: string): CommandPaletteSources {
  const { canAccessAdmin, savedLeads, sessionStatus } = useApp();
  const savedViews = useQuery({
    queryKey: queryKeys.savedViews(),
    queryFn: ({ signal }) => fetchSavedViews(signal),
    enabled: open,
    staleTime: SAVED_VIEWS_STALE_MS,
    retry: false,
  });
  const recent = useQuery({
    queryKey: queryKeys.paletteRecentEvents(),
    queryFn: ({ signal }) => api.myAuditEvents(RECENT_EVENTS_LIMIT, signal),
    enabled: open && sessionStatus === 'ready',
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  const assets = useQuery({
    queryKey: queryKeys.paletteAssets(),
    queryFn: ({ signal }) => api.dataEstate(signal),
    enabled: open && canAccessAdmin && query.trim().length >= ASSET_QUERY_MIN_CHARS,
    staleTime: PALETTE_ASSETS_STALE_MS,
    retry: false,
  });
  const views = savedViews.data?.saved_views ?? NO_VIEWS;
  const events = recent.data?.items ?? NO_EVENTS;
  const estate = canAccessAdmin ? assets.data ?? null : null;
  const groups = useMemo(
    () => paletteSourceGroups({ query, savedViews: views, savedLeads, recentEvents: events }),
    [query, views, savedLeads, events],
  );
  const assetGroup = useMemo(() => paletteAssetGroup(estate, query), [estate, query]);
  return { groups, assetGroup };
}

/** The binding ids registered at `version` (keyBindingsVersion): listKeyBindings() changes exactly when it does. */
function registeredBindingIds(version: number): { version: number; ids: ReadonlySet<string> } {
  return { version, ids: new Set(listKeyBindings().map((binding) => binding.id)) };
}

export interface LiveShortcut {
  /** The keycaps the row shows ('Shift', 'A'). */
  keycaps: readonly string[];
  /** The option's aria-keyshortcuts value. */
  aria: string;
}

/**
 * Which command shortcuts are live right now (shell-07 item 3): the binding
 * is registered (this page bound it) AND its chord works (a single-key chord
 * is off while the Console's single-key switch is off). Re-evaluated when a
 * page binds or drops a key and when the switch flips.
 */
export function useLiveShortcut(): (shortcut: CommandShortcut | undefined) => LiveShortcut | null {
  const bindingsVersion = useSyncExternalStore(subscribeKeyBindings, keyBindingsVersion, keyBindingsVersion);
  const [singleKeysOn] = useSingleKeyShortcuts();
  const registered = useMemo(() => registeredBindingIds(bindingsVersion), [bindingsVersion]);
  return (shortcut) => {
    if (!shortcut || !registered.ids.has(shortcut.bindingId)) return null;
    if (!singleKeysOn && !survivesSingleKeySwitch(parseChord(shortcut.chord))) return null;
    return { keycaps: chordKeycaps(shortcut.chord), aria: ariaKeyShortcuts([shortcut.chord]) };
  };
}
