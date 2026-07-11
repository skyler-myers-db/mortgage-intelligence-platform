import { Icon } from '../Icon';
import { Chip } from '../Primitives';
import type { CampaignGeoPrefill } from '../../lib/campaignPrefill';
import { safeSegmentName } from '../../lib/segmentMetadata';

/**
 * Draft-context surface for a geo → campaign handoff (S9).
 *
 * Rendered at the top of the Portfolio Builder when the URL carries a
 * valid `prefill_source=geo-drilldown` payload (see lib/campaignPrefill.ts).
 * The copy is deliberately honest about today's boundary: the STATE
 * predicate applies to the build immediately (it rides the existing
 * `states=` deep-link param); county/ZIP narrowing and the segment
 * carry-over are saved as draft context and become build predicates when
 * the dedicated campaign builder (S10) ships.
 */
export function CampaignPrefillBanner({
  prefill,
  error,
}: {
  prefill: CampaignGeoPrefill | null;
  error?: string;
}) {
  if (error) {
    // A marked-but-malformed link: never apply half a context. One muted,
    // honest line instead of a silent drop.
    return (
      <div className="muted fs-12" role="status" data-testid="campaign-prefill-error">
        Ignored an invalid campaign prefill link.
      </div>
    );
  }
  if (!prefill) return null;
  const segmentNames = prefill.segmentCodes
    .map((code) => safeSegmentName(code) ?? code)
    .filter(Boolean);
  return (
    <div className="surface" data-testid="campaign-prefill-banner">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="pin" size={14} />
          </div>
          <div>
            <div className="h-4">Campaign draft from geography drill-down</div>
            <div className="muted fs-12">
              State filter applied to this build. County/ZIP narrowing and segment
              carry-over are saved as draft context — they become build predicates
              when the campaign builder (S10) ships.
            </div>
          </div>
        </div>
      </div>
      <div className="surface__body">
        <div className="chip-row">
          <Chip variant="success" icon="pin">
            State {prefill.state} — applied
          </Chip>
          {prefill.countyFips && (
            <Chip variant="neutral" icon="pin">
              {/* countyName arrives display-ready (e.g. "Cook County"). */}
              {prefill.countyName
                ? `${prefill.countyName} · FIPS ${prefill.countyFips}`
                : `County FIPS ${prefill.countyFips}`}{' '}
              — draft context
            </Chip>
          )}
          {prefill.zip && (
            <Chip variant="neutral" icon="pin">
              ZIP {prefill.zip} — draft context
            </Chip>
          )}
          {segmentNames.length > 0 && (
            <Chip variant="neutral" icon="layers">
              Segments ({prefill.segmentMode}): {segmentNames.join(', ')} — draft context
            </Chip>
          )}
          {prefill.leadCount !== null && (
            // The overlay snapshot is segment-agnostic; the "(all segments)"
            // qualifier keeps these counts honest next to the segment chips.
            <Chip variant="neutral" icon="db">
              {prefill.leadCount.toLocaleString()} leads
              {prefill.unattendedCount !== null
                ? ` · ${prefill.unattendedCount.toLocaleString()} unattended`
                : ''}{' '}
              at draft time (all segments)
            </Chip>
          )}
        </div>
      </div>
    </div>
  );
}
