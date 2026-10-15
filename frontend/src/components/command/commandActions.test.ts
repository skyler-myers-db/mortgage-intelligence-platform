import { afterEach, describe, expect, it } from 'vitest';
import {
  COMMAND_ACTIONS,
  commandActionsForAccess,
  commandVerbActions,
  filterCommandActions,
  fuzzyWordPrefixScore,
  scoreAction,
  type CommandAction,
} from './commandActions';
import type { CommandSelectionContext } from './commandSelection';

describe('command palette action registry', () => {
  it('exposes every product-flow route as a navigable action', () => {
    const routes = COMMAND_ACTIONS.filter((a) => a.target.kind === 'route').map((a) =>
      a.target.kind === 'route' ? a.target.to : '',
    );
    for (const to of [
      '/',
      '/portfolio-builder',
      '/segment-intelligence',
      '/lead-queue',
      '/borrower-360',
      '/offer-orchestrator',
      '/analytics',
      '/ask-genie',
      '/glossary',
      '/audit-ledger',
      '/admin-config',
    ]) {
      expect(routes).toContain(to);
    }
  });

  it('removes the Admin destination unless access is affirmative', () => {
    expect(commandActionsForAccess(false).map((action) => action.id)).not.toContain('nav-admin');
    expect(commandActionsForAccess(true).map((action) => action.id)).toContain('nav-admin');
  });

  it('offers the audit ledger to admins and auditors only (D-audit-reads-c3)', () => {
    const ids = (actions: CommandAction[]) => actions.map((action) => action.id);
    const admin = ids(commandActionsForAccess(true));
    expect(admin).toContain('nav-admin');
    expect(admin).toContain('nav-audit-ledger');
    const auditor = ids(commandActionsForAccess(false, COMMAND_ACTIONS, true));
    expect(auditor).toContain('nav-audit-ledger');
    expect(auditor).not.toContain('nav-admin');
    const plain = ids(commandActionsForAccess(false));
    expect(plain).not.toContain('nav-audit-ledger');
    expect(plain).not.toContain('nav-admin');
  });

  it('finds the ledger by "audit log", not Admin', () => {
    const ids = filterCommandActions('audit log').map((action) => action.id);
    expect(ids[0]).toBe('nav-audit-ledger');
    expect(ids).not.toContain('nav-admin');
  });

  it('returns the full registry (in order) for an empty query', () => {
    expect(filterCommandActions('')).toEqual([...COMMAND_ACTIONS]);
    expect(filterCommandActions('   ')).toEqual([...COMMAND_ACTIONS]);
  });

  it('ranks an exact label above a prefix above a substring above a keyword', () => {
    const exact: CommandAction = {
      id: 'x', label: 'Leads', hint: '', icon: 'flow', group: 'Navigate', keywords: [],
      target: { kind: 'route', to: '/x' },
    };
    const prefix: CommandAction = { ...exact, id: 'p', label: 'Leads dashboard' };
    const substr: CommandAction = { ...exact, id: 's', label: 'My Leads Page' };
    const keyword: CommandAction = { ...exact, id: 'k', label: 'Queue', keywords: ['leads'] };
    expect(scoreAction(exact, 'leads')).toBeGreaterThan(scoreAction(prefix, 'leads'));
    expect(scoreAction(prefix, 'leads')).toBeGreaterThan(scoreAction(substr, 'leads'));
    expect(scoreAction(substr, 'leads')).toBeGreaterThan(scoreAction(keyword, 'leads'));
  });

  it('matches by keyword so domain terms surface the right page', () => {
    const ids = filterCommandActions('investor').map((a) => a.id);
    expect(ids).toContain('nav-segments'); // "investor" is a segment keyword
  });

  it('surfaces Lead Queue first when the query is "lead"', () => {
    const first = filterCommandActions('lead')[0];
    expect(first.id).toBe('nav-leads');
  });

  it('drops non-matches entirely', () => {
    expect(filterCommandActions('zzzznope')).toEqual([]);
  });

  it('is case-insensitive', () => {
    expect(filterCommandActions('GENIE').length).toBeGreaterThan(0);
  });

  it('fuzzy step: a run of word prefixes finds the page (shell-07 item 5)', () => {
    expect(filterCommandActions('leaqu')[0]?.id).toBe('nav-leads');
    expect(filterCommandActions('segint')[0]?.id).toBe('nav-segments');
    expect(fuzzyWordPrefixScore('Lead Queue', 'leaqu')).toBe(20);
    expect(fuzzyWordPrefixScore('Toggle Lead Queue', 'leaqu')).toBe(10);
    expect(fuzzyWordPrefixScore('Lead Queue', 'lq')).toBe(0); // under three letters
    expect(fuzzyWordPrefixScore('Lead Queue', 'leaxq')).toBe(0);
  });

  it('fuzzy step never outranks a keyword hit, so the synonyms resolve first', () => {
    const fuzzy: CommandAction = {
      id: 'f', label: 'Refund Fixer', hint: '', icon: 'flow', group: 'Navigate', keywords: [],
      target: { kind: 'route', to: '/f' },
    };
    const keyword: CommandAction = { ...fuzzy, id: 'k', label: 'Queue', keywords: ['refix things'] };
    expect(scoreAction(fuzzy, 'refi')).toBeGreaterThan(0);
    expect(scoreAction(keyword, 'refi')).toBeGreaterThan(scoreAction(fuzzy, 'refi'));
    expect(scoreAction(fuzzy, 'refi')).toBeLessThan(25);
    expect(filterCommandActions('refi')[0]?.id).toBe('nav-segments');
  });

  it('masked ids and ZIPs never fuzzy-match', () => {
    expect(fuzzyWordPrefixScore('Borrower 360', 'b-1eeen00s99gxc')).toBe(0);
    expect(fuzzyWordPrefixScore('B-1EEEN00S99GXC', 'beee')).toBe(0);
    expect(fuzzyWordPrefixScore('Zone 60611 Lead', '60611')).toBe(0);
    expect(filterCommandActions('60611')).toEqual([]);
  });

  it('offers the shortcut sheet as a workspace command bound to `?` (shell-07 item 3)', () => {
    const sheet = COMMAND_ACTIONS.find((action) => action.id === 'cmd-shortcuts');
    expect(sheet).toEqual(expect.objectContaining({
      label: 'Keyboard shortcuts',
      target: { kind: 'command', command: 'open-shortcuts' },
      shortcut: { bindingId: 'shortcut-sheet', chord: '?' },
    }));
  });

  it('keeps the Ask Genie route distinct from the floating panel command', () => {
    const genieLabels = COMMAND_ACTIONS
      .filter((action) => action.id === 'nav-genie' || action.id === 'cmd-genie')
      .map((action) => action.label);
    expect(genieLabels).toEqual(['Ask Genie', 'Open Genie panel']);
  });
});

