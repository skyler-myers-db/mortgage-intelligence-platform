import { Chip, EvidenceChip } from '../components/Primitives';
import { drawerForAsset } from '../lib/drawerSources';
import { friendlyAssetLabel } from '../lib/assetLabels';
import { isUspsStateCode } from '../lib/uspsStates';
import type {
  GrowthAgentSegmentCode,
  GrowthAgentWorkflowId,
} from '../types';

// Catalog-relative suffixes for the trusted-asset panel (ordering). Friendly
// labels come from the shared assetLabels helper; the backend returns the
// concrete catalog-qualified paths for this deployment on /api/genie/start.
const TRUSTED_ASSET_SUFFIXES: string[] = [
  'gold.lead_population',
  'gold.segment_population',
  'gold.lead_scores',
  'gold.borrower_360',
  'gold.borrower_dossier',
  'gold.evidence_events',
  'gold.source_readiness',
  'gold.lockin_cohort',
  'semantics.lead_generation_metric_view',
  'semantics.segment_performance_metric_view',
  'semantics.borrower_opportunity_metric_view',
];

export const CUSTOM_SEGMENTS: Array<{ code: GrowthAgentSegmentCode; label: string }> = [
  { code: 'itm', label: 'Prime Refi Candidates' },
  { code: 'listed', label: 'Listed for Sale' },
  { code: 'permit', label: 'HELOC Intent' },
  { code: 'investor', label: 'Investor / Multi-Property' },
  { code: 'equity', label: 'Home Equity Candidate' },
  { code: 'retention', label: 'Retention Risk' },
];

const STATE_TOKEN_RE = /^[A-Za-z]{2}$/;

export function trustedAssetsForCatalog(
  startAssets: string[] | undefined,
): Array<{ label: string; path: string }> {
  return TRUSTED_ASSET_SUFFIXES.map((suffix) => ({
    label: friendlyAssetLabel(suffix),
    path: startAssets?.find((path) => path.endsWith(`.${suffix}`)) ?? suffix,
  }));
}

export function buildTrustedAssetQuestion(asset: { label: string; path: string }): string {
  return `Using ${asset.path}, summarize what this trusted asset can answer for Module 0 and show the most useful fields for ${asset.label.toLowerCase()}.`;
}

export function parseGrowthAgentStateInput(value: string): { states: string[]; invalid: string[] } {
  const tokens = value
    .split(/[,\s]+/)
    .map((token) => token.trim())
    .filter(Boolean);
  const states: string[] = [];
  const invalid: string[] = [];
  tokens.forEach((token) => {
    if (!STATE_TOKEN_RE.test(token) || !isUspsStateCode(token)) {
      invalid.push(token);
      return;
    }
    const state = token.toUpperCase();
    if (!states.includes(state)) states.push(state);
  });
  return { states, invalid };
}

export function workflowIcon(workflowId: GrowthAgentWorkflowId) {
  if (workflowId === 'borrower_dossier_review') return 'doc';
  if (workflowId === 'listing_watch') return 'tag';
  if (workflowId === 'competitor_recapture_monitor') return 'target';
  if (workflowId === 'high_equity_heloc_watch') return 'equity';
  if (workflowId === 'branch_capacity_review') return 'user';
  if (workflowId === 'source_freshness_sentinel') return 'db';
  if (workflowId === 'custom_segment_watch') return 'filter';
  return 'money';
}

export function renderSourceAssetChip(asset: string) {
  const source = drawerForAsset(asset);
  const label = friendlyAssetLabel(asset);
  // Plain-English label on the surface; the fully-qualified UC id stays in the
  // title tooltip (and remains visible in /admin-config + /data-estate detail).
  if (source) return <EvidenceChip key={asset} source={source} title={asset}>{label}</EvidenceChip>;
  return <Chip key={asset} variant="neutral" icon="db" title={asset}>{label}</Chip>;
}
