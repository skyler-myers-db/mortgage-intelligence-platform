/**
 * Portfolio Builder's per-actor campaign-setup draft (2026-09-21 audit
 * critic-v3): the operator's unsaved setup survives an in-page link, a route
 * change or a reload in this tab, and comes back with a "Draft restored" chip
 * and a Reset.
 *
 * What is persisted is deliberately narrow: ONLY the operator setup fields
 * (holdout, send window, budget, channel costs, household toggle). Never the
 * server-authored copy, its generation mode or its provenance tokens (the
 * operator re-applies the recommendation), and never borrower data.
 *
 * The key (CAMPAIGN_DRAFT_STORAGE_KEY) lives in lib/actorScopedBrowserState,
 * so the shell's actor-change clear removes it without importing this lazy
 * module. Every storage access is wrapped: without storage (private mode,
 * blocked site data) the page renders exactly as it did before drafts.
 */
import { useEffect, useRef } from 'react';
import { CAMPAIGN_DRAFT_STORAGE_KEY } from '../lib/actorScopedBrowserState';
import {
  CAMPAIGN_NUMERIC_BOUNDS,
  normalizeCampaignNumericValue,
  type CampaignNumericField,
  type CampaignSetupState,
} from './portfolio-builder.logic';

const NUMERIC_FIELDS: readonly CampaignNumericField[] = ['holdoutPct', 'budget', 'emailCost', 'smsCost', 'mailCost'];
const TIME_FIELDS = ['startLocal', 'endLocal'] as const;
const DRAFT_VERSION = 1;
/** Writes wait for a 300 ms pause in typing. */
export const CAMPAIGN_DRAFT_WRITE_DELAY_MS = 300;

/** The persisted subset: operator setup only. */
export type CampaignSetupDraft = Pick<
  CampaignSetupState,
  'holdoutPct' | 'startLocal' | 'endLocal' | 'budget' | 'emailCost' | 'smsCost' | 'mailCost' | 'marketHouseholdTogether'
>;

export function campaignDraftOf(setup: CampaignSetupState): CampaignSetupDraft {
  return {
    holdoutPct: setup.holdoutPct,
    startLocal: setup.startLocal,
    endLocal: setup.endLocal,
    budget: setup.budget,
    emailCost: setup.emailCost,
    smsCost: setup.smsCost,
    mailCost: setup.mailCost,
    marketHouseholdTogether: setup.marketHouseholdTogether,
  };
}

function draftsEqual(a: CampaignSetupDraft, b: CampaignSetupDraft): boolean {
  return (Object.keys(a) as Array<keyof CampaignSetupDraft>).every((key) => a[key] === b[key]);
}

/** A stored numeric value, re-normalized; a non-number (or a blank the field cannot hold) is malformed. */
function restoredNumber(field: CampaignNumericField, raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  if (raw.trim() === '') return CAMPAIGN_NUMERIC_BOUNDS[field].fallback === null ? '' : null;
  if (!Number.isFinite(Number(raw))) return null;
  return normalizeCampaignNumericValue(field, raw).value;
}

function restoredTime(raw: unknown): string | null {
  return typeof raw === 'string' && /^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/.test(raw) ? raw : null;
}

/**
 * The valid fields of the stored draft, or null when there is none. Each
 * field is validated on its own: a malformed one is ignored, the rest apply.
 */
export function readCampaignDraft(): Partial<CampaignSetupDraft> | null {
  let raw: string | null;
  try {
    raw = window.sessionStorage.getItem(CAMPAIGN_DRAFT_STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null) return null;
  const { v, setup } = parsed as { v?: unknown; setup?: unknown };
  if (v !== DRAFT_VERSION || typeof setup !== 'object' || setup === null) return null;
  const stored = setup as Record<string, unknown>;
  const draft: Partial<CampaignSetupDraft> = {};
  for (const field of NUMERIC_FIELDS) {
    const value = restoredNumber(field, stored[field]);
    if (value !== null) draft[field] = value;
  }
  for (const field of TIME_FIELDS) {
    const value = restoredTime(stored[field]);
    if (value !== null) draft[field] = value;
  }
  if (typeof stored.marketHouseholdTogether === 'boolean') {
    draft.marketHouseholdTogether = stored.marketHouseholdTogether;
  }
  return Object.keys(draft).length > 0 ? draft : null;
}

/** The setup with a restored draft applied (the copy fields are never in it). */
export function applyCampaignDraft(
  setup: CampaignSetupState,
  draft: Partial<CampaignSetupDraft> | null,
): CampaignSetupState {
  return draft ? { ...setup, ...draft } : setup;
}

function writeCampaignDraft(serialized: string | null): void {
  try {
    if (serialized === null) window.sessionStorage.removeItem(CAMPAIGN_DRAFT_STORAGE_KEY);
    else window.sessionStorage.setItem(CAMPAIGN_DRAFT_STORAGE_KEY, serialized);
  } catch {
    // Storage can be unavailable in privacy-restricted contexts.
  }
}

/** Forget the draft now (a save, or Reset). */
export function clearCampaignDraft(): void {
  writeCampaignDraft(null);
}

/**
 * Keep sessionStorage in step with the setup: the persisted subset while it
 * differs from the last saved setup, nothing once it matches. An
 * external-system sync with no setState, debounced 300 ms. A write still
 * waiting when the page goes away (unmount or pagehide) is flushed, so the
 * last edit is not lost; a write that already happened is never repeated,
 * so the flush cannot bring back a draft the actor-change clear removed.
 */
export function useCampaignDraftPersistence(setup: CampaignSetupState, saved: CampaignSetupState): void {
  const draft = campaignDraftOf(setup);
  const serialized = draftsEqual(draft, campaignDraftOf(saved))
    ? null
    : JSON.stringify({ v: DRAFT_VERSION, setup: draft });
  const pending = useRef<{ serialized: string | null } | null>(null);
  useEffect(() => {
    pending.current = { serialized };
    const timer = window.setTimeout(() => {
      pending.current = null;
      writeCampaignDraft(serialized);
    }, CAMPAIGN_DRAFT_WRITE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [serialized]);
  useEffect(() => {
    const flush = () => {
      const waiting = pending.current;
      pending.current = null;
      if (waiting) writeCampaignDraft(waiting.serialized);
    };
    window.addEventListener('pagehide', flush);
    return () => {
      window.removeEventListener('pagehide', flush);
      flush();
    };
  }, []);
}
