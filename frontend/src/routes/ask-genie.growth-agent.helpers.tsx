import { Chip, EvidenceChip } from '../components/Primitives';
import { drawerForAsset } from '../lib/drawerSources';
import type {
  GrowthAgentSegmentCode,
  GrowthAgentWorkflowId,
} from '../types';

// Friendly name + catalog-relative suffix. The backend returns the concrete
// catalog-qualified paths for this deployment on /api/genie/start.
const TRUSTED_ASSET_DEFS: Array<{ label: string; suffix: string }> = [
  { label: 'Ranked lead population',       suffix: 'gold.lead_population' },
  { label: 'Segment rollups',              suffix: 'gold.segment_population' },
  { label: 'Opportunity scores',           suffix: 'gold.lead_scores' },
  { label: 'Borrower 360 profile',         suffix: 'gold.borrower_360' },
  { label: 'Borrower dossier',             suffix: 'gold.borrower_dossier' },
  { label: 'Source evidence',              suffix: 'gold.evidence_events' },
  { label: 'Source readiness',             suffix: 'gold.source_readiness' },
  { label: 'Lock-in cohort',               suffix: 'gold.lockin_cohort' },
  { label: 'Lead-generation metric view',  suffix: 'semantics.lead_generation_metric_view' },
  { label: 'Segment performance view',     suffix: 'semantics.segment_performance_metric_view' },
  { label: 'Borrower opportunity view',    suffix: 'semantics.borrower_opportunity_metric_view' },
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
  return TRUSTED_ASSET_DEFS.map((asset) => ({
    label: asset.label,
    path: startAssets?.find((path) => path.endsWith(`.${asset.suffix}`))
      ?? asset.suffix,
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
    if (!STATE_TOKEN_RE.test(token)) {
      invalid.push(token);
      return;
    }
    const state = token.toUpperCase();
    if (!states.includes(state)) states.push(state);
  });
  return { states, invalid };
}

function sourceAssetLabel(asset: string): string {
  const parts = asset.split('.').filter(Boolean);
  return parts.slice(-2).join('.') || asset;
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
  const label = sourceAssetLabel(asset);
  if (source) return <EvidenceChip key={asset} source={source}>{label}</EvidenceChip>;
  return <Chip key={asset} variant="neutral" icon="db">{label}</Chip>;
}

export function capabilityStatusText(status: string): string {
  switch (status) {
    case 'available':
      return 'Available';
    case 'configured':
      return 'Configured';
    case 'not_provisioned':
      return 'Not provisioned';
    case 'preview_mirror':
      return 'Roadmap mirror';
    case 'hidden':
      return 'Hidden';
    default:
      return status.replace('_', ' ');
  }
}
