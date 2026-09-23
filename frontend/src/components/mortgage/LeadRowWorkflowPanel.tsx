import type { ReactNode } from 'react';
import type { LeadSummary } from '../../types';
import { Button, Chip } from '../Primitives';
import { AssignmentLifecycleAdvance } from './AssignmentLifecycleAdvance';
import {
  assignmentStatusLabel,
  assignmentStatusVariant,
  formatDateTimeShort,
} from './LeadTable.logic';
import { leadComplianceFlags, leadWorkflowStates, type LeadStatusEntry } from './LeadTable.status';

/**
 * Workflow strip of the expanded lead row (audit tables-04). The one-line
 * table rows keep only a primary status chip, so the detail they used to
 * stack moved here: every workflow state in full, the assignment lifecycle
 * stage with its advance control, the assignment and last-touch timestamps,
 * the suppression reason, and the Log call-disposition button.
 *
 * `.lead-row-workflow` is an app extension: the prototype's expanded row
 * (design_files/Module 0 Prototype.html:1888-1989, BorrowerDetail) has no
 * sales-ops workflow, which the app added for loan officers. It composes the
 * prototype's `.field__label`, `.chip` and `.btn--ghost` only.
 */
export function LeadRowWorkflowPanel({
  lead,
  salesBusy,
  salesTeamCount,
  onOpenDisposition,
  onAssignmentUpdate,
}: {
  lead: LeadSummary;
  salesBusy: boolean;
  salesTeamCount: number;
  onOpenDisposition: (borrowerId: string) => void;
  onAssignmentUpdate: (borrowerId: string, update: Partial<LeadSummary>) => void;
}) {
  const states = leadWorkflowStates(lead);
  const byKey = (key: LeadStatusEntry['key']) => states.find((entry) => entry.key === key);
  const flags = leadComplianceFlags(lead);
  const relationship = byKey('relationship');
  const multiOwner = byKey('multi_owner');
  const outreach = byKey('outreach');
  const aging = byKey('aging');
  const lastTouch = byKey('last_touch');
  const stage = lead.assigned_to_email ? assignmentStatusLabel(lead.assignment_status) : '';

  return (
    <div className="tbl__expand-inner lead-row-workflow" data-testid={`lead-workflow-${lead.borrower_id}`}>
      <div className="eyebrow">Workflow</div>
      <div className="lead-row-workflow__fields">
        <Field label="Relationship">
          {relationship ? <EntryChip entry={relationship} /> : <None />}
          {multiOwner && <EntryChip entry={multiOwner} />}
        </Field>
        <Field label="Contactability">
          {flags.length === 0 && <span className="muted fs-12">Eligible</span>}
          {flags.map((flag) => (
            <Chip key={flag.key} variant={flag.variant} icon={flag.icon} title={flag.title}>
              {flag.key === 'suppressed' && lead.suppression_reason
                ? `${flag.label}: ${lead.suppression_reason}`
                : flag.label}
            </Chip>
          ))}
        </Field>
        <Field label="Assigned to">
          {lead.assigned_to_email ? (
            <Chip variant="success">{lead.assigned_to_label ?? lead.assigned_to_email}</Chip>
          ) : <None />}
          {stage && (
            <Chip variant={assignmentStatusVariant(lead.assignment_status)} title="Assignment lifecycle stage">
              {stage}
            </Chip>
          )}
          {lead.assigned_to_email && lead.assignment_status && lead.assignment_id && (
            <AssignmentLifecycleAdvance
              assignmentId={lead.assignment_id}
              status={lead.assignment_status}
              borrowerId={lead.borrower_id}
              onAdvanced={onAssignmentUpdate}
            />
          )}
          {lead.assigned_at && (
            <span className="muted mono fs-11">{formatDateTimeShort(lead.assigned_at)}</span>
          )}
        </Field>
        <Field label="Outreach">
          {outreach ? <EntryChip entry={outreach} /> : <None />}
          {aging && <EntryChip entry={aging} />}
        </Field>
        <Field label="Last touch">
          {lastTouch ? <EntryChip entry={lastTouch} /> : <None />}
          {lead.latest_disposition_at && (
            <span className="muted mono fs-11">{formatDateTimeShort(lead.latest_disposition_at)}</span>
          )}
          <Button
            variant="ghost"
            size="sm"
            icon="bolt"
            onClick={(event) => {
              event.stopPropagation();
              onOpenDisposition(lead.borrower_id);
            }}
            disabled={salesBusy || salesTeamCount === 0}
            aria-label={`Log call disposition for ${lead.borrower_id}`}
          >
            Log
          </Button>
        </Field>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="lead-row-workflow__field">
      <div className="field__label">{label}</div>
      <div className="chip-row">{children}</div>
    </div>
  );
}

function EntryChip({ entry }: { entry: LeadStatusEntry }) {
  return <Chip variant={entry.variant} title={entry.detail}>{entry.label}</Chip>;
}

function None() {
  return <span className="muted fs-12">None</span>;
}
