/**
 * Ask Genie and Growth Agent fixtures.
 *
 * The conversation starts EMPTY: `/api/genie/start`, the session history and
 * the reviewed workflow catalog are registered, but no answer turn is. A lane
 * that needs an answer registers `POST /api/genie/message/submit` (and
 * `/progress`, `/complete` for an async turn) with its own GenieResult.
 *
 * Fixture answers are test data only. Nothing here may be used to overlay or
 * replace a live Genie turn in the running app (CLAUDE.md live-first rule).
 */
import type {
  GenieSessionSummary,
  GenieStartResult,
  GrowthAgentHomeResponse,
  GrowthAgentMonitor,
  GrowthAgentWorkflow,
} from '../../../../src/types';
import { fixture, json, type FixtureEntry } from '../mockApi';

export const GENIE_CONVERSATION_ID = 'fixture-conversation-0001';

const TRUSTED_ASSETS = [
  'mip.gold.borrower_360',
  'mip.gold.evidence_events',
  'mip.semantics.borrower_opportunity_metric_view',
];

function workflow(
  id: GrowthAgentWorkflow['id'],
  title: string,
  objective: string,
  trigger: string,
  action: string,
  route: string,
): GrowthAgentWorkflow {
  return {
    id,
    title,
    objective,
    trigger_label: trigger,
    action_label: action,
    source_assets: ['mip.gold.borrower_360'],
    default_route: route,
    proof_points: ['Governed SQL', 'Human approval required'],
    cadence_options: ['daily', 'weekly'],
  };
}

export const GROWTH_AGENT_HOME: GrowthAgentHomeResponse = {
  workflows: [
    workflow('daily_refi_brief', 'Daily refi brief', "Rank today's prime refi candidates and hand the cohort to the Lead Queue.", 'Rate spread >= 75 bps', 'Open cohort in Lead Queue', '/lead-queue?segment=itm'),
    workflow('high_equity_heloc_watch', 'High-equity HELOC watch', 'Watch high-equity borrowers with HELOC propensity.', 'Equity >= 40%', 'Review HELOC cohort', '/lead-queue?segment=equity'),
    workflow('listing_watch', 'Listing watch', 'Surface borrowers with a new active listing.', 'Active MLS listing', 'Review listed cohort', '/lead-queue?segment=listed'),
  ],
  monitors: [],
  capabilities: [
    { key: 'genie_conversation', label: 'Genie Conversation API', ga: true, status: 'configured', claimable: true, detail: 'Space configured (fixture).' },
    { key: 'agent_bricks', label: 'Agent Bricks supervisor', ga: false, status: 'not_provisioned', claimable: false, detail: 'Not provisioned in this workspace.' },
  ],
};

export const genieFixtures: FixtureEntry[] = [
  fixture('POST', '/api/genie/start', () =>
    json<GenieStartResult>({
      conversation_id: GENIE_CONVERSATION_ID,
      sample_questions: [
        'Which states have the most prime refi candidates?',
        'How many HELOC-intent borrowers have at least 40% equity?',
        'Show retention-risk borrowers with recent competitor lien activity.',
      ],
      trusted_assets: TRUSTED_ASSETS,
    }),
  ),
  fixture('GET', '/api/genie/sessions', () => json<{ sessions: GenieSessionSummary[] }>({ sessions: [] })),
  fixture('GET', '/api/growth-agent', () => json<GrowthAgentHomeResponse>(GROWTH_AGENT_HOME)),
  fixture('GET', '/api/growth-agent/monitors', () => json<GrowthAgentMonitor[]>([])),
];
