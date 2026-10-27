/**
 * @vitest-environment happy-dom
 *
 * The queue rows' tooltip descriptions (post-W5 tooltip-render-cost): every
 * row trigger keeps the accessible description it had, and the rows hold one
 * description node per distinct text (ui/tooltipDescriptions.ts), so a
 * window shift adds only what its new row brought. At 550dcf97 each of a
 * row's triggers portalled its own styled `<span>` into <body>: seven per row.
 * The rendered-layer twin is tests/e2e/fixture/tooltip-descriptions.fixture.spec.ts.
 */
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { safeSegmentName } from '../../lib/segmentMetadata';
import { mount } from '../../test/render';
import { LeadTableRow } from './LeadTableRow';

vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer: () => undefined, showEvidence: true, showConfidence: true, setLastBorrowerId: () => undefined }),
}));
vi.mock('react-router', () => ({
  Link: ({ children, to, ...props }: { children: ReactNode; to: string }) => <a href={to} {...props}>{children}</a>,
}));
vi.mock('./ScoreAnatomyGate', () => ({ ScoreAnatomyGate: () => null }));

const SIGNAL_STRENGTH_NOTE = 'Signal strength is a deterministic average of the five scoring sub-scores, not a'
  + ' statistical confidence interval.';
const SCORE_NOTE = 'Opportunity score ranks lead strength on a 0-100 scale. Scores of 75+ are the strongest review'
  + ' candidates and include economics, intent, fit, relationship, and evidence.';

/** Seven rows: a property ref each, two cities and two segments between them, one offer. */
const LEADS: LeadSummary[] = Array.from({ length: 7 }, (_, index) => ({
  borrower_id: `B-00000000000${String(index).padStart(2, '0')}`,
  display_name: 'Borrower',
  city: index % 2 === 0 ? 'Columbus' : 'Dayton',
  state: 'OH',
  zip: '43004',
  clip: `clip_ref_000${index}`,
  segment_codes: [index % 2 === 0 ? 'itm' : 'equity'],
  equity_estimate: 250000,
  rate_spread_bps: 80,
  opportunity_score: 86,
  confidence: 88,
  recommended_offer_code: 'refi',
  recommended_offer: 'Rate refinance',
  why_now: 'Rate spread and equity support review.',
  evidence_ids: ['ev-1'],
  approval_status: 'pending',
  outreach_status: 'queued',
}));

const noop = () => undefined;

function Rows({ leads }: { leads: LeadSummary[] }) {
  return (
    <>
      {leads.map((lead, index) => (
        <LeadTableRow
          key={lead.borrower_id}
          lead={lead}
          virtualIndex={index}
          isOpen={false}
          approval={undefined}
          isSelected={false}
          isSelectable
          isApprovalEligible
          bulkApproving={false}
          salesBusy={false}
          salesTeamCount={1}
          approverGate={null}
          onToggleRow={noop}
          onToggleSelect={noop}
          onApprove={noop}
          onReject={noop}
          onOpenDisposition={noop}
          onAssignmentUpdate={noop}
        />
      ))}
    </>
  );
}

/** Each row trigger kind and the description it must have. */
const KINDS: Array<{ kind: string; selector: string; expected: (lead: LeadSummary) => string }> = [
  { kind: 'property ref', selector: '.lead-table__clip', expected: (lead) => lead.clip ?? '' },
  { kind: 'city', selector: '.lead-table__city', expected: (lead) => `${lead.city}, ${lead.state}` },
  { kind: 'segment chip', selector: '.chip--segment', expected: (lead) => safeSegmentName(lead.segment_codes[0]) ?? '' },
  { kind: 'offer', selector: '.lead-table__offer', expected: (lead) => offerDisplayLabel(lead.recommended_offer_code, lead.recommended_offer) },
  { kind: 'score', selector: '.score', expected: () => SCORE_NOTE },
  { kind: 'signal strength', selector: '.conf', expected: () => SIGNAL_STRENGTH_NOTE },
  { kind: 'Reject', selector: '.lead-table__reject', expected: () => 'Reject' },
];

function accessibleDescription(element: Element): string {
  return (element.getAttribute('aria-describedby') ?? '')
    .split(/\s+/)
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? `<missing ${id}>`)
    .join(' ');
}

function rowTriggers(): Element[] {
  return Array.from(document.querySelectorAll('tr[data-borrower-row] [data-tooltip]'));
}

/** The description nodes the rows' triggers name, and the distinct texts they carry. */
function census(): { nodes: Set<Element>; texts: Set<string> } {
  const nodes = new Set<Element>();
  const texts = new Set<string>();
  for (const trigger of rowTriggers()) {
    const node = document.getElementById(trigger.getAttribute('data-tooltip') ?? '');
    if (node) nodes.add(node);
    texts.add(node?.textContent ?? '');
  }
  return { nodes, texts };
}

async function renderRows(leads: LeadSummary[]) {
  const table = document.body.appendChild(document.createElement('table'));
  const tbody = table.appendChild(document.createElement('tbody'));
  const view = await mount(<Rows leads={leads} />, { container: tbody });
  return { ...view, table };
}

let cleanup: (() => void) | null = null;

afterEach(() => {
  cleanup?.();
  cleanup = null;
});

describe('LeadTableRow: tooltip descriptions', () => {
  it('describes every row trigger by the text it had', async () => {
    const { table, unmount } = await renderRows(LEADS);
    cleanup = () => { unmount(); table.remove(); };

    for (const lead of LEADS) {
      const tr = document.querySelector(`tr[data-borrower-row="${lead.borrower_id}"]`);
      const triggers = Array.from(tr?.querySelectorAll('[data-tooltip]') ?? []);
      for (const trigger of triggers) {
        const kinds = KINDS.filter((entry) => trigger.matches(entry.selector));
        expect(kinds.map((entry) => entry.kind), `${lead.borrower_id}: ${trigger.className}`).toHaveLength(1);
        expect(accessibleDescription(trigger), `${lead.borrower_id} ${kinds[0].kind}`).toBe(kinds[0].expected(lead));
      }
      for (const entry of KINDS) {
        expect(triggers.some((trigger) => trigger.matches(entry.selector)), `${lead.borrower_id} has a ${entry.kind}`).toBe(true);
      }
    }
  });

  it('holds one description node per distinct text across the rows, not one per trigger', async () => {
    const { table, unmount } = await renderRows(LEADS);
    cleanup = () => { unmount(); table.remove(); };

    const { nodes, texts } = census();
    // 7 rows x 7 triggers; 7 refs + 2 cities + 2 segments + offer + score + signal + Reject.
    expect(rowTriggers()).toHaveLength(49);
    expect(texts.size).toBe(15);
    expect(nodes.size).toBe(texts.size);
  });

  it('a window shift (one row out, one in) adds only the new row\'s own text', async () => {
    const { table, unmount, rerender } = await renderRows(LEADS.slice(0, 6));
    cleanup = () => { unmount(); table.remove(); };
    const before = census().nodes;

    await rerender(<Rows leads={LEADS.slice(1, 7)} />);

    const after = census().nodes;
    const added = [...after].filter((node) => !before.has(node)).map((node) => node.textContent);
    const removed = [...before].filter((node) => !after.has(node)).map((node) => node.textContent);
    expect(added).toEqual([LEADS[6].clip]);
    expect(removed).toEqual([LEADS[0].clip]);
  });
});
