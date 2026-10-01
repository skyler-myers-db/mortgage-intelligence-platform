import { describe, expect, it } from 'vitest';
import {
  COMMAND_ACTIONS,
  commandActionsForAccess,
  commandVerbActions,
  filterCommandActions,
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
      '/admin-config',
    ]) {
      expect(routes).toContain(to);
    }
  });

  it('removes the Admin destination unless access is affirmative', () => {
    expect(commandActionsForAccess(false).map((action) => action.id)).not.toContain('nav-admin');
    expect(commandActionsForAccess(true).map((action) => action.id)).toContain('nav-admin');
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

  it('groups the counts with en-US separators whatever the browser locale', () => {
    const labels = commandVerbActions(selection({ approveCount: 1234, rejectCount: 1234, selectedCount: 2500 }))
      .map((action) => action.label);
    expect(labels).toEqual(['Approve 1,234 selected…', 'Reject 1,234 selected…', 'Assign 2,500 selected…']);
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
