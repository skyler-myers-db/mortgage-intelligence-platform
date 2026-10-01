import {
  createContext,
  use,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type Dispatch,
  type PropsWithChildren,
  type SetStateAction,
} from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { queryKeys } from '../lib/queryKeys';
import { sessionQueryOptions } from '../lib/sessionQuery';
import {
  DEFAULT_ACCENT,
  DEFAULT_DENSITY,
  DEFAULT_THEME_PREFERENCE,
  DENSITIES,
  CONSOLE_OPEN_STORAGE_KEY,
  DENSITY_STORAGE_KEY,
  persistAccent,
  persistDensity,
  persistThemePreference,
  readAccentPreference,
  readStoredChoice,
  readThemePreference,
  resolveTheme,
  subscribeSystemTheme,
  syncThemeColorMeta,
  systemPrefersDark,
  type Accent,
  type Density,
  type Theme,
  type ThemePreference,
} from '../lib/themePreference';
import { changeTheme } from '../lib/themeTransition';
import { AppStoreContext, createAppStore, type AppStoreKey } from './appStore';
import type {
  ConfigOptions,
  SavedDraft,
  SavedDraftInput,
  SavedLead,
  SavedLeadInput,
  SessionResponse,
} from '../types';

/**
 * AppContext — theme, accent, density, configured tenant, drawer, Genie, approvals,
 * evidence toggles. Ported from the Module 0 prototype so every page shares
 * one provider and writes data-theme/data-accent/data-density to <html>.
 *
 * Theme model: `themePreference` is what the user chose (dark, light or
 * system); `theme` is what is painted. public/theme-boot.js applies the same
 * attributes before first paint from the same storage keys (see
 * lib/themePreference.ts), so the post-mount effect below only re-asserts
 * them and takes over live changes (Console, OS scheme flips). Nothing is
 * stored on mount: only an explicit pick persists, with its choice marker
 * (a first visit boots dark and stays unstored; 2026-09-30, report 12.4 #4).
 *
 * Narrow reads (audit runtime-05): the evidence / confidence toggles, the
 * drawer, the approvals and the last borrower live in a selector store
 * (./appStore). `useApp()` returns the whole facade exactly as before and
 * re-renders on any change; `useApp('setDrawer', 'showEvidence')` returns
 * just those keys and re-renders only when one of them changes. A non-store
 * key is a type error, and a test's `vi.mock` of useApp ignores the keys, so
 * every mock keeps working. On it now: EvidenceChip and ConfidenceMeter
 * (Primitives.tsx, ConfidenceMeter.tsx). Next (W5c): LeadTable and
 * LeadTableRow (w5-lead-queue-paging), RouteNav's lastBorrowerId
 * (w5-shell-nav-followups).
 */

export type { Accent, Density, Theme, ThemePreference };

export interface DrawerSource {
  title: string;
  description?: string;
  signals?: Array<{ label: string; source: string; value: string }>;
  updatedAt?: string;
  eventDate?: string;
  short?: string;
  assetKey?: string;
  assetPath?: string;
  usedIn?: string[];
  notExposed?: string;
  /**
   * Family id in the governed lineage manifest
   * (backend/resources/lineage_manifest.json). Drives the drawer's
   * Lineage tab; sources without one show an explicit "lineage not
   * mapped" state — the UI never invents nodes.
   */
  lineageFamily?: string;
}

