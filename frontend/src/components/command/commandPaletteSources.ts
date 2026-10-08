import type { IconName } from '../Icon';
import type { ActorAuditEventSummary } from '../../lib/apiTypes';
import { assetHrefForSource } from '../../lib/drawerSources';
import { glossaryEntries } from '../../lib/mortgageGlossary';
import { QUEUE_MASKED_ID_RE } from '../../lib/queueContext';
import { SEGMENT_DEFINITIONS, segmentIcon, segmentName } from '../../lib/segmentMetadata';
import type { DataEstateResponse } from '../../types';
import type { SavedView } from '../../types/leadFilters';
import type { SavedLead } from '../../types/workspace';
import { scoreText } from './commandActions';

/**
 * The ⌘K palette's provider groups beyond the page and action registry
 * (2026-09-21 audit shell-07 items 2 and 5, tables-09 phase 2): pure builders
 * and ranking, so the order and the caps are unit-pinnable. The open-gated
 * reads live in useCommandPaletteSources.ts; nothing here touches the network.
 *
 * Group order (deviation:palette-provider-groups): Selected borrowers, Pages & actions,
 * Saved views, Segments, Glossary, Saved leads, Recent borrowers, Borrowers
 * (live search), Data assets (administrators only), Ask Genie (always last).
 * This module builds the six provider groups between Pages & actions and the
 * live Borrowers / Data assets pair.
 *
 * Rules:
 *   - Each provider shows at most PALETTE_PROVIDER_MAX rows.
 *   - An empty query shows Saved views and Recent borrowers only (with the
 *     verbs and Pages & actions); the others need a typed character.
 *   - A borrower appears once: saved leads first, then recents, then the live
 *     search rows (dedupePaletteBorrowers).
 *   - Every row navigates (D-shell-deviations-c1: a borrower row opens the
 *     dossier, never a peek sheet). Opening a dossier is the audited read;
 *     a row never reads a borrower itself.
 */

export const PALETTE_PROVIDER_MAX = 5;

export type PaletteProviderId =
  | 'saved-views'
  | 'segments'
  | 'glossary'
  | 'saved-leads'
  | 'recent-borrowers'
  | 'data-assets';

export interface PaletteSourceRow {
  /** Unique within the palette (the React key and the option's identity). */
  id: string;
  label: string;
  hint: string;
  icon: IconName;
  keywords: readonly string[];
  /** Where Enter or a click goes. */
  to: string;
  /** A masked borrower id row: monospaced, and de-duplicated across groups. */
  borrowerId?: string;
}

export interface PaletteSourceGroup {
  id: PaletteProviderId;
  label: string;
  rows: readonly PaletteSourceRow[];
}

export const PALETTE_GROUP_LABELS: Readonly<Record<PaletteProviderId, string>> = {
  'saved-views': 'Saved views',
  segments: 'Segments',
  glossary: 'Glossary',
  'saved-leads': 'Saved leads',
  'recent-borrowers': 'Recent borrowers',
  'data-assets': 'Data assets',
};

/** Shown with an empty query; every other provider waits for a typed character. */
const EMPTY_QUERY_PROVIDERS: ReadonlySet<PaletteProviderId> = new Set(['saved-views', 'recent-borrowers']);

function borrowerPath(borrowerId: string): string {
  return `/borrower-360/${borrowerId}`;
}

/** A saved Lead Queue view opens exactly as the queue's own panel link does. */
export function savedViewRows(views: readonly SavedView[]): PaletteSourceRow[] {
  return views.map((view) => ({
    id: `saved-view-${view.view_id}`,
    label: view.name,
    hint: 'Saved view',
    icon: 'filter',
    keywords: [],
    to: `/lead-queue?${view.params}`,
  }));
}

/** Names and icons come from lib/segmentMetadata, so a lexicon rename flows through. */
export function segmentRows(): PaletteSourceRow[] {
  return SEGMENT_DEFINITIONS.map((definition) => ({
    id: `segment-${definition.code}`,
    label: segmentName(definition.code),
    hint: 'Segment',
    icon: segmentIcon(definition.code),
    keywords: [definition.code, definition.description],
    to: `/segment-intelligence?segment=${encodeURIComponent(definition.code)}`,
  }));
}

export function glossaryRows(): PaletteSourceRow[] {
  return glossaryEntries.map((entry) => ({
    id: `glossary-${entry.id}`,
    label: entry.term,
    hint: entry.short,
    icon: 'doc',
    keywords: entry.aliases,
    to: `/glossary#${entry.id}`,
  }));
}

/** Newest first, as the Console lists them. */
export function savedLeadRows(savedLeads: Readonly<Record<string, SavedLead>>): PaletteSourceRow[] {
  return Object.values(savedLeads)
    .filter((lead) => QUEUE_MASKED_ID_RE.test(lead.borrower_id))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .map((lead) => ({
      id: `saved-lead-${lead.borrower_id}`,
      label: lead.borrower_id,
      hint: [lead.city, lead.state].filter(Boolean).join(', ') || 'Saved lead',
      icon: 'tag',
      keywords: [lead.city ?? '', lead.state ?? '', lead.zip ?? ''].filter(Boolean),
      to: borrowerPath(lead.borrower_id),
      borrowerId: lead.borrower_id,
    }));
}

