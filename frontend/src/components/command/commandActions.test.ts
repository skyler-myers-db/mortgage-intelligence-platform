import { describe, expect, it } from 'vitest';
import {
  COMMAND_ACTIONS,
  filterCommandActions,
  scoreAction,
  type CommandAction,
} from './commandActions';

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
});
