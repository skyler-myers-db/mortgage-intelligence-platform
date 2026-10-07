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
 * aria-describedby to whichever of unit, hint, notice and error are present.
 * The notice is an ALWAYS-MOUNTED polite role=status region, so a message
 * written into it (a clamp, say) is announced; an error sets aria-invalid on
 * the control.
 *
 * deviation:field-affixes-readouts. A `prefix` / `suffix` ('$', '%') sits
 * beside the control in `.field__control`, aria-hidden: the `unit` ('US
 * dollars') is the control's sr-only description, so the accessible NAME stays
 * the visible label. FieldReadout shows a value that is not editable as text
 * (label plus `.field__value`), never as a read-only input. Every addition is
 * opt-in: a Field with none of these props renders exactly the markup it
 * always did (Lead Queue's range and saved-view fields rely on that).
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
  /** Shown before the control ('$'), hidden from assistive tech. */
  prefix?: string;
  /** Shown after the control ('%'), hidden from assistive tech. */
  suffix?: string;
  /** The value's unit in words ('US dollars'): the control's sr-only description. */
  unit?: string;
  /** Keep the label for assistive tech only (a control whose row already says what it is). */
  labelHidden?: boolean;
  children: (control: FieldControlProps) => ReactNode;
}

export function Field({
  label,
  hint,
  notice,
  error,
  className,
  prefix,
  suffix,
  unit,
  labelHidden,
  children,
}: FieldProps) {
  const id = useId();
  const controlId = `${id}control`;
  const unitId = `${id}unit`;
  const hintId = `${id}hint`;
  const noticeId = `${id}notice`;
  const errorId = `${id}error`;
  const describedBy = [unit ? unitId : null, hint ? hintId : null, notice ? noticeId : null, error ? errorId : null]
    .filter((part): part is string => part !== null)
    .join(' ');
  const control: FieldControlProps = {
    id: controlId,
    'aria-describedby': describedBy || undefined,
    'aria-invalid': error ? true : undefined,
  };
  const affixed = Boolean(prefix || suffix);
  return (
    <div className={className ? `field ${className}` : 'field'}>
      <label className={labelHidden ? 'field__label sr-only' : 'field__label'} htmlFor={controlId}>{label}</label>
      {affixed ? (
        <div className="field__control">
          {prefix ? <span className="field__affix" aria-hidden="true">{prefix}</span> : null}
          {children(control)}
          {suffix ? <span className="field__affix" aria-hidden="true">{suffix}</span> : null}
        </div>
      ) : (
        children(control)
      )}
      {unit ? <span className="sr-only" id={unitId}>{unit}</span> : null}
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
  /** What an empty value reads as (muted). */
  empty?: string;
  /** Keep the value's line breaks (a message body). */
  multiline?: boolean;
  className?: string;
}

/** A labelled value that is not editable here: text in `.field__value`, never a read-only input. */
export function FieldReadout({ label, value, empty = 'Not set.', multiline = false, className }: FieldReadoutProps) {
  const labelId = `${useId()}label`;
  const set = value.trim() !== '';
  const valueClass = [
    'field__value field__readout',
    multiline ? 'field__readout--multiline' : null,
    set ? null : 'field__readout--empty',
  ].filter(Boolean).join(' ');
  return (
    <div className={className ? `field ${className}` : 'field'} role="group" aria-labelledby={labelId}>
      <span className="field__label" id={labelId}>{label}</span>
      <div className={valueClass}>{set ? value : empty}</div>
    </div>
  );
}
