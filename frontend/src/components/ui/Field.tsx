/**
 * Field and FieldReadout: the form-field primitive (2026-09-21 audit
 * critic-04), landed in campaign setup first; the rollout to the other native
 * controls is a later wave.
 *
 * DECLARED APP EXTENSION: the prototype (design_files/index.html) has no field
 * system, only the `.field__label` / `.field__value` pair this reuses. Tokens
 * only; Field.css rides the lazy chunk of whichever route imports this.
 *
 * Field binds its label to the control (useId), lays optional prefix / suffix
 * adornments around it, and wires the control's aria-describedby to whichever
 * of hint, notice and error are present. The notice is an ALWAYS-MOUNTED
 * polite role=status region, so a message written into it (a clamp, say) is
 * announced; an error sets aria-invalid on the control.
 *
 * FieldReadout is read-only text under a label (pre-wrap): copy the operator
 * cannot edit is not dressed up as an input.
 */
import { useId, type ReactNode } from 'react';
import './Field.css';

/** What a Field hands its control: spread it onto the input. */
export interface FieldControlProps {
  id: string;
  'aria-describedby': string | undefined;
  'aria-invalid': true | undefined;
}

export interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  /** A unit before the control (for example '$'). Visual; the label names the field. */
  prefix?: ReactNode;
  /** A unit after the control (for example '%'). */
  suffix?: ReactNode;
  /** A polite status message (for example a clamp). Cleared with null. */
  notice?: string | null;
  /** A validation error: sets aria-invalid on the control. */
  error?: string | null;
  className?: string;
  children: (control: FieldControlProps) => ReactNode;
}

export function Field({ label, hint, prefix, suffix, notice, error, className, children }: FieldProps) {
  const id = useId();
  const controlId = `${id}control`;
  const hintId = `${id}hint`;
  const noticeId = `${id}notice`;
  const errorId = `${id}error`;
  const describedBy = [hint ? hintId : null, notice ? noticeId : null, error ? errorId : null]
    .filter((part): part is string => part !== null)
    .join(' ');
  const control: FieldControlProps = {
    id: controlId,
    'aria-describedby': describedBy || undefined,
    'aria-invalid': error ? true : undefined,
  };
  return (
    <div className={className ? `field ${className}` : 'field'}>
      <label className="field__label" htmlFor={controlId}>{label}</label>
      <div className="field__control">
        {prefix ? <span className="field__adornment" aria-hidden="true">{prefix}</span> : null}
        {children(control)}
        {suffix ? <span className="field__adornment" aria-hidden="true">{suffix}</span> : null}
      </div>
      {hint ? <p className="field__hint" id={hintId}>{hint}</p> : null}
      {/* Always mounted: a live region must exist before its text changes.
          Empty, it is visually hidden so it takes no room in the field. */}
      <p className={notice ? 'field__notice' : 'field__notice sr-only'} id={noticeId} role="status" aria-live="polite">
        {notice ?? ''}
      </p>
      {error ? <p className="field__error" id={errorId}>{error}</p> : null}
    </div>
  );
}

export interface FieldReadoutProps {
  label: ReactNode;
  value: string;
  className?: string;
}

/** A label and its read-only value as a name/value pair; an empty value reads as a muted dash. */
export function FieldReadout({ label, value, className }: FieldReadoutProps) {
  const id = useId();
  const labelId = `${id}label`;
  const empty = value.trim().length === 0;
  return (
    <dl className={className ? `field field--readout ${className}` : 'field field--readout'}>
      <dt className="field__label" id={labelId}>{label}</dt>
      <dd className="field__value field__readout" aria-labelledby={labelId}>
        {empty ? (
          <>
            <span className="field__readout-empty" aria-hidden="true">—</span>
            <span className="sr-only">Not set</span>
          </>
        ) : value}
      </dd>
    </dl>
  );
}
