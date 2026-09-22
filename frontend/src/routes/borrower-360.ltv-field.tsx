import type { Borrower360 } from '../types';
import { Chip } from '../components/Primitives';
import { currency, ltvPct } from '../lib/formatters';

/**
 * The "LTV / Equity" value cell of the Borrower 360 dossier.
 *
 * `Borrower360.ltv` is `int | None` on the wire. The API withholds it (None,
 * with `ltv_basis_is_unreliable: true`) when gold has no trustworthy basis
 * for a property-level ratio: the valuation is implausibly low, or the lien
 * far exceeds it (blanket / portfolio attribution), and no usable modeled
 * CLTV exists. The route interpolated the field raw, so those borrowers read
 * "null% · $0" (2026-09-21 audit, quality-04).
 *
 * Three states, none of which fabricates a number:
 *  - no AVM            → unknown, "AVM was unavailable" (unchanged copy);
 *  - LTV withheld      → unknown + the backend's unreliable-basis caveat;
 *  - LTV reported      → "54% · $285,000".
 *
 * The equity estimate is withheld WITH the ratio. Gold floors it at 0
 * (`GREATEST(0, avm - lien)`, and 0 outright under the valuation floor), so
 * on every unreliable-basis row it is $0 — and "$0" beside a withheld LTV is
 * the same "free and clear / no equity" misreading the API withholds the
 * ratio to prevent. AVM and current lien stay on the dossier as reported.
 *
 * The caveat copy deliberately names no threshold: the plausibility floor and
 * the lien multiple live in `sql/transformations/gold_borrower_360.sql`, and
 * a restated number here could only drift from them.
 */

export const LTV_UNRELIABLE_CHIP_LABEL = 'LTV basis unreliable';
export const LTV_UNRELIABLE_TITLE =
  'Gold found no trustworthy basis for a property-level loan-to-value: the valuation is implausibly low or '
  + 'the lien far exceeds it (blanket or portfolio attribution), and no usable modeled CLTV exists.';

type LtvFieldBorrower = Pick<
  Borrower360,
  'avm_value' | 'ltv' | 'ltv_basis_is_unreliable' | 'equity_estimate'
>;

function UnknownValue({ label }: { label: string }) {
  return (
    <div className="field__value mono num">
      <span aria-hidden="true">—</span>
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function LtvEquityValue({ borrower }: { borrower: LtvFieldBorrower }) {
  if (!(borrower.avm_value > 0)) {
    return (
      <div>
        <UnknownValue label="LTV and equity unknown" />
        <div className="field__sub">Not a zero-equity signal; AVM was unavailable.</div>
      </div>
    );
  }

  const unreliable = borrower.ltv_basis_is_unreliable === true;
  const reported = typeof borrower.ltv === 'number' && Number.isFinite(borrower.ltv);
  if (unreliable || !reported) {
    return (
      <div>
        <UnknownValue label="LTV and equity unknown" />
        {unreliable && (
          <div className="field__sub">
            <Chip variant="warning" className="chip--compact" icon="info" title={LTV_UNRELIABLE_TITLE}>
              {LTV_UNRELIABLE_CHIP_LABEL}
            </Chip>
          </div>
        )}
        <div className="field__sub">
          {unreliable
            ? 'Withheld, not zero: this is not a free-and-clear or no-equity signal. AVM and current lien are shown as reported.'
            : 'LTV was not reported for this borrower; not a zero-equity signal.'}
        </div>
      </div>
    );
  }

  return (
    <div className="field__value mono num">
      {`${ltvPct(borrower.ltv)} · ${currency(borrower.equity_estimate)}`}
    </div>
  );
}
