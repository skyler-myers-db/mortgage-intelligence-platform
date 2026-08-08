/**
 * Plain-English rendering for the RAW SOURCE CODES the Borrower 360 dossier
 * shows. These are Cotality/Census identifiers, not product vocabulary: the
 * dossier's "Metro / loan type" field rendered them straight through, so the
 * screen read "42660 · CNV" — two opaque tokens where a loan officer expects
 * a place and a loan program.
 *
 * Rule: expand what is known, pass through what is not. An unrecognized code
 * is real signal and must stay visible verbatim; it must never be guessed
 * into a bucket. Same fail-closed posture as `mip.gold.fn_loan_product_type`,
 * which returns NULL rather than a guess for an unknown code.
 */

/**
 * Cotality `first_position_mortgage_loan_type_code`
 * (`silver.lien_current.first_pos_loan_type`). Only codes whose meaning is
 * unambiguous belong here.
 *
 * NOTE: live gold data carries `CNV` for conventional, while
 * `sql/uc_functions/fn_loan_product_type.sql` and the fit sub-score branch in
 * `gold_borrower_360.sql` test for `CONV`. Both spellings map to Conventional
 * here so the dossier reads correctly either way — but the SQL-side mismatch
 * is a separate, real defect, not something this map fixes.
 */
const LOAN_TYPE_LABELS: Record<string, string> = {
  CNV: 'Conventional',
  CONV: 'Conventional',
  CONVENTIONAL: 'Conventional',
  FHA: 'FHA',
  VA: 'VA',
  USDA: 'USDA',
  USD: 'USDA',
  FMHA: 'USDA (FmHA)',
};

/** "CNV" -> "Conventional"; unknown codes pass through unchanged. */
export function loanTypeLabel(code: string | null | undefined): string | null {
  const raw = (code ?? '').trim();
  if (!raw) return null;
  return LOAN_TYPE_LABELS[raw.toUpperCase()] ?? raw;
}

/**
 * "42660" -> "CBSA 42660". The gold layer carries the CBSA CODE only
 * (`borrower_360.situs_cbsa_code`) — there is no metro NAME column anywhere
 * in the dossier payload, so labelling the code is as far as this can
 * honestly go. If a metro-name reference lands in gold, prefer it here.
 */
export function metroLabel(cbsaCode: string | null | undefined): string | null {
  const raw = (cbsaCode ?? '').trim();
  if (!raw) return null;
  return /^cbsa\b/i.test(raw) ? raw : `CBSA ${raw}`;
}

/**
 * The dossier's "Metro / loan type" line, with an honest gap marker for
 * whichever half is missing.
 */
export function metroLoanTypeLabel(
  cbsaCode: string | null | undefined,
  loanTypeCode: string | null | undefined,
): string {
  const metro = metroLabel(cbsaCode) ?? 'CBSA unavailable';
  const loanType = loanTypeLabel(loanTypeCode) ?? 'Loan type unavailable';
  return `${metro} · ${loanType}`;
}
