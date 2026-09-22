import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type PropsWithChildren,
  type SetStateAction,
} from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { queryKeys } from '../lib/queryKeys';
import {
  ACCENTS,
  ACCENT_STORAGE_KEY,
  DEFAULT_ACCENT,
  DEFAULT_DENSITY,
  DEFAULT_THEME_PREFERENCE,
  DENSITIES,
  DENSITY_STORAGE_KEY,
  THEME_PREFERENCES,
  THEME_STORAGE_KEY,
  readStoredChoice,
  resolveTheme,
  subscribeSystemTheme,
  syncThemeColorMeta,
  systemPrefersDark,
  type Accent,
  type Density,
  type Theme,
  type ThemePreference,
} from '../lib/themePreference';
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
 * them and takes over live changes (Console, OS scheme flips).
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

export function shouldInstallRum(configOptions: Pick<ConfigOptions, 'rum_enabled'> | undefined): boolean {
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
    readStoredChoice(THEME_STORAGE_KEY, DEFAULT_THEME_PREFERENCE, THEME_PREFERENCES),
  );
  const [systemDark, setSystemDark] = useState<boolean>(() => systemPrefersDark());
  const theme = resolveTheme(themePreference, systemDark);
  const [accent, setAccentState] = useState<Accent>(() =>
    readStoredChoice(ACCENT_STORAGE_KEY, DEFAULT_ACCENT, ACCENTS),
  );
  const [density, setDensityState] = useState<Density>(() =>
    readStoredChoice(DENSITY_STORAGE_KEY, DEFAULT_DENSITY, DENSITIES),
  );
  // Module 0 does not support arbitrary client-side lender switching.
  // The tenant label is display-only; lender predicates are resolved by
  // backend configuration and the Unity Catalog gold views.
  const configOptionsQuery = useConfigOptionsQuery();
  const lender = configOptionsQuery.data?.lender_name?.trim() || 'Configured lender';
  const rumEnabled = shouldInstallRum(configOptionsQuery.data);
  const [showEvidence, setShowEvidence] = useState(true);
  const [showConfidence, setShowConfidence] = useState(true);
  // Console is opt-in so the first demo viewport uses the full prototype
  // layout. Presenter preference persists across reloads via localStorage
  // (same pattern as theme/accent/density).
  const [consoleOpen, setConsoleOpenState] = useState<boolean>(() =>
    readStoredBool('mip.consoleOpen', false),
  );
  const [recentActivityFocusRequest, setRecentActivityFocusRequest] = useState(0);
  const [drawer, setDrawer] = useState<DrawerSource | null>(null);
  const [genieOpen, setGenieOpen] = useState(false);
  const [approvals, setApprovals] = useState<Record<string, 'approved' | 'rejected'>>({});
  const [lastBorrowerIdState, setLastBorrowerIdState] = useState<string | null>(null);
  const [savedLeads, setSavedLeads] = useState<Record<string, SavedLead>>({});
  const [savedDrafts, setSavedDrafts] = useState<Record<string, SavedDraft>>({});
  const [workspaceStatus, setWorkspaceStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [workspaceReloadToken, setWorkspaceReloadToken] = useState(0);
  const sessionQuery = useQuery<SessionResponse>({
    queryKey: ['session', 'access'],
    queryFn: ({ signal }) => api.session(signal),
    retry: false,
  });
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
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, themePreference);
      window.localStorage.setItem(ACCENT_STORAGE_KEY, accent);
      window.localStorage.setItem(DENSITY_STORAGE_KEY, density);
    } catch {
      // ignore
    }
  }, [theme, themePreference, accent, density]);

  // Reflect console-open state on <html> (so .app-shell can pad .main when the
  // Console overlays the right edge) and persist the presenter's preference.
  useEffect(() => {
    document.documentElement.setAttribute('data-console', consoleOpen ? 'open' : 'closed');
    try {
      window.localStorage.setItem('mip.consoleOpen', consoleOpen ? 'true' : 'false');
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

  const setTheme = useCallback((t: Theme) => setThemePreferenceState(t), []);
  const setThemePreference = useCallback((p: ThemePreference) => setThemePreferenceState(p), []);
  const setAccent = useCallback((a: Accent) => setAccentState(a), []);
  const setDensity = useCallback((d: Density) => setDensityState(d), []);
  const setConsoleOpen = useCallback((v: boolean) => setConsoleOpenState(v), []);
  const openConsoleRecentActivity = useCallback(() => {
    setConsoleOpenState(true);
    setRecentActivityFocusRequest((request) => request + 1);
  }, []);
  const acknowledgeRecentActivityFocus = useCallback(() => {
    setRecentActivityFocusRequest(0);
  }, []);
  const setApproval = useCallback((borrowerId: string, state: 'approved' | 'rejected') => {
    setApprovals((cur) => ({ ...cur, [borrowerId]: state }));
  }, []);
  const setLastBorrowerId = useCallback((borrowerId: string | null) => {
    const next = borrowerId?.trim();
    setLastBorrowerIdState(next && next.length > 0 ? next : null);
  }, []);
  const clearActorScopedState = useCallback(() => {
    resetActorScopedAppState({
      setApprovals,
      setDrawer,
      setGenieOpen,
      setLastBorrowerIdState,
      setSavedLeads,
      setSavedDrafts,
      setWorkspaceStatus,
      setWorkspaceError,
      setWorkspaceReloadToken,
    });
  }, []);
  const saveLead = useCallback((lead: SavedLeadInput) => {
    if (!lead.borrower_id) return;
    const now = new Date().toISOString();
    setLastBorrowerIdState(lead.borrower_id);
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
  }, []);
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
    setLastBorrowerIdState(draft.borrower_id);
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
  }, []);
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
      showEvidence, setShowEvidence,
      showConfidence, setShowConfidence,
      consoleOpen, setConsoleOpen,
      recentActivityFocusRequest, openConsoleRecentActivity, acknowledgeRecentActivityFocus,
      drawer, setDrawer,
      genieOpen, setGenieOpen,
      approvals, setApproval,
      lastBorrowerId: lastBorrowerIdState,
      setLastBorrowerId,
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
      showEvidence, showConfidence, consoleOpen, setConsoleOpen,
      recentActivityFocusRequest, openConsoleRecentActivity, acknowledgeRecentActivityFocus,
      drawer, genieOpen, approvals, setApproval,
      lastBorrowerIdState, setLastBorrowerId, clearActorScopedState,
      savedLeads, saveLead, removeSavedLead, isLeadSaved,
      savedDrafts, saveDraft, removeSavedDraft,
      workspaceStatus, workspaceError, refreshWorkspace,
    ]
  );

  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}

export function useApp(): AppCtxValue {
  const v = useContext(AppCtx);
  if (!v) throw new Error('useApp must be used inside <AppProvider>');
  return v;
}
