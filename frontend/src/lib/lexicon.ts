/**
 * The product lexicon (2026-09-21 audit critic-11, D-platform-process-g).
 *
 * One economic-incentive concept used to carry six names ("Refi economics
 * screen", "Prime Refi Candidates", "refi screen", "In-the-money", ...). Its
 * one name is "In the Money", the prototype's and CLAUDE.md's vocabulary, in
 * three grammatical forms: the label ("In the Money"), the prose predicate
 * ("are in the money") and the prenominal ("in-the-money borrowers"). "ITM"
 * appears only inside "ITM on Related Property" or an <abbr>.
 *
 * This is the ONLY file exempt from eslint.config.js LEXICON_BAN: every
 * retired variant below is an INPUT alias (the glossary's search-only
 * `searchAliases`, palette keywords, a legacy segment name), never display
 * text; /glossary shows only "ITM" and "in-the-money" under the term
 * (lexicon-copy.fixture.spec.ts scans it). Codes, columns and ids never change: `itm`,
 * `in_the_money`, `refi_economics_screen` and the glossary id `in-the-money`
 * all stay. backend/services/segment_labels.py is the server twin.
 */
import { formatCount } from './formatters';

export const LEXICON = {
  inTheMoney: {
    label: 'In the Money',
    prose: 'in the money',
    prenominal: 'in-the-money',
    short: 'ITM',
    promptPhrase: 'in-the-money',
    glossaryId: 'in-the-money',
    ruleLabel: 'In the Money rule',
    definition: (spreadBps: number, equityPct: number): string =>
      `Lien rate ≥ ${formatCount(spreadBps)} bps above par and equity ≥ ${formatCount(equityPct)}%`,
    legacyAliases: [
      'ITM',
      'in-the-money',
      'refi economics',
      'refi economics screen',
      'refinance economics screen',
      'prime refi candidates',
    ],
    legacyNames: ['Prime Refi Candidates'],
  },
} as const;
