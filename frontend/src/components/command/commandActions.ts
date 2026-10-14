import type { IconName } from '../Icon';
import { formatCount } from '../../lib/formatters';
import { NAVIGATION_ROUTE_IDS, ROUTES, type NavigationRouteId } from '../../lib/routeMeta';
import type { CommandSelectionContext, CommandVerb } from './commandSelection';

/**
 * Command palette action registry + pure filter/rank (re-audit #4 follow-up,
 * Buyer-Wow #1). Kept side-effect-free and decoupled from React so the
 * ranking is unit-pinnable: each action declares a `target` (a route or a
 * named workspace command) and the palette component resolves it to a
 * handler. No borrower data lives here — borrower/geography matches come
 * from the live /api/borrowers/search and are merged in the component.
 */

export type CommandTarget =
  | { kind: 'route'; to: string }
  | { kind: 'command'; command: 'toggle-theme' | 'toggle-console' | 'open-genie' | 'open-shortcuts' }
  // A verb on the page's current selection (audit wow-power-4). The palette
  // resolves it through the selection context the page published, which
  // calls the page's own guarded handler (never a parallel approval path).
  | { kind: 'verb'; verb: CommandVerb };

export interface CommandAction {
  id: string;
  label: string;
  /** Right-aligned secondary text (route path or verb). */
  hint: string;
  icon: IconName;
  group: 'Navigate' | 'Workspace' | 'Selection';
  /** Extra search terms beyond the label. */
  keywords: string[];
  target: CommandTarget;
  /**
   * The keymap binding that does the same thing (shell-07 item 3). The row
   * shows the chord's keycaps and the option's aria-keyshortcuts only while
   * that binding is registered and the chord is live (a single-key chord is
   * off with the Console's single-key switch).
   */
  shortcut?: CommandShortcut;
}

export interface CommandShortcut {
  /** A lib/keymap binding id (`registerKeyBinding({ id })`). */
  bindingId: string;
  /** One of that binding's chords, in lib/keymap notation ('Shift+A', '?'). */
  chord: string;
}

/**
 * Command id and extra search terms per palette route. Label, hint, icon and
 * destination come from the route registry (lib/routeMeta, audit shell-08),
 * so the palette and the nav chips can no longer disagree about a route.
 *
 * Mortgage synonyms (audit 2026-09-21 `shell-07`): "refi" used to match
 * nothing. The domain words a lender types are keywords BEFORE any fuzzy
 * scoring, so they resolve to the page that owns the concept.
 */
const ROUTE_ACTION_TERMS = {
  home: { id: 'nav-home', keywords: ['overview', 'dashboard', 'start', 'map', 'geography'] },
  portfolio: {
    id: 'nav-portfolio',
    keywords: ['build', 'population', 'filters', 'campaign', 'roi', 'economics', 'in the money', 'heloc'],
  },
  segments: {
    id: 'nav-segments',
    keywords: ['segments', 'in the money', 'itm', 'investor', 'heloc', 'equity', 'cohort', 'refi', 'refinance', 'cash-out', 'cash out', 'recapture', 'retention', 'listed', 'listed for sale'],
  },
  leads: {
    id: 'nav-leads',
    keywords: ['ranked', 'borrowers', 'approve', 'reject', 'queue', 'outreach', 'refi', 'in the money', 'heloc'],
  },
  borrowerIndex: { id: 'nav-borrower', keywords: ['dossier', 'profile', 'evidence', 'proof'] },
  offerIndex: {
    id: 'nav-offer',
    keywords: ['offer', 'next best', 'nbo', 'draft', 'outreach', 'refi', 'refinance', 'cash-out', 'cash out', 'heloc', 'recapture', 'retention'],
  },
  analytics: { id: 'nav-analytics', keywords: ['executive', 'geography', 'economics', 'signals', 'funnel', 'charts'] },
  askGenie: { id: 'nav-genie', keywords: ['ai', 'question', 'chat', 'natural language', 'sql'] },
  glossary: { id: 'nav-glossary', keywords: ['terms', 'definitions', 'clip', 'owner link', 'help'] },
  auditLedger: {
    id: 'nav-audit-ledger',
    keywords: ['audit', 'ledger', 'audit log', 'compliance', 'receipts', 'history'],
  },
  admin: { id: 'nav-admin', keywords: ['config', 'offer rules', 'governance'] },
} as const satisfies Record<NavigationRouteId, { id: string; keywords: readonly string[] }>;