interface AppCtxValue {
  /** The painted theme (system preference already resolved). */
  theme: Theme;
  /** Pin an explicit theme; the Topbar toggle and Cmd-K use this. */
  setTheme: (t: Theme) => void;
  /** What the user chose, including `system`; the Console control edits this. */
  themePreference: ThemePreference;
  setThemePreference: (p: ThemePreference) => void;
  accent: Accent;
  setAccent: (a: Accent) => void;
  density: Density;
  setDensity: (d: Density) => void;
  lender: string;
  canAccessAdmin: boolean;
  /** Server-decided approver capability; false until /session says otherwise. */
  canApprove: boolean;
  /** The signed-in actor's own forwarded identity, for "Approving as …". */
  actorEmail: string | null;
  /** Lets gated controls say "checking" instead of a false "requires role". */
  sessionStatus: 'loading' | 'ready' | 'error';
  showEvidence: boolean;
  setShowEvidence: (v: boolean) => void;
  showConfidence: boolean;
  setShowConfidence: (v: boolean) => void;
  consoleOpen: boolean;
  setConsoleOpen: (v: boolean) => void;
  recentActivityFocusRequest: number;
  openConsoleRecentActivity: () => void;
  acknowledgeRecentActivityFocus: () => void;
  drawer: DrawerSource | null;
  setDrawer: (d: DrawerSource | null) => void;
  genieOpen: boolean;
  setGenieOpen: (v: boolean) => void;
  approvals: Record<string, 'approved' | 'rejected'>;
  setApproval: (borrowerId: string, state: 'approved' | 'rejected') => void;
  lastBorrowerId: string | null;
  setLastBorrowerId: (borrowerId: string | null) => void;
  clearActorScopedState: () => void;
  savedLeads: Record<string, SavedLead>;
  saveLead: (lead: SavedLeadInput) => void;
  removeSavedLead: (borrowerId: string) => void;
  isLeadSaved: (borrowerId: string) => boolean;
  savedDrafts: Record<string, SavedDraft>;
  saveDraft: (draft: SavedDraftInput) => Promise<SavedDraft>;
  removeSavedDraft: (borrowerId: string, channel?: SavedDraft['channel']) => void;
  workspaceStatus: 'loading' | 'ready' | 'error';
  workspaceError: string | null;
  refreshWorkspace: () => void;
}

const AppCtx = createContext<AppCtxValue | null>(null);

function readStoredBool(key: string, fallback: boolean): boolean {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === 'true') return true;
    if (raw === 'false') return false;
  } catch {
    // ignore
  }
  return fallback;
}

/**
 * The RUM gate: the session's `rum_enabled` when it is a boolean (audit
 * delivery-07: it rides the zero-dependency session call), else the options
 * call's. Backward compatible: called with the options alone, it reads them.
 */
export function shouldInstallRum(
  configOptions: Pick<ConfigOptions, 'rum_enabled'> | undefined,
  session?: Pick<SessionResponse, 'rum_enabled'>,
): boolean {
  if (typeof session?.rum_enabled === 'boolean') return session.rum_enabled;
  return configOptions?.rum_enabled === true;
}

interface ActorScopedResetSetters {
  setApprovals: Dispatch<SetStateAction<Record<string, 'approved' | 'rejected'>>>;
  setDrawer: Dispatch<SetStateAction<DrawerSource | null>>;
  setGenieOpen: Dispatch<SetStateAction<boolean>>;
  setLastBorrowerIdState: Dispatch<SetStateAction<string | null>>;
  setSavedLeads: Dispatch<SetStateAction<Record<string, SavedLead>>>;
  setSavedDrafts: Dispatch<SetStateAction<Record<string, SavedDraft>>>;
  setWorkspaceStatus: Dispatch<SetStateAction<'loading' | 'ready' | 'error'>>;
  setWorkspaceError: Dispatch<SetStateAction<string | null>>;
  setWorkspaceReloadToken: Dispatch<SetStateAction<number>>;
}

export function resetActorScopedAppState(setters: ActorScopedResetSetters): void {
  setters.setApprovals({});
  setters.setDrawer(null);
  setters.setGenieOpen(false);
  setters.setLastBorrowerIdState(null);
  setters.setSavedLeads({});
  setters.setSavedDrafts({});
  setters.setWorkspaceStatus('loading');
  setters.setWorkspaceError(null);
  setters.setWorkspaceReloadToken((n) => n + 1);
}