describe('selection verbs (responsive-04: one number format)', () => {
  const selection = (overrides: Partial<CommandSelectionContext>): CommandSelectionContext => ({
    selectedCount: 1,
    approveCount: 1,
    canApprove: true,
    rejectCount: 0,
    canReject: true,
    canAssign: true,
    run: () => undefined,
    ...overrides,
  });

  // The browser's default locale, forced to German for one test: a label
  // built with `n.toLocaleString()` or `new Intl.NumberFormat()` would then
  // read "1.234" (W5d review leftover: the old test never changed the locale).
  const originalToLocaleString = Number.prototype.toLocaleString;
  const OriginalNumberFormat = Intl.NumberFormat;
  function forceDefaultLocale(locale: string): void {
    Number.prototype.toLocaleString = function toLocaleString(
      this: number,
      locales?: Intl.LocalesArgument,
      options?: Intl.NumberFormatOptions,
    ): string {
      return originalToLocaleString.call(this, locales ?? locale, options);
    };
    const Forced = function NumberFormat(locales?: Intl.LocalesArgument, options?: Intl.NumberFormatOptions) {
      return new OriginalNumberFormat(locales ?? locale, options);
    } as unknown as typeof Intl.NumberFormat;
    Object.assign(Forced, { supportedLocalesOf: OriginalNumberFormat.supportedLocalesOf });
    Intl.NumberFormat = Forced;
  }
  afterEach(() => {
    Number.prototype.toLocaleString = originalToLocaleString;
    Intl.NumberFormat = OriginalNumberFormat;
  });

  it('groups the counts with en-US separators whatever the browser locale', () => {
    forceDefaultLocale('de-DE');
    // The stub really changes the default: an unpinned format reads German.
    expect((1234).toLocaleString()).toBe('1.234');
    expect(new Intl.NumberFormat().format(1234)).toBe('1.234');
    const labels = commandVerbActions(selection({ approveCount: 1234, rejectCount: 1234, selectedCount: 2500 }))
      .map((action) => action.label);
    expect(labels).toEqual(['Approve 1,234 selected…', 'Reject 1,234 selected…', 'Assign 2,500 selected…']);
  });

  it('wires the approve and reject verbs to the Lead Queue bulk binding (shell-07 item 3)', () => {
    const shortcuts = commandVerbActions(selection({ approveCount: 2, rejectCount: 2, selectedCount: 2 }))
      .map((action) => [action.id, action.shortcut]);
    expect(shortcuts).toEqual([
      ['verb-approve-selected', { bindingId: 'lead-table-bulk-approve', chord: 'Shift+A' }],
      ['verb-reject-selected', { bindingId: 'lead-table-bulk-approve', chord: 'Shift+R' }],
      ['verb-assign-selected', undefined],
    ]);
  });

  it('offers Reject with the panel hint for one row and the gate hint for several (tables-07)', () => {
    const one = commandVerbActions(selection({ rejectCount: 1 })).find((action) => action.id === 'verb-reject-selected');
    expect(one).toEqual(expect.objectContaining({
      label: 'Reject 1 selected…',
      hint: 'Opens the reject panel',
      target: { kind: 'verb', verb: 'reject-selected' },
    }));
    const many = commandVerbActions(selection({ rejectCount: 7 })).find((action) => action.id === 'verb-reject-selected');
    expect(many?.hint).toBe('Opens the rejection reason gate');
    expect(commandVerbActions(selection({ rejectCount: 7, canReject: false })).map((action) => action.id))
      .not.toContain('verb-reject-selected');
  });

  it('offers no verb without a selection', () => {
    expect(commandVerbActions(null)).toEqual([]);
    expect(commandVerbActions(selection({ selectedCount: 0 }))).toEqual([]);
  });
});