/**
 * The borrowers this actor worked on recently (any borrower event: a view, a
 * decision), newest first, from the actor's own audit-free my-events feed:
 * masked ids only, each once. Hence 'Recent', not 'Recently opened'.
 */
export function recentBorrowerRows(events: readonly ActorAuditEventSummary[]): PaletteSourceRow[] {
  const seen = new Set<string>();
  const rows: PaletteSourceRow[] = [];
  for (const event of events) {
    const id = event.subject_id;
    if (event.entity_type !== 'borrower' || id === null || !QUEUE_MASKED_ID_RE.test(id) || seen.has(id)) continue;
    seen.add(id);
    rows.push({
      id: `recent-${id}`,
      label: id,
      hint: 'Recent',
      icon: 'user',
      keywords: [],
      to: borrowerPath(id),
      borrowerId: id,
    });
  }
  return rows;
}

/** Data-estate assets with an asset page (assetHrefForSource), each once. */
export function dataAssetRows(estate: DataEstateResponse | null): PaletteSourceRow[] {
  if (!estate) return [];
  const seen = new Set<string>();
  const rows: PaletteSourceRow[] = [];
  for (const lane of estate.lanes) {
    for (const asset of lane.assets) {
      const href = assetHrefForSource(asset.uc_object);
      if (!href || seen.has(href)) continue;
      seen.add(href);
      rows.push({
        id: `asset-${href}`,
        label: asset.label,
        hint: asset.uc_object ?? lane.title,
        icon: 'db',
        keywords: [asset.name, lane.title],
        to: href,
      });
    }
  }
  return rows;
}

/** Rank rows for a query (registry order on ties and for an empty query), capped. */
export function rankPaletteRows(
  rows: readonly PaletteSourceRow[],
  query: string,
  max: number = PALETTE_PROVIDER_MAX,
): PaletteSourceRow[] {
  return rows
    .map((row, index) => ({ row, index, score: scoreText(row.label, row.hint, row.keywords, query) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .slice(0, max)
    .map((entry) => entry.row);
}

export interface PaletteSourceInput {
  query: string;
  savedViews: readonly SavedView[];
  savedLeads: Readonly<Record<string, SavedLead>>;
  recentEvents: readonly ActorAuditEventSummary[];
}

/**
 * The provider groups between Pages & actions and the live Borrowers, in
 * order, each ranked and capped, borrowers de-duplicated (saved leads win
 * over recents). Empty groups are dropped.
 */
export function paletteSourceGroups({ query, savedViews, savedLeads, recentEvents }: PaletteSourceInput): PaletteSourceGroup[] {
  const typed = query.trim().length > 0;
  const shown = (id: PaletteProviderId) => typed || EMPTY_QUERY_PROVIDERS.has(id);
  const saved = shown('saved-leads') ? rankPaletteRows(savedLeadRows(savedLeads), query) : [];
  const savedIds = new Set(saved.map((row) => row.borrowerId));
  const recent = rankPaletteRows(
    recentBorrowerRows(recentEvents).filter((row) => !savedIds.has(row.borrowerId)),
    query,
  );
  const groups: PaletteSourceGroup[] = [
    { id: 'saved-views', label: PALETTE_GROUP_LABELS['saved-views'], rows: rankPaletteRows(savedViewRows(savedViews), query) },
    { id: 'segments', label: PALETTE_GROUP_LABELS.segments, rows: shown('segments') ? rankPaletteRows(segmentRows(), query) : [] },
    { id: 'glossary', label: PALETTE_GROUP_LABELS.glossary, rows: shown('glossary') ? rankPaletteRows(glossaryRows(), query) : [] },
    { id: 'saved-leads', label: PALETTE_GROUP_LABELS['saved-leads'], rows: saved },
    { id: 'recent-borrowers', label: PALETTE_GROUP_LABELS['recent-borrowers'], rows: recent },
  ];
  return groups.filter((group) => group.rows.length > 0);
}

/** The data-asset group (administrators, two typed characters), ranked and capped. */
export function paletteAssetGroup(estate: DataEstateResponse | null, query: string): PaletteSourceGroup | null {
  if (query.trim().length === 0) return null;
  const rows = rankPaletteRows(dataAssetRows(estate), query);
  return rows.length > 0 ? { id: 'data-assets', label: PALETTE_GROUP_LABELS['data-assets'], rows } : null;
}

/** Live search rows whose borrower an earlier group already lists are dropped. */
export function dedupePaletteBorrowers<T extends { borrower_id: string }>(
  live: readonly T[],
  groups: readonly PaletteSourceGroup[],
): T[] {
  const listed = new Set<string>();
  for (const group of groups) {
    for (const row of group.rows) if (row.borrowerId) listed.add(row.borrowerId);
  }
  return live.filter((lead) => !listed.has(lead.borrower_id));
}
