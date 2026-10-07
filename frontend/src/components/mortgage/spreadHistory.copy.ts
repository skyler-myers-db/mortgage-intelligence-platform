/**
 * Copy for "Crossed the line" (audit wow-stage-4): the borrower's fixed note
 * rate against the weekly 30-year series in the proof drawer's Math tab.
 *
 * Every caption is history: the crossing week is today's rule and today's
 * equity applied to past weekly rates (gold.borrower_dossier.first_itm_week),
 * never a rate forecast.
 */
export const SPREAD_HISTORY_COPY = {
  title: 'Crossed the line',
  sinceWeek: (week: string) => `In the money since the week of ${week}`,
  sinceAtLeast: (week: string) => `In the money since at least the week of ${week}, when the rate series starts`,
  notInTheMoney: 'Not in the money at the latest 30-year rate',
  historyNote: "Today's rule and today's equity applied to past rates. History, not a forecast.",
  needsDossier: 'Open the borrower dossier to see the rate history.',
  notAvailable: 'Rate history is not available for this borrower.',
  fixedOnly: 'Rate history is drawn for fixed-rate first liens only.',
  noNoteRate: 'No active fixed note rate to compare.',
  rateAtBound: 'The recorded note rate sits at a source bound (1% or 15%), so its true rate is unknown.',
  noWeeks: 'No weekly 30-year rates in the window yet.',
  loading: 'Loading the rate history',
  unavailableTitle: 'Rate history unavailable',
  unavailableBody: 'The weekly 30-year series did not load. Try again once the warehouse is healthy.',
  retry: 'Try again',
  showTable: 'Show as table',
  hideTable: 'Hide table',
  legendNote: 'Note rate',
  legendMarket: '30-year fixed (FRED)',
  legendScreen: 'Spread screen',
  legendSpread: 'Note rate above the market',
  legendCrossing: 'Crossing week',
  tableLabel: 'Weekly 30-year rate against the note rate',
  tableWeek: 'Week',
  tableRate: '30-year rate',
  crossingChip: 'Crossed',
  crossingText: 'in the money from here',
} as const;
