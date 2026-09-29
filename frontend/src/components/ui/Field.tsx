/**
 * Field: the form-field primitive (2026-09-21 audit critic-04), landed on the
 * campaign-setup numeric controls first; the rollout to the other native
 * controls is a later wave.
 *
 * DECLARED APP EXTENSION: the prototype (design_files/index.html) has no field
 * system, only the `.field__label` / `.field__value` pair this reuses. Tokens
 * only; Field.css rides the lazy chunk of whichever route imports this.
 *
 * Field binds its label to the control (useId) and wires the control's
 * aria-describedby to whichever of hint, notice and error are present. The
 * notice is an ALWAYS-MOUNTED polite role=status region, so a message written
 * into it (a clamp, say) is announced; an error sets aria-invalid on the
 * control.
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
  /** A polite status message (for example a clamp). Cleared with null. */
  notice?: string | null;
  /** A validation error: sets aria-invalid on the control. */
  error?: string | null;
  className?: string;
  children: (control: FieldControlProps) => ReactNode;
}

export function Field({ label, hint, notice, error, className, children }: FieldProps) {
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
      {children(control)}
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