function routeAction(routeId: NavigationRouteId): CommandAction {
  const route = ROUTES[routeId];
  const terms = ROUTE_ACTION_TERMS[routeId];
  return {
    id: terms.id,
    label: route.name,
    hint: route.pattern,
    icon: route.icon,
    group: 'Navigate',
    keywords: [...terms.keywords],
    target: { kind: 'route', to: route.pattern },
  };
}

export const COMMAND_ACTIONS: readonly CommandAction[] = [
  // --- Navigate (the eight product-flow routes + analytics/glossary/admin) ---
  ...NAVIGATION_ROUTE_IDS.map(routeAction),
  // --- Workspace commands ---
  { id: 'cmd-genie', label: 'Open Genie panel', hint: 'Floating assistant', icon: 'sparkle', group: 'Workspace',
    keywords: ['ai', 'assistant', 'chat'], target: { kind: 'command', command: 'open-genie' } },
  { id: 'cmd-theme', label: 'Toggle theme', hint: 'Light / dark', icon: 'moon', group: 'Workspace',
    keywords: ['dark', 'light', 'appearance'], target: { kind: 'command', command: 'toggle-theme' } },
  { id: 'cmd-console', label: 'Toggle Console', hint: 'Theme · accent · density', icon: 'tweak', group: 'Workspace',
    keywords: ['settings', 'density', 'accent', 'tenant'], target: { kind: 'command', command: 'toggle-console' } },
  // The `?` sheet (shell-07 item 3): the palette closes, then the sheet opens.
  { id: 'cmd-shortcuts', label: 'Keyboard shortcuts', hint: 'What this page binds', icon: 'tweak', group: 'Workspace',
    keywords: ['keyboard', 'shortcuts', 'hotkeys', 'keys', 'help'], target: { kind: 'command', command: 'open-shortcuts' },
    shortcut: { bindingId: 'shortcut-sheet', chord: '?' } },
];

/** The Lead Queue's one bulk binding carries Shift+A and Shift+R (LeadTable.keymap). */
const LEAD_TABLE_BULK_BINDING = 'lead-table-bulk-approve';

/**
 * Verbs on the published selection. "Approve N selected…" opens the page's
 * bulk rationale gate (or the single-row review for one row); it is hidden
 * whenever the approver gate, a campaign binding or an in-flight run would
 * refuse it. The ellipsis says a review step follows: no verb submits.
 */
export function commandVerbActions(selection: CommandSelectionContext | null): CommandAction[] {
  if (!selection || selection.selectedCount === 0) return [];
  const verbs: CommandAction[] = [];
  if (selection.canApprove && selection.approveCount > 0) {
    verbs.push({
      id: 'verb-approve-selected',
      label: `Approve ${formatCount(selection.approveCount)} selected…`,
      hint: selection.approveCount === 1 ? 'Opens the approval review' : 'Opens the approval rationale gate',
      icon: 'check',
      group: 'Selection',
      keywords: ['approve', 'bulk', 'selected', 'selection', 'rationale'],
      target: { kind: 'verb', verb: 'approve-selected' },
      shortcut: { bindingId: LEAD_TABLE_BULK_BINDING, chord: 'Shift+A' },
    });
  }
  if (selection.canReject && selection.rejectCount > 0) {
    verbs.push({
      id: 'verb-reject-selected',
      label: `Reject ${formatCount(selection.rejectCount)} selected…`,
      hint: selection.rejectCount === 1 ? 'Opens the reject panel' : 'Opens the rejection reason gate',
      icon: 'cross',
      group: 'Selection',
      keywords: ['reject', 'bulk', 'selected', 'selection', 'reason'],
      target: { kind: 'verb', verb: 'reject-selected' },
      shortcut: { bindingId: LEAD_TABLE_BULK_BINDING, chord: 'Shift+R' },
    });
  }
  if (selection.canAssign) {
    verbs.push({
      id: 'verb-assign-selected',
      label: `Assign ${formatCount(selection.selectedCount)} selected…`,
      hint: 'Choose a loan officer',
      icon: 'user',
      group: 'Selection',
      keywords: ['assign', 'distribute', 'loan officer', 'lo', 'selected', 'selection'],
      target: { kind: 'verb', verb: 'assign-selected' },
    });
  }
  return verbs;
}

