import type { DrawerSource } from '../components/AppContext';
import type { SegmentSummary } from '../types';
import { SEGMENT_EVIDENCE_SPECS, SEGMENT_GATE_COPY } from './segmentEvidenceSpecs';

export function segmentEvidenceSource(
  segment: Pick<
    SegmentSummary,
    'code' | 'name' | 'count' | 'avg_score' | 'description' | 'source_status' | 'source_name'
  >,
): DrawerSource {
  const spec = SEGMENT_EVIDENCE_SPECS[segment.code];
  const predicate = spec?.[0];
  const sources = spec?.[1];
  const gated = segment.source_status === 'not_connected' || segment.source_status === 'not_licensed';
  const gateCopy = gated ? SEGMENT_GATE_COPY[segment.source_status ?? ''] : null;
  return {
    title: `${segment.name} evidence`,
    short: `segment_population.${segment.code}`,
    assetKey: 'segment_population',
    assetPath: 'mip.gold.segment_population',
    lineageFamily: 'segment_population',
    description: gateCopy
      ? `${segment.description} ${gateCopy}`
      : `${segment.description} Live total from mip.gold.segment_population.`,
    lineage: [
      ...(sources?.map(([layer, name, meta]) => ({ layer, name, meta })) ?? []),
      { layer: 'GOLD', name: 'mip.gold.borrower_360', meta: predicate ?? 'segment membership flag' },
      { layer: 'GOLD', name: 'mip.gold.segment_population' },
    ],
    signals: gated
      ? [
          {
            label: 'Source status',
            source: 'source_readiness',
            value: `${segment.source_name ?? 'source'}: ${segment.source_status === 'not_licensed' ? 'unlicensed' : 'not connected'}`,
          },
        ]
      : [
          { label: 'Members', source: `count['${segment.code}']`, value: segment.count.toLocaleString() },
          { label: 'Average score', source: 'avg_score', value: String(segment.avg_score) },
        ],
  };
}
