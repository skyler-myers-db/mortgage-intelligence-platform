/**
 * A composed plan step's inputs in lender copy (audit 2026-09-21 `critic-01`):
 * the server's plan digest binds each step's params, so the card shows them;
 * 'Run this plan' runs exactly the steps "with the inputs shown".
 */

export type SegmentLabeler = (code: string) => string;

/**
 * The inputs a planner-exposed tool can take, in display order. Closed on
 * purpose: every value is a registry-validated closed vocabulary. The only
 * free-text param (`address_line`) belongs to a tool the planner can never
 * compose, and a key outside this list is never rendered.
 */
const PARAM_LABELS = [
  ['states', 'States'],
  ['segment_codes', 'Segments'],
  ['segment_mode', 'Match'],
  ['min_opportunity_score', 'Min score'],
] as const;

/** A step's inputs in one line, e.g. `States: IL, TX · Match: any segment`. */
export function formatStepParams(params: Record<string, unknown>, segmentLabel: SegmentLabeler = (code) => code): string {
  const parts: string[] = [];
  for (const [key, label] of PARAM_LABELS) {
    if (!Object.prototype.hasOwnProperty.call(params, key)) continue;
    const value = params[key];
    const list = (Array.isArray(value) ? value : [value]).map(String);
    let text = list.join(', ');
    if (key === 'states') text = text || 'current coverage';
    else if (key === 'segment_codes') text = list.map(segmentLabel).join(', ');
    else if (key === 'segment_mode') text = value === 'all' ? 'all segments' : 'any segment';
    parts.push(`${label}: ${text}`);
  }
  return parts.join(' · ') || 'No inputs (current coverage)';
}