function mapByBorrower<T extends { borrower_id: string }>(items: T[]): Record<string, T> {
  return Object.fromEntries(items.map((item) => [item.borrower_id, item]));
}

function draftKey(borrowerId: string, channel: SavedDraft['channel'] = 'email'): string {
  return `${borrowerId}::${channel}`;
}

function mapDraftsByBorrowerChannel(items: SavedDraft[]): Record<string, SavedDraft> {
  return Object.fromEntries(items.map((item) => [draftKey(item.borrower_id, item.channel), item]));
}

export function AppProvider({ children }: PropsWithChildren) {
  const [themePreference, setThemePreferenceState] = useState<ThemePreference>(() =>
    readThemePreference(DEFAULT_THEME_PREFERENCE),
  );
  const [systemDark, setSystemDark] = useState<boolean>(() => systemPrefersDark());
  const theme = resolveTheme(themePreference, systemDark);
  const [accent, setAccentState] = useState<Accent>(() => readAccentPreference(DEFAULT_ACCENT));
  const [density, setDensityState] = useState<Density>(() =>
    readStoredChoice(DENSITY_STORAGE_KEY, DEFAULT_DENSITY, DENSITIES),
  );
  const sessionQuery = useQuery(sessionQueryOptions());
  // Module 0 does not support arbitrary client-side lender switching.
  // The tenant label is display-only; lender predicates are resolved by
  // backend configuration and the Unity Catalog gold views. It reads the
  // zero-dependency session first (audit delivery-07), so it no longer waits
  // on the warehouse-backed options call; options stay the fallback.
  const configOptionsQuery = useConfigOptionsQuery();
  const lender = sessionQuery.data?.lender_name?.trim()
    || configOptionsQuery.data?.lender_name?.trim()
    || 'Configured lender';
  // RUM installs once and never uninstalls, so the gate waits for the session
  // to settle: an options answer that lands first cannot install what the
  // session would have vetoed. A failed session check falls back to options.
  const rumEnabled = !sessionQuery.isPending && shouldInstallRum(configOptionsQuery.data, sessionQuery.data);
  const [store] = useState(createAppStore);
  const app = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  // Console is opt-in so the first demo viewport uses the full prototype
  // layout. Presenter preference persists across reloads via localStorage
  // (same pattern as theme/accent/density).
  const [consoleOpen, setConsoleOpenState] = useState<boolean>(() =>
    readStoredBool(CONSOLE_OPEN_STORAGE_KEY, false),
  );
  const [recentActivityFocusRequest, setRecentActivityFocusRequest] = useState(0);
  const [genieOpen, setGenieOpen] = useState(false);
  const [savedLeads, setSavedLeads] = useState<Record<string, SavedLead>>({});
  const [savedDrafts, setSavedDrafts] = useState<Record<string, SavedDraft>>({});
  const [workspaceStatus, setWorkspaceStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [workspaceReloadToken, setWorkspaceReloadToken] = useState(0);
  // TanStack retains the last successful session payload during background
  // refetches. Authorization-sensitive surfaces therefore fail closed on the
  // first load without flickering away after access has been established.
  const canAccessAdmin = sessionQuery.data?.can_access_admin === true;
  // Audit flow-02 / shell-06: the approve UI used to ignore `can_approve`, so
  // non-approvers got live Approve buttons that wrote a DRAFT_OUTREACH audit
  // row and then 403'd. The server 403 stays the real enforcement.
  const canApprove = sessionQuery.data?.can_approve === true;
  const actorEmail = sessionQuery.data?.actor_email?.trim() || null;
  const sessionStatus = sessionQuery.data
    ? 'ready'
    : sessionQuery.isError ? 'error' : 'loading';
  const workspaceQuery = useQuery({
    queryKey: [...queryKeys.workspace(), workspaceReloadToken],
    queryFn: ({ signal }) => api.workspace(signal),
    retry: false,
  });

  useEffect(() => {
    if (!rumEnabled) return;
    let cancelled = false;
    void import('../lib/rum')
      .then(({ installRum }) => {
        if (!cancelled) installRum();
      })
      .catch(() => {
        // RUM is best-effort and opt-in. A telemetry chunk load failure must
        // never block the app shell or change user-visible behavior.
      });
    return () => {
      cancelled = true;
    };
  }, [rumEnabled]);

  // Follow the OS scheme live while the preference is `system`. Subscribed
  // unconditionally so a later switch to System already has the current
  // value; resolveTheme ignores it for an explicit dark/light.
  useEffect(() => subscribeSystemTheme(setSystemDark), []);

  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute('data-theme', theme);
    root.setAttribute('data-accent', accent);
    root.setAttribute('data-density', density);
    // Browser chrome (Safari tab bar, PWA title bar) follows the page
    // background of the painted theme; theme-boot.js set the boot value.
    syncThemeColorMeta(root);
  }, [theme, accent, density]);

  // Reflect console-open state on <html> (so .app-shell can pad .main when the
  // Console overlays the right edge) and persist the presenter's preference.
  useEffect(() => {
    document.documentElement.setAttribute('data-console', consoleOpen ? 'open' : 'closed');
    try {
      window.localStorage.setItem(CONSOLE_OPEN_STORAGE_KEY, consoleOpen ? 'true' : 'false');
    } catch {
      // ignore
    }
  }, [consoleOpen]);

  useEffect(() => {
    if (workspaceQuery.isPending) {
      setWorkspaceStatus((cur) => (cur === 'ready' ? cur : 'loading'));
      return;
    }
    if (workspaceQuery.isSuccess) {
      setSavedLeads(mapByBorrower(workspaceQuery.data.saved_leads));
      setSavedDrafts(mapDraftsByBorrowerChannel(workspaceQuery.data.saved_drafts));
      setWorkspaceStatus('ready');
      setWorkspaceError(null);
      return;
    }
    if (workspaceQuery.isError) {
      setWorkspaceStatus('error');
      setWorkspaceError(
        workspaceQuery.error instanceof Error
          ? `Couldn't load saved workspace: ${workspaceQuery.error.message}`
          : "Couldn't load saved workspace.",
      );
    }
  }, [
    workspaceQuery.data,
    workspaceQuery.error,
    workspaceQuery.isError,
    workspaceQuery.isPending,
    workspaceQuery.isSuccess,
  ]);

  // A change of the PAINTED theme cross-fades (lib/themeTransition, audit
  // motion-03); the data-theme effect above has run by the time its
  // flushSync'd update returns, so the new snapshot shows the new theme.
  // The pick is stored first, synchronously, never inside the transition's
  // callback (which runs a task later, or never when it is skipped).
  const setTheme = useCallback(
    (t: Theme) => {
      persistThemePreference(t);
      changeTheme(theme, t, () => setThemePreferenceState(t));
    },
    [theme],
  );
  const setThemePreference = useCallback(
    (p: ThemePreference) => {
      persistThemePreference(p);
      changeTheme(theme, resolveTheme(p, systemDark), () => setThemePreferenceState(p));
    },
    [systemDark, theme],
  );
  const setAccent = useCallback((a: Accent) => {
    persistAccent(a);
    setAccentState(a);
  }, []);
  const setDensity = useCallback((d: Density) => {
    persistDensity(d);
    setDensityState(d);
  }, []);
  const setConsoleOpen = useCallback((v: boolean) => setConsoleOpenState(v), []);
  const openConsoleRecentActivity = useCallback(() => {
    setConsoleOpenState(true);
    setRecentActivityFocusRequest((request) => request + 1);
  }, []);
  const acknowledgeRecentActivityFocus = useCallback(() => {
    setRecentActivityFocusRequest(0);
  }, []);
  const clearActorScopedState = useCallback(() => {
    resetActorScopedAppState({
      setApprovals: (action) => store.update('approvals', action),
      setDrawer: (action) => store.update('drawer', action),
      setGenieOpen,
      setLastBorrowerIdState: (action) => store.update('lastBorrowerId', action),
      setSavedLeads,
      setSavedDrafts,
      setWorkspaceStatus,
      setWorkspaceError,
      setWorkspaceReloadToken,
    });
  }, [store]);
  const saveLead = useCallback((lead: SavedLeadInput) => {
    if (!lead.borrower_id) return;
    const now = new Date().toISOString();
    store.getSnapshot().setLastBorrowerId(lead.borrower_id);
    let prior: SavedLead | undefined;
    setSavedLeads((cur) => {
      prior = cur[lead.borrower_id];
      return {
        ...cur,
        [lead.borrower_id]: {
          ...prior,
          ...lead,
          saved_at: prior?.saved_at ?? now,
          updated_at: now,
        },
      };
    });
    void api.saveWorkspaceLead(lead)
      .then((saved) => {
        setSavedLeads((cur) => ({ ...cur, [saved.borrower_id]: saved }));
        setWorkspaceStatus('ready');
        setWorkspaceError(null);
      })
      .catch((err: unknown) => {
        setSavedLeads((cur) => {
          const { [lead.borrower_id]: _discard, ...rest } = cur;
          return prior ? { ...rest, [lead.borrower_id]: prior } : rest;
        });
        setWorkspaceStatus('error');
        setWorkspaceError(
          err instanceof Error
            ? `Couldn't save lead: ${err.message}`
            : "Couldn't save lead.",
        );
      });
  }, [store]);
  const removeSavedLead = useCallback((borrowerId: string) => {
    let prior: SavedLead | undefined;
    setSavedLeads((cur) => {
      prior = cur[borrowerId];
      const { [borrowerId]: _discard, ...rest } = cur;
      return rest;
    });
    void api.deleteWorkspaceLead(borrowerId)
      .then(() => {
        setWorkspaceStatus('ready');
        setWorkspaceError(null);
      })
      .catch((err: unknown) => {
        if (prior) setSavedLeads((cur) => ({ ...cur, [borrowerId]: prior as SavedLead }));
        setWorkspaceStatus('error');
        setWorkspaceError(
          err instanceof Error
            ? `Couldn't remove saved lead: ${err.message}`
            : "Couldn't remove saved lead.",
        );
      });
  }, []);
  const isLeadSaved = useCallback(
    (borrowerId: string) => Boolean(savedLeads[borrowerId]),
    [savedLeads],
  );
  const saveDraft = useCallback(async (draft: SavedDraftInput): Promise<SavedDraft> => {
    if (
      !draft.borrower_id
      || !draft.generation_id
      || !/^[0-9a-f]{64}$/.test(draft.response_hash)
    ) {
      throw new Error('A borrower and audited draft proof are required.');
    }
    store.getSnapshot().setLastBorrowerId(draft.borrower_id);
    try {
      const saved = await api.saveWorkspaceDraft(draft);
      setSavedDrafts((cur) => ({
        ...cur,
        [draftKey(saved.borrower_id, saved.channel)]: saved,
      }));
      setWorkspaceStatus('ready');
      setWorkspaceError(null);
      return saved;
    } catch (err: unknown) {
      setWorkspaceStatus('error');
      setWorkspaceError(
        err instanceof Error
          ? `Couldn't save draft: ${err.message}`
          : "Couldn't save draft.",
      );
      throw err;
    }
  }, [store]);
  const removeSavedDraft = useCallback((borrowerId: string, channel: SavedDraft['channel'] = 'email') => {
    let prior: SavedDraft | undefined;
    const key = draftKey(borrowerId, channel);
    setSavedDrafts((cur) => {
      prior = cur[key];
      const { [key]: _discard, ...rest } = cur;
      return rest;
    });
    void api.deleteWorkspaceDraft(borrowerId, channel)
      .then(() => {
        setWorkspaceStatus('ready');
        setWorkspaceError(null);
      })
      .catch((err: unknown) => {
        if (prior) setSavedDrafts((cur) => ({ ...cur, [key]: prior as SavedDraft }));
        setWorkspaceStatus('error');
        setWorkspaceError(
          err instanceof Error
            ? `Couldn't remove draft: ${err.message}`
            : "Couldn't remove draft.",
        );
      });
  }, []);
  const refreshWorkspace = useCallback(() => {
    setWorkspaceStatus((cur) => (cur === 'ready' ? cur : 'loading'));
    setWorkspaceReloadToken((n) => n + 1);
  }, []);

  const value = useMemo<AppCtxValue>(
    () => ({
      theme, setTheme,
      themePreference, setThemePreference,
      accent, setAccent,
      density, setDensity,
      lender,
      canAccessAdmin,
      canApprove,
      actorEmail,
      sessionStatus,
      showEvidence: app.showEvidence, setShowEvidence: app.setShowEvidence,
      showConfidence: app.showConfidence, setShowConfidence: app.setShowConfidence,
      consoleOpen, setConsoleOpen,
      recentActivityFocusRequest, openConsoleRecentActivity, acknowledgeRecentActivityFocus,
      drawer: app.drawer, setDrawer: app.setDrawer,
      genieOpen, setGenieOpen,
      approvals: app.approvals, setApproval: app.setApproval,
      lastBorrowerId: app.lastBorrowerId,
      setLastBorrowerId: app.setLastBorrowerId,
      clearActorScopedState,
      savedLeads,
      saveLead,
      removeSavedLead,
      isLeadSaved,
      savedDrafts,
      saveDraft,
      removeSavedDraft,
      workspaceStatus,
      workspaceError,
      refreshWorkspace,
    }),
    [
      theme, setTheme, themePreference, setThemePreference, accent, setAccent, density, setDensity,
      lender, canAccessAdmin, canApprove, actorEmail, sessionStatus,
      app, consoleOpen, setConsoleOpen,
      recentActivityFocusRequest, openConsoleRecentActivity, acknowledgeRecentActivityFocus,
      genieOpen, clearActorScopedState,
      savedLeads, saveLead, removeSavedLead, isLeadSaved,
      savedDrafts, saveDraft, removeSavedDraft,
      workspaceStatus, workspaceError, refreshWorkspace,
    ]
  );

  return (
    <AppStoreContext.Provider value={store}>
      <AppCtx.Provider value={value}>{children}</AppCtx.Provider>
    </AppStoreContext.Provider>
  );
}

const NO_KEYS: readonly AppStoreKey[] = [];

/**
 * The app context. With no argument: the whole facade, re-rendering on any
 * change. With store keys: just those (see the header), re-rendering only
 * when one of them changes.
 */
// The no-argument overload is declared last, so ReturnType<typeof useApp>
// (used by tests) stays the full facade.
export function useApp<K extends AppStoreKey>(...keys: [K, ...K[]]): Pick<AppCtxValue, K>;
export function useApp(): AppCtxValue;
export function useApp(...keys: AppStoreKey[]): AppCtxValue | Pick<AppCtxValue, AppStoreKey> {
  const store = useContext(AppStoreContext);
  if (!store) throw new Error('useApp must be used inside <AppProvider>');
  const read = () => store.pick(keys.length > 0 ? keys : NO_KEYS);
  // The same read serves server rendering (AppShell's static-markup tests).
  const picked = useSyncExternalStore(store.subscribe, read, read);
  if (keys.length > 0) return picked;
  const v = use(AppCtx);
  if (!v) throw new Error('useApp must be used inside <AppProvider>');
  return v;
}
