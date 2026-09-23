import type { IconName } from '../Icon';
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
  | { kind: 'command'; command: 'toggle-theme' | 'toggle-console' | 'open-genie' }
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
}

export const COMMAND_ACTIONS: readonly CommandAction[] = [
  // --- Navigate (the eight product-flow routes + analytics/glossary/admin) ---
  { id: 'nav-home', label: 'Home', hint: '/', icon: 'home', group: 'Navigate',
    keywords: ['overview', 'dashboard', 'start', 'map', 'geography'], target: { kind: 'route', to: '/' } },
  // Mortgage synonyms (audit 2026-09-21 `shell-07`): "refi" used to match
  // nothing. The domain words a lender types are keywords BEFORE any fuzzy
  // scoring, so they resolve to the page that owns the concept.
  { id: 'nav-portfolio', label: 'Portfolio Builder', hint: '/portfolio-builder', icon: 'target', group: 'Navigate',
    keywords: ['build', 'population', 'filters', 'campaign', 'roi', 'economics', 'in the money', 'heloc'], target: { kind: 'route', to: '/portfolio-builder' } },
  { id: 'nav-segments', label: 'Segment Intelligence', hint: '/segment-intelligence', icon: 'layers', group: 'Navigate',
    keywords: ['segments', 'in the money', 'itm', 'investor', 'heloc', 'equity', 'cohort', 'refi', 'refinance', 'cash-out', 'cash out', 'recapture', 'retention', 'listed', 'listed for sale'], target: { kind: 'route', to: '/segment-intelligence' } },
  { id: 'nav-leads', label: 'Lead Queue', hint: '/lead-queue', icon: 'flow', group: 'Navigate',
    keywords: ['ranked', 'borrowers', 'approve', 'reject', 'queue', 'outreach', 'refi', 'in the money', 'heloc'], target: { kind: 'route', to: '/lead-queue' } },
  { id: 'nav-borrower', label: 'Borrower 360', hint: '/borrower-360', icon: 'user', group: 'Navigate',
    keywords: ['dossier', 'profile', 'evidence', 'proof'], target: { kind: 'route', to: '/borrower-360' } },
  { id: 'nav-offer', label: 'Offer Orchestrator', hint: '/offer-orchestrator', icon: 'send', group: 'Navigate',
    keywords: ['offer', 'next best', 'nbo', 'draft', 'outreach', 'refi', 'refinance', 'cash-out', 'cash out', 'heloc', 'recapture', 'retention'], target: { kind: 'route', to: '/offer-orchestrator' } },
  { id: 'nav-analytics', label: 'Analytics', hint: '/analytics', icon: 'audit', group: 'Navigate',
    keywords: ['executive', 'geography', 'economics', 'signals', 'funnel', 'charts'], target: { kind: 'route', to: '/analytics' } },
  { id: 'nav-genie', label: 'Ask Genie', hint: '/ask-genie', icon: 'sparkle', group: 'Navigate',
    keywords: ['ai', 'question', 'chat', 'natural language', 'sql'], target: { kind: 'route', to: '/ask-genie' } },
  { id: 'nav-glossary', label: 'Glossary', hint: '/glossary', icon: 'doc', group: 'Navigate',
    keywords: ['terms', 'definitions', 'clip', 'owner link', 'help'], target: { kind: 'route', to: '/glossary' } },
  { id: 'nav-admin', label: 'Admin', hint: '/admin-config', icon: 'settings', group: 'Navigate',
    keywords: ['config', 'audit log', 'offer rules', 'governance'], target: { kind: 'route', to: '/admin-config' } },
  // --- Workspace commands ---
  { id: 'cmd-genie', label: 'Open Genie panel', hint: 'Floating assistant', icon: 'sparkle', group: 'Workspace',
    keywords: ['ai', 'assistant', 'chat'], target: { kind: 'command', command: 'open-genie' } },
  { id: 'cmd-theme', label: 'Toggle theme', hint: 'Light / dark', icon: 'moon', group: 'Workspace',
    keywords: ['dark', 'light', 'appearance'], target: { kind: 'command', command: 'toggle-theme' } },
  { id: 'cmd-console', label: 'Toggle Console', hint: 'Theme · accent · density', icon: 'tweak', group: 'Workspace',
    keywords: ['settings', 'density', 'accent', 'tenant'], target: { kind: 'command', command: 'toggle-console' } },
];

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
      label: `Approve ${selection.approveCount.toLocaleString()} selected…`,
      hint: selection.approveCount === 1 ? 'Opens the approval review' : 'Opens the approval rationale gate',
      icon: 'check',
      group: 'Selection',
      keywords: ['approve', 'bulk', 'selected', 'selection', 'rationale'],
      target: { kind: 'verb', verb: 'approve-selected' },
    });
  }
  if (selection.canAssign) {
    verbs.push({
      id: 'verb-assign-selected',
      label: `Assign ${selection.selectedCount.toLocaleString()} selected…`,
      hint: 'Choose a loan officer',
      icon: 'user',
      group: 'Selection',
      keywords: ['assign', 'distribute', 'loan officer', 'lo', 'selected', 'selection'],
      target: { kind: 'verb', verb: 'assign-selected' },
    });
  }
  return verbs;
}

export function commandActionsForAccess(
  canAccessAdmin: boolean,
  actions: readonly CommandAction[] = COMMAND_ACTIONS,
): CommandAction[] {
  return actions.filter((action) => canAccessAdmin || action.id !== 'nav-admin');
}

function normalize(text: string): string {
  return text.toLowerCase().trim();
}

/**
 * Rank one action against a normalized query. Higher is better; 0 means no
 * match (filtered out). Label matches outrank keyword matches; a prefix
 * outranks a mid-string hit so "lead" surfaces Lead Queue first.
 */
export function scoreAction(action: CommandAction, query: string): number {
  const q = normalize(query);
  if (!q) return 1; // no query → everything shows in registry order
  const label = normalize(action.label);
  if (label === q) return 100;
  if (label.startsWith(q)) return 80;
  if (label.includes(q)) return 60;
  const hint = normalize(action.hint);
  if (hint.includes(q)) return 45;
  for (const kw of action.keywords) {
    const k = normalize(kw);
    if (k === q || k.startsWith(q)) return 40;
    if (k.includes(q)) return 25;
  }
  return 0;
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
