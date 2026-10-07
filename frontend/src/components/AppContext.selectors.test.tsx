/**
 * @vitest-environment happy-dom
 *
 * The AppContext selector store (2026-09-21 audit runtime-05). A row expand
 * (setLastBorrowerId) and a drawer open (setDrawer) used to re-render every
 * useApp() consumer, including the evidence chip and confidence meter in
 * each Lead Queue row. This mounts the real AppProvider with N memoised rows,
 * each holding an EvidenceChip and a ConfidenceMeter under a Profiler, and
 * counts their commits.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, memo, Profiler, useEffect, type ProfilerOnRenderCallback } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../test/installLocalStorage';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  session: vi.fn(),
  workspace: vi.fn(),
}));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));

vi.mock('../lib/configOptionsQuery', () => ({ useConfigOptionsQuery: () => ({ data: { lender_name: 'Summit Mortgage', rum_enabled: false } }) }));

// RouteNav calls its dock hook once per render: a render counter that sees
// every re-render. A Profiler placed directly under the provider misses a
// re-render that a context change propagates into its child: react-dom 19.3
// sets the Profiler's update flag from its childLanes in
// attemptEarlyBailoutIfNoUpdate, before bailoutOnAlreadyFinishedWork's lazy
// propagateParentContextChanges marks the consumer. Measured 2026-10-07: on
// a drawer open a direct Profiler stayed at 1 commit while its useApp()
// reader rendered twice; under a memo wrapper (as the row pins below sit) the
// propagation starts above the Profiler and it counted both.
const navRenders = vi.hoisted(() => ({ count: 0 }));
vi.mock('../hooks/useRouteNavDock', () => ({
  useRouteNavDock: () => {
    navRenders.count += 1;
  },
}));

import { AppProvider, useApp, type DrawerSource } from './AppContext';
import { createAppStore } from './appStore';
import { EvidenceChip } from './Primitives';
import { ConfidenceMeter } from './mortgage/ConfidenceMeter';
import { RouteNav } from './layout/RouteNav';

const ROWS = 6;
const SOURCE: DrawerSource = { title: 'Lien history', short: 'Cotality lien' };
const commits = new Map<string, number>();
const onRender: ProfilerOnRenderCallback = (id) => commits.set(id, (commits.get(id) ?? 0) + 1);

const Row = memo(function Row({ index }: { index: number }) {
  return (
    <Profiler id={`row-${index}`} onRender={onRender}>
      <div className="row">
        <EvidenceChip source={SOURCE}>Lien</EvidenceChip>
        <ConfidenceMeter value={72} />
      </div>
    </Profiler>
  );
});

const Rows = memo(function Rows() {
  return (
    <>
      {Array.from({ length: ROWS }, (_, index) => (
        <Row key={index} index={index} />
      ))}
    </>
  );
});

let facade: ReturnType<typeof useApp> | undefined;
function FacadeConsumer() {
  const context = useApp();
  useEffect(() => {
    facade = context;
  }, [context]);
  return null;
}

function LastBorrowerProbe() {
  const { lastBorrowerId } = useApp('lastBorrowerId');
  return <output data-testid="last-borrower">{lastBorrowerId ?? ''}</output>;
}
const probeRenders = () => commits.get('probe') ?? 0;

function rowCommits(): number[] {
  return Array.from({ length: ROWS }, (_, index) => commits.get(`row-${index}`) ?? 0);
}

function current(): NonNullable<typeof facade> {
  if (!facade) throw new Error('the facade consumer has not rendered');
  return facade;
}

describe('AppContext selector store (runtime-05)', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    installLocalStorage();
    commits.clear();
    facade = undefined;
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    apiMocks.session.mockReturnValue(new Promise(() => undefined));
    apiMocks.workspace.mockReturnValue(new Promise(() => undefined));
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function mount() {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <AppProvider>
            <FacadeConsumer />
            <Profiler id="probe" onRender={onRender}>
              <LastBorrowerProbe />
            </Profiler>
            <Rows />
          </AppProvider>
        </QueryClientProvider>,
      );
    });
  }

  it('a row expand and a drawer open leave every row at its mount commit', async () => {
    await mount();
    const atMount = rowCommits();
    expect(atMount.every((count) => count >= 1)).toBe(true);
    expect(document.querySelectorAll('.evidence-chip')).toHaveLength(ROWS);
    expect(document.querySelectorAll('.conf')).toHaveLength(ROWS);
    const probeAtMount = probeRenders();

    await act(async () => current().setLastBorrowerId('B-ABCDEFGHJKLMN'));
    expect(current().lastBorrowerId).toBe('B-ABCDEFGHJKLMN');
    await act(async () => current().setDrawer(SOURCE));
    expect(current().drawer).toBe(SOURCE);

    expect(rowCommits()).toEqual(atMount);
    // Non-vacuity: a keyed lastBorrowerId reader re-renders exactly once.
    expect(probeRenders()).toBe(probeAtMount + 1);
    expect(document.querySelector('[data-testid="last-borrower"]')?.textContent).toBe('B-ABCDEFGHJKLMN');
  });

  it('RouteNav reads lastBorrowerId by key: a drawer open leaves it alone, a new last borrower re-links it', async () => {
    navRenders.count = 0;
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <AppProvider>
              <FacadeConsumer />
              <RouteNav />
            </AppProvider>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    const navCommits = () => navRenders.count;
    const borrowerHref = () => document.querySelector('a.route-nav__link[href^="/borrower-360"]')?.getAttribute('href');
    expect(borrowerHref()).toBe('/borrower-360');
    const atMount = navCommits();

    await act(async () => current().setDrawer(SOURCE));
    expect(current().drawer).toBe(SOURCE);
    expect(navCommits(), 'a drawer open does not re-render the nav').toBe(atMount);

    await act(async () => current().setLastBorrowerId('B-ABCDEFGHJKLMN'));
    expect(navCommits(), 'non-vacuity: the last borrower re-renders it').toBeGreaterThan(atMount);
    expect(borrowerHref()).toBe('/borrower-360/B-ABCDEFGHJKLMN');
  });

  it('toggling showEvidence re-renders the chips, and the meters follow showConfidence', async () => {
    await mount();
    await act(async () => current().setShowEvidence(false));
    expect(document.querySelectorAll('.evidence-chip')).toHaveLength(0);
    expect(document.querySelectorAll('.conf')).toHaveLength(ROWS);
    await act(async () => current().setShowConfidence(false));
    expect(document.querySelectorAll('.conf')).toHaveLength(0);
    await act(async () => current().setShowEvidence(true));
    expect(document.querySelectorAll('.evidence-chip')).toHaveLength(ROWS);
  });

  it('keeps every facade field (41) for useApp() callers', async () => {
    await mount();
    expect(Object.keys(current()).sort()).toEqual([
      'accent', 'acknowledgeRecentActivityFocus', 'actorEmail', 'approvals', 'canAccessAdmin', 'canApprove',
      'clearActorScopedState', 'consoleOpen', 'density', 'drawer', 'genieOpen', 'isLeadSaved', 'lastBorrowerId',
      'lender', 'openConsoleRecentActivity', 'recentActivityFocusRequest', 'refreshWorkspace', 'removeSavedDraft',
      'removeSavedLead', 'saveDraft', 'saveLead', 'savedDrafts', 'savedLeads', 'sessionStatus', 'setAccent',
      'setApproval', 'setConsoleOpen', 'setDensity', 'setDrawer', 'setGenieOpen', 'setLastBorrowerId',
      'setShowConfidence', 'setShowEvidence', 'setTheme', 'setThemePreference', 'showConfidence', 'showEvidence',
      'theme', 'themePreference', 'workspaceError', 'workspaceStatus',
    ].sort());
  });

  it('clearActorScopedState resets approvals, the drawer and the last borrower, not the presenter toggles', async () => {
    await mount();
    await act(async () => {
      current().setApproval('B-ABCDEFGHJKLMN', 'approved');
      current().setDrawer(SOURCE);
      current().setLastBorrowerId('  B-ABCDEFGHJKLMN  ');
      current().setShowEvidence(false);
    });
    expect(current().approvals).toEqual({ 'B-ABCDEFGHJKLMN': 'approved' });
    expect(current().lastBorrowerId).toBe('B-ABCDEFGHJKLMN');
    await act(async () => current().clearActorScopedState());
    expect(current().approvals).toEqual({});
    expect(current().drawer).toBeNull();
    expect(current().lastBorrowerId).toBeNull();
    expect(current().showEvidence).toBe(false);
  });

  it('pick() keeps its identity while the picked values hold, and reset() clears the actor-scoped keys', () => {
    const store = createAppStore();
    const first = store.pick(['showEvidence', 'setDrawer']);
    store.getSnapshot().setLastBorrowerId('B-ABCDEFGHJKLMN');
    expect(store.pick(['showEvidence', 'setDrawer'])).toBe(first);
    store.getSnapshot().setShowEvidence(false);
    const second = store.pick(['showEvidence', 'setDrawer']);
    expect(second).not.toBe(first);
    expect(second).toEqual({ showEvidence: false, setDrawer: first.setDrawer });
    store.getSnapshot().setLastBorrowerId('   ');
    expect(store.getSnapshot().lastBorrowerId).toBeNull();
    store.getSnapshot().setApproval('B-ABCDEFGHJKLMN', 'rejected');
    store.reset();
    expect(store.pick(['approvals', 'drawer', 'lastBorrowerId', 'showEvidence'])).toEqual({
      approvals: {}, drawer: null, lastBorrowerId: null, showEvidence: false,
    });
  });

  it('throws outside a provider in both forms and types only store keys', () => {
    function Bare() {
      useApp();
      return null;
    }
    function BareKeyed() {
      useApp('drawer');
      return null;
    }
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(() => act(() => root.render(<Bare />))).toThrow('useApp must be used inside <AppProvider>');
    expect(() => act(() => root.render(<BareKeyed />))).toThrow('useApp must be used inside <AppProvider>');
    spy.mockRestore();
    function WrongKey() {
      // @ts-expect-error useApp('theme'): theme is a facade field, not a store key.
      useApp('theme');
      return null;
    }
    expect(typeof WrongKey).toBe('function');
  });
});
