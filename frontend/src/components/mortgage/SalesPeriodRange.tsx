import { useEffect, useRef } from 'react';
import { Field } from '../ui/Field';

/**
 * The Sales ops custom range (2026-09-21 audit critic-10, item 6a): two
 * Field-wrapped native date inputs. Lazy (React.lazy from
 * routes/analytics.sales-ops.tsx) so Field and its stylesheet load only when a
 * manager picks Custom, never with the analytics route.
 *
 * A date is applied on the input's own `change` event (a picked or fully
 * typed date) or on Enter, never per keystroke, so a half-typed date never
 * reaches the URL or the reads. deviation:sales-manager-period.
 */
function PeriodDate({ label, value, max, onApply }: { label: string; value: string; max: string; onApply: (value: string) => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    const input = inputRef.current;
    if (!input) return undefined;
    const apply = () => {
      if (input.value) onApply(input.value);
    };
    input.addEventListener('change', apply);
    return () => input.removeEventListener('change', apply);
  });
  return (
    <Field label={label} className="sales-ops-period__date">
      {(control) => (
        <input
          {...control}
          ref={inputRef}
          type="date"
          className="form-input"
          defaultValue={value}
          key={value}
          max={max}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && event.currentTarget.value) onApply(event.currentTarget.value);
          }}
        />
      )}
    </Field>
  );
}

export interface SalesPeriodRangeProps {
  from: string;
  to: string;
  /** Today, the latest day either input accepts. */
  max: string;
  onApply: (range: { from: string; to: string }) => void;
}

export default function SalesPeriodRange({ from, to, max, onApply }: SalesPeriodRangeProps) {
  return (
    <div className="sales-ops-period__range">
      <PeriodDate label="From" value={from} max={max} onApply={(value) => onApply({ from: value, to })} />
      <PeriodDate label="To" value={to} max={max} onApply={(value) => onApply({ from, to: value })} />
    </div>
  );
}
