/**
 * Refusal-card copy and rephrase chips per coarse refusal family
 * (audit 2026-09-21 `genie-05`).
 *
 * The sentences explain what the product CAN answer; they never say the guard
 * was wrong, and they never name a guard rule or a matched term. The chips
 * are the reviewed rewordings for each family and live in
 * `genieRefusalChips.json`, which `tests/unit/test_genie_refusal_chips.py`
 * runs through the real backend guard battery: every chip here is proven
 * to reach live Genie before it is offered.
 */
import type { GenieAnswer, GenieRefusalReason } from '../../types';
import chips from './genieRefusalChips.json';

export interface GenieRefusalFamily {
  /** Short label for the card header. */
  title: string;
  /** One plain-language sentence: what Genie answers instead. */
  sentence: string;
}

export const GENIE_REFUSAL_FAMILIES: Record<GenieRefusalReason, GenieRefusalFamily> = {
  protected_class: {
    title: 'Fair-lending scope',
    sentence:
      'Genie selects, ranks, and explains borrowers on mortgage, lien, equity, segment, geography, and offer signals, not on protected-class attributes or their proxies.',
  },
  unreviewed_criterion: {
    title: 'Outside the reviewed vocabulary',
    sentence:
      'That selection criterion is not in the reviewed Module 0 vocabulary. Ask with the reviewed mortgage, lien, equity, segment, trigger, and offer signals.',
  },
  pii_request: {
    title: 'Personal identifiers stay masked',
    sentence:
      'Genie returns aggregates and masked borrower IDs with score, segment, offer, and evidence. It does not look up names, street addresses, or contact details.',
  },
  instruction_override: {
    title: 'Scoped analytics only',
    sentence:
      'Genie answers scoped Module 0 mortgage questions over trusted lead, lien, equity, segment, and offer signals. Ask the analytics question directly.',
  },
  outreach_instruction: {
    title: 'Outreach stays in the governed workflow',
    sentence:
      'Genie can size a cohort, explain why borrowers qualify, and open an audited draft campaign. Borrower-facing email or SMS copy is written in the Offer Orchestrator with human approval.',
  },
  scope_bypass: {
    title: 'Read-only over trusted assets',
    sentence:
      'This space reads governed Module 0 assets only. Ask a borrower, segment, geography, trigger, or offer question rather than about schemas, tables, or SQL.',
  },
  out_of_scope: {
    title: 'Module 0 mortgage analytics',
    sentence:
      "Genie covers this lender's borrower segments, scores, geography, triggers, and offers across the current Cotality coverage. Ask about that book of business.",
  },
  output_policy: {
    title: 'Answer withheld before display',
    sentence:
      'Genie produced a result that did not pass the governed checks before display, so it was not shown. A narrower aggregate question over the trusted assets usually does.',
  },
  unknown: {
    title: 'Question withheld',
    sentence:
      'This question stopped before a live result. Ask about borrower segments, scores, geography, triggers, and offers across the current Cotality coverage.',
  },
};

const CHIPS: Record<GenieRefusalReason, readonly string[]> = chips;

/** Reviewed, guard-validated rewordings for one family (2-3 per family). */
export function refusalRephraseChips(reason: GenieRefusalReason): readonly string[] {
  return CHIPS[reason] ?? CHIPS.unknown;
}

const REASONS = new Set<string>(Object.keys(GENIE_REFUSAL_FAMILIES));

function isRefusalReason(value: unknown): value is GenieRefusalReason {
  return typeof value === 'string' && REASONS.has(value);
}

/** True for the two withheld sources the card renders for. */
export function isWithheldGenieSource(source: string | null | undefined): boolean {
  return source === 'refused' || source === 'policy_blocked';
}

/**
 * The family to render. An older backend sends no `refusal_reason`; a
 * `policy_blocked` source without one is still an output-policy withhold,
 * and any other refusal without one renders the generic family.
 */
export function refusalFamilyFor(payload: Pick<GenieAnswer, 'source' | 'refusal_reason'>): GenieRefusalReason {
  if (isRefusalReason(payload.refusal_reason)) return payload.refusal_reason;
  return payload.source === 'policy_blocked' ? 'output_policy' : 'unknown';
}
