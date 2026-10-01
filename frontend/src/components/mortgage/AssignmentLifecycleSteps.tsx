import type { AssignmentLifecycleStatus } from '../../types';
import { assignmentStatusLabel } from './LeadTable.logic';

/** The reviewed lifecycle, first stage to terminal. */
const LIFECYCLE: readonly AssignmentLifecycleStatus[] = [
  'assigned',
  'contact_drafted',
  'approved',
  'actioned',
  'outcome_recorded',
];

/**
 * The compact five-step lifecycle stepper (audit critic-06 item d;
 * deviation:assignment-lifecycle-stepper: the prototype's expanded row has
 * no sales-ops lifecycle, so this composes its `.chip` BEM). Done steps are
 * success chips, the current step carries aria-current="step", upcoming
 * steps are muted neutral chips. Display only: the stage chip beside it
 * stays the element tests and assistive tech read the stage from.
 *
 * Its own lazy chunk (the brief's first budget move): the expanded row's
 * workflow strip loads it, so the shared LeadTable chunk carries none of it.
 * A plain render helper, so the chunk carries no compiler memo cache either.
 */
export function lifecycleSteps(status: AssignmentLifecycleStatus) {
  const current = LIFECYCLE.indexOf(status);
  return (
    <ol className="chip-row lead-row-workflow__steps" aria-label="Assignment lifecycle" data-testid="assignment-lifecycle-steps">
      {LIFECYCLE.map((step, index) => (
        <li key={step} aria-current={index === current ? 'step' : undefined}>
          <span
            className={index < current
              ? 'chip chip--success'
              : index === current ? 'chip chip--neutral' : 'chip chip--neutral lead-row-workflow__step--upcoming'}
          >
            {assignmentStatusLabel(step)}
          </span>
        </li>
      ))}
    </ol>
  );
}
