import { useCallback, useEffect, useState, useSyncExternalStore, type SetStateAction } from 'react';
import {
  actorScopeStatus,
  readActorScoped,
  removeActorScoped,
  subscribeActorScope,
  updateActorScoped,
} from '../lib/actorScope';
import type { CampaignSetupState } from './portfolio-builder.logic';
import { campaignSetupsEqual } from './portfolio-builder.unsaved';

/**
 * The campaign-setup draft (2026-09-21 audit critic-v3): an unsaved setup
 * survives an in-app navigation and a reload of this tab, per actor.
 *
 * The draft is a PRIVATE_SESSION key of lib/actorScope (read and written only
 * through its gate): it belongs to the actor this tab observed, the gate
 * removes it when another actor is observed, and nothing here names browser
 * storage. Its value is JSON {v: 1, buildKey, savedAt, setup}. `buildKey` is
 * the preview criteria the setup's message variants were applied under (the
 * criteria their provenance tokens bind), kept while the copy is unchanged.
 *
 * Restore runs once: at mount when the gate is open, else when it first
 * opens, and then only if the setup is still the baseline. The same build
 * restores the whole setup; another build restores the delivery settings
 * only (the copy, its provenance and generation stay the defaults). Until
 * that check has run NOTHING is written or removed: a removal queued while
 * the gate is pending replays before 'opened' and would delete the draft the
 * check is about to read. After it, the setup is written while it differs
 * from the saved baseline and removed when it equals it; a save and Reset
 * remove it too.
 *
 * deviation:campaign-draft-restore (the 'Draft restored' chip and Reset are
 * rendered by portfolio-builder.campaign-setup.tsx).
 */

export const CAMPAIGN_DRAFT_KEY = 'mip.portfolio.campaignDraft.v1';

/** A setup restored from the draft: the whole setup, or the delivery settings only. */
export interface CampaignDraftRestore {
  kind: 'full' | 'partial';
  /** The draft's message variants belonged to another build and were left out. */
  variantsDropped: boolean;
}

interface StoredDraft {
  v: 1;
  buildKey: string;
  savedAt: number;
  setup: CampaignSetupState;
}

interface DraftState {
  setup: CampaignSetupState;
  /** The restore check has run: from here on the draft is written and removed. */
  checked: boolean;
  restore: CampaignDraftRestore | null;
}

const TEXT_FIELDS = [
  'subjectA',
  'subjectB',
  'bodyA',
  'bodyB',
  'holdoutPct',
  'startLocal',
  'endLocal',
  'budget',
  'emailCost',
  'smsCost',
  'mailCost',
  'generatorLabel',
] as const satisfies ReadonlyArray<keyof CampaignSetupState>;
/** The copy and what binds it to a build. */
const COPY_FIELDS = [
  'subjectA',
  'subjectB',
  'bodyA',
  'bodyB',
  'generationMode',
  'generatorLabel',
  'provenanceTokenA',
  'provenanceTokenB',
] as const satisfies ReadonlyArray<keyof CampaignSetupState>;
const GENERATION_MODES: ReadonlyArray<CampaignSetupState['generationMode']> = ['supervisor', 'reviewed_fallback', 'operator'];
const MAX_TEXT = 4096;
const MAX_BUILD_KEY = 16_384;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function provenanceToken(value: unknown): string | null | undefined {
  if (value === null) return null;
  return typeof value === 'string' && value.length >= 32 && value.length <= MAX_TEXT ? value : undefined;
}

/** Every field type-checked; anything else is no draft. */
function parseSetup(value: unknown): CampaignSetupState | null {
  if (!isRecord(value)) return null;
  const text: Partial<Record<(typeof TEXT_FIELDS)[number], string>> = {};
  for (const key of TEXT_FIELDS) {
    const field = value[key];
    if (typeof field !== 'string' || field.length > MAX_TEXT) return null;
    text[key] = field;
  }
  const mode = GENERATION_MODES.find((candidate) => candidate === value.generationMode);
  const tokenA = provenanceToken(value.provenanceTokenA);
  const tokenB = provenanceToken(value.provenanceTokenB);
  if (typeof value.marketHouseholdTogether !== 'boolean' || !mode || tokenA === undefined || tokenB === undefined) return null;
  return {
    ...(text as Record<(typeof TEXT_FIELDS)[number], string>),
    marketHouseholdTogether: value.marketHouseholdTogether,
    generationMode: mode,
    provenanceTokenA: tokenA,
    provenanceTokenB: tokenB,
  };
}