/**
 * The actions this session may take: Admin for administrators, the audit
 * ledger for administrators and auditors (D-audit-reads-c3). `canReadAudit`
 * defaults to `canAccessAdmin`, so a caller that passes only the admin flag
 * keeps administrators right; pass the ledger flag to offer it to auditors.
 */
export function commandActionsForAccess(
  canAccessAdmin: boolean,
  actions: readonly CommandAction[] = COMMAND_ACTIONS,
  canReadAudit: boolean = canAccessAdmin,
): CommandAction[] {
  return actions.filter((action) => {
    if (action.id === 'nav-admin') return canAccessAdmin;
    if (action.id === 'nav-audit-ledger') return canReadAudit;
    return true;
  });
}

function normalize(text: string): string {
  return text.toLowerCase().trim();
}

/** Letters only, at least three: a masked id ('B-…') or a ZIP never qualifies. */
const FUZZY_QUERY_RE = /^[a-z]{3,}$/;
const MASKED_ID_LABEL_RE = /^b-[0-9a-z]{13}$/;

/** Can `query` be spelled by prefixes of consecutive `words`, each at least one letter, starting at `from`? */
function prefixRun(query: string, words: readonly string[], from: number): boolean {
  if (query === '') return true;
  const word = words[from];
  if (word === undefined) return false;
  for (let take = Math.min(query.length, word.length); take > 0; take -= 1) {
    if (word.startsWith(query.slice(0, take)) && prefixRun(query.slice(take), words, from + 1)) return true;
  }
  return false;
}

/**
 * The fuzzy step after every exact, prefix, substring, hint and keyword
 * check (shell-07 item 5): the query runs through prefixes of the label's
 * words in order, so 'leaqu' finds Lead Queue. 20 when the run starts at the
 * first word, 10 otherwise: below every keyword hit (25 and up), so the
 * mortgage synonyms still resolve first. Masked ids and ZIPs never fuzzy-match.
 */
export function fuzzyWordPrefixScore(label: string, query: string): number {
  const q = normalize(query).replace(/\s+/g, '');
  const text = normalize(label);
  if (!FUZZY_QUERY_RE.test(q) || MASKED_ID_LABEL_RE.test(text)) return 0;
  const words = text.split(/[^a-z0-9]+/).filter((word) => word.length > 0);
  for (let start = 0; start < words.length; start += 1) {
    if (prefixRun(q, words, start)) return start === 0 ? 20 : 10;
  }
  return 0;
}

/**
 * Rank a row (label, hint, keywords) against a query. Higher is better; 0
 * means no match. Label matches outrank hint and keyword matches; a prefix
 * outranks a mid-string hit so "lead" surfaces Lead Queue first; the fuzzy
 * word-prefix step comes last. The palette's other groups rank with it too.
 */
export function scoreText(
  labelText: string,
  hintText: string,
  keywords: readonly string[],
  query: string,
): number {
  const q = normalize(query);
  if (!q) return 1; // no query → everything shows in registry order
  const label = normalize(labelText);
  if (label === q) return 100;
  if (label.startsWith(q)) return 80;
  if (label.includes(q)) return 60;
  const hint = normalize(hintText);
  if (hint.includes(q)) return 45;
  for (const kw of keywords) {
    const k = normalize(kw);
    if (k === q || k.startsWith(q)) return 40;
    if (k.includes(q)) return 25;
  }
  return fuzzyWordPrefixScore(label, q);
}

/** Rank one action against a query (see scoreText). */
export function scoreAction(action: CommandAction, query: string): number {
  return scoreText(action.label, action.hint, action.keywords, query);
}

export function filterCommandActions(
  query: string,
  actions: readonly CommandAction[] = COMMAND_ACTIONS,
): CommandAction[] {
  const q = normalize(query);
  if (!q) return [...actions];
  return actions
    .map((action, index) => ({ action, index, score: scoreAction(action, q) }))
    .filter((entry) => entry.score > 0)
    // Stable: by score desc, then original registry order.
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map((entry) => entry.action);
}
