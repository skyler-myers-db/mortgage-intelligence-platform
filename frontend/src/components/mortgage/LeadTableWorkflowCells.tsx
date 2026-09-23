/**
 * One-line workflow cells of the ranked-borrower table (audit visual-01,
 * tables-04, tables-05). The Default view merges Relationship, Assigned to,
 * Outreach and Last touch into ONE Status cell; the Sales ops view renders
 * them as four cells again. Either way:
 *
 *  - a cell shows one primary chip, the rest fold into a `+n` overflow whose
 *    evidence hover-card lists them (LeadTableOverflowChip);
 *  - a cell with nothing to say renders an em dash, never a default-value
 *    chip ("Other", "Unassigned", "None", "Untouched");
 *  - DNC, Suppressed and Owner-unresolved are compliance signals and always
 *    render in full, in-row, never inside `+n`.
 *
 * Timestamps, the assignment lifecycle-advance control and the Log button
 * moved to the expanded row (LeadRowWorkflowPanel).
 */
import type { DrawerSource } from '../AppContext';
import type { LeadSummary } from '../../types';
import { Chip } from '../Primitives';
import { LeadTableOverflowChip, type LeadOverflowItem, type LeadOverflowNoun } from './LeadTableOverflowChip';
import {
  leadAssignmentEntries,
  leadComplianceFlags,
  leadWorkflowStates,
  type LeadStatusEntry,
} from './LeadTable.status';

/** The em dash an empty workflow cell renders, with a spoken equivalent. */
export function LeadEmptyCell({ spoken }: { spoken: string }) {
  return (
    <span className="lead-table__empty">
      <span aria-hidden="true">—</span>
      <span className="sr-only">{spoken}</span>
    </span>
  );
}

export function LeadComplianceChips({ lead }: { lead: LeadSummary }) {
  const flags = leadComplianceFlags(lead);
  if (flags.length === 0) return null;
  return (
    <span className="lead-table__flags" data-testid={`lead-compliance-${lead.borrower_id}`}>
      {flags.map((flag) => (
        <Chip
          key={flag.key}
          variant={flag.variant}
          icon={flag.icon}
          title={flag.title}
          className="chip--compact lead-table__flag"
        >
          {flag.label}
        </Chip>
      ))}
    </span>
  );
}

/** The chip shows the label; a qualifier (the lifecycle stage) is spoken with it. */
function StatusChip({ entry }: { entry: LeadStatusEntry }) {
  return (
    <>
      <Chip variant={entry.variant} title={entry.detail} className="chip--compact">
        {entry.label}
      </Chip>
      {entry.qualifier && <span className="sr-only">, {entry.qualifier}</span>}
    </>
  );
}

function overflowItems(entries: readonly LeadStatusEntry[]): LeadOverflowItem[] {
  return entries.map((entry) => ({
    field: entry.field,
    value: entry.qualifier ? `${entry.label} (${entry.qualifier})` : entry.label,
    source: entry.source,
  }));
}

/** The hover card names the one table the hidden values come from, or none when they mix. */
function overflowSource(title: string, entries: readonly LeadStatusEntry[]): Pick<DrawerSource, 'title' | 'assetPath'> {
  const assets = [...new Set(entries.map((entry) => entry.source))];
  return assets.length === 1 ? { title, assetPath: assets[0] } : { title };
}

/** One primary chip plus `+n` for the remaining entries, on one line. */
function PrimaryWithOverflow({
  entries,
  noun,
  title,
  onExpand,
}: {
  entries: readonly LeadStatusEntry[];
  noun: LeadOverflowNoun;
  /** Hover-card title for the `+n` ("Workflow status"). */
  title: string;
  onExpand: () => void;
}) {
  const [primary, ...rest] = entries;
  if (!primary) return null;
  return (
    <span className="lead-table__line">
      <StatusChip entry={primary} />
      <LeadTableOverflowChip
        items={overflowItems(rest)}
        noun={noun}
        source={overflowSource(title, rest)}
        onActivate={onExpand}
      />
    </span>
  );
}

/** Default view: the merged Status cell. */
export function LeadStatusCell({ lead, onExpand }: { lead: LeadSummary; onExpand: () => void }) {
  const states = leadWorkflowStates(lead);
  const hasFlags = leadComplianceFlags(lead).length > 0;
  return (
    <td className="lead-table__status-cell" data-testid={`lead-status-${lead.borrower_id}`}>
      <div className="lead-table__status">
        <LeadComplianceChips lead={lead} />
        <PrimaryWithOverflow entries={states} noun={['status', 'statuses']} title="Workflow status" onExpand={onExpand} />
        {states.length === 0 && !hasFlags && <LeadEmptyCell spoken="No workflow activity" />}
      </div>
    </td>
  );
}

function entriesFor(lead: LeadSummary, keys: ReadonlyArray<LeadStatusEntry['key']>): LeadStatusEntry[] {
  return leadWorkflowStates(lead).filter((entry) => keys.includes(entry.key));
}

/** Sales ops view: Relationship (plus the compliance flags and owner caveat). */
export function LeadRelationshipCell({ lead, onExpand }: { lead: LeadSummary; onExpand: () => void }) {
  const entries = entriesFor(lead, ['relationship', 'multi_owner']);
  const hasFlags = leadComplianceFlags(lead).length > 0;
  return (
    <td>
      <div className="lead-table__status">
        <LeadComplianceChips lead={lead} />
        <PrimaryWithOverflow entries={entries} noun={['owner detail', 'owner details']} title="Relationship" onExpand={onExpand} />
        {entries.length === 0 && !hasFlags && <LeadEmptyCell spoken="No first-party relationship" />}
      </div>
    </td>
  );
}

/** Sales ops view: Assigned to, with the lifecycle stage as its `+n`. */
export function LeadAssignmentCell({ lead, onExpand }: { lead: LeadSummary; onExpand: () => void }) {
  const entries = leadAssignmentEntries(lead);
  return (
    <td data-testid={`lead-assignment-${lead.borrower_id}`}>
      {entries.length > 0
        ? <PrimaryWithOverflow entries={entries} noun={['assignment detail', 'assignment details']} title="Assignment" onExpand={onExpand} />
        : <LeadEmptyCell spoken="Unassigned" />}
    </td>
  );
}

/** Sales ops view: Outreach (+ aging) or Last touch. */
export function LeadWorkflowFieldCell({
  lead,
  keys,
  noun,
  title,
  empty,
  onExpand,
}: {
  lead: LeadSummary;
  keys: ReadonlyArray<LeadStatusEntry['key']>;
  noun: LeadOverflowNoun;
  title: string;
  empty: string;
  onExpand: () => void;
}) {
  const entries = entriesFor(lead, keys);
  return (
    <td>
      {entries.length > 0
        ? <PrimaryWithOverflow entries={entries} noun={noun} title={title} onExpand={onExpand} />
        : <LeadEmptyCell spoken={empty} />}
    </td>
  );
}