export function parseCampaignDraft(raw: string | null): StoredDraft | null {
  if (raw === null) return null;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(value) || value.v !== 1) return null;
  const { buildKey, savedAt } = value;
  if (typeof buildKey !== 'string' || buildKey.length > MAX_BUILD_KEY) return null;
  if (typeof savedAt !== 'number' || !Number.isFinite(savedAt)) return null;
  const setup = parseSetup(value.setup);
  return setup ? { v: 1, buildKey, savedAt, setup } : null;
}

function sameFields(a: CampaignSetupState, b: CampaignSetupState, keys: ReadonlyArray<keyof CampaignSetupState>): boolean {
  return keys.every((key) => a[key] === b[key]);
}

/** The checked state for `buildKey`: the stored draft restored onto `baseline`, or the baseline. */
function restoredState(buildKey: string, baseline: CampaignSetupState): DraftState {
  const draft = parseCampaignDraft(readActorScoped('session', CAMPAIGN_DRAFT_KEY));
  if (draft === null) return { setup: baseline, checked: true, restore: null };
  if (draft.buildKey === buildKey) {
    return { setup: draft.setup, checked: true, restore: { kind: 'full', variantsDropped: false } };
  }
  // Another build: the delivery settings only, never the copy.
  const { holdoutPct, startLocal, endLocal, budget, emailCost, smsCost, mailCost, marketHouseholdTogether } = draft.setup;
  const setup = { ...baseline, holdoutPct, startLocal, endLocal, budget, emailCost, smsCost, mailCost, marketHouseholdTogether };
  const variantsDropped = !sameFields(draft.setup, baseline, COPY_FIELDS);
  const restored = variantsDropped || !campaignSetupsEqual(setup, baseline);
  return { setup, checked: true, restore: restored ? { kind: 'partial', variantsDropped } : null };
}

const subscribeGate = (onChange: () => void) => subscribeActorScope(() => onChange());

export interface CampaignDraft {
  setup: CampaignSetupState;
  setSetup: (action: SetStateAction<CampaignSetupState>) => void;
  /** Set from a restore until Reset, a save, or the setup is the baseline again (unless variants were left out). */
  restore: CampaignDraftRestore | null;
  /** Back to the saved baseline (defaults before any save); the draft is removed. */
  reset: () => void;
  /** The setup was saved with a build: the draft is removed. */
  saved: () => void;
}

/**
 * The route's campaign setup, kept as this tab's draft. `buildKey` is the
 * committed preview criteria (JSON), `baseline` the setup last saved with a
 * build (the defaults before any save).
 */
export function useCampaignDraft(buildKey: string, baseline: CampaignSetupState): CampaignDraft {
  const gate = useSyncExternalStore(subscribeGate, actorScopeStatus, actorScopeStatus);
  const [state, setState] = useState<DraftState>(() =>
    gate === 'open' ? restoredState(buildKey, baseline) : { setup: baseline, checked: false, restore: null },
  );
  // The route mounted before the gate opened: restore once it does, unless
  // the operator has already started on the setup.
  if (!state.checked && gate === 'open') {
    setState(campaignSetupsEqual(state.setup, baseline) ? restoredState(buildKey, baseline) : { ...state, checked: true });
  }
  const { checked, setup } = state;

  useEffect(() => {
    if (!checked) return;
    if (campaignSetupsEqual(setup, baseline)) {
      removeActorScoped('session', CAMPAIGN_DRAFT_KEY);
      return;
    }
    updateActorScoped('session', CAMPAIGN_DRAFT_KEY, (raw) => {
      const previous = parseCampaignDraft(raw);
      // The variants stay bound to the build they were applied under.
      const key = previous !== null && sameFields(previous.setup, setup, COPY_FIELDS) ? previous.buildKey : buildKey;
      const draft: StoredDraft = { v: 1, buildKey: key, savedAt: Date.now(), setup };
      return JSON.stringify(draft);
    });
  }, [baseline, buildKey, checked, setup]);

  const setSetup = useCallback((action: SetStateAction<CampaignSetupState>) => {
    setState((current) => ({ ...current, setup: typeof action === 'function' ? action(current.setup) : action }));
  }, []);
  const reset = useCallback(() => {
    removeActorScoped('session', CAMPAIGN_DRAFT_KEY);
    setState((current) => ({ ...current, setup: baseline, restore: null }));
  }, [baseline]);
  const saved = useCallback(() => {
    removeActorScoped('session', CAMPAIGN_DRAFT_KEY);
    setState((current) => ({ ...current, restore: null }));
  }, []);

  const shown = state.restore !== null && (state.restore.variantsDropped || !campaignSetupsEqual(setup, baseline));
  return { setup, setSetup, restore: shown ? state.restore : null, reset, saved };
}
