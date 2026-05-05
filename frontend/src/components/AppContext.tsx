import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from 'react';
import { api } from '../lib/api';
import type { SavedDraft, SavedDraftInput, SavedLead, SavedLeadInput } from '../types';

/**
 * AppContext — theme, accent, density, lender, drawer, Genie, approvals,
 * evidence toggles. Ported from the Module 0 prototype so every page shares
 * one provider and writes data-theme/data-accent/data-density to <html>.
 */

export type Theme = 'dark' | 'light';
export type Accent = 'bright' | 'teal' | 'navy' | 'red';
export type Density = 'comfortable' | 'compact';

export interface DrawerSource {
  title: string;
  description?: string;
  lineage?: Array<{ layer: string; name: string; meta?: string }>;
  signals?: Array<{ label: string; source: string; value: string }>;
  updatedAt?: string;
  short?: string;
}

interface AppCtxValue {
  theme: Theme;
  setTheme: (t: Theme) => void;
  accent: Accent;
  setAccent: (a: Accent) => void;
  density: Density;
  setDensity: (d: Density) => void;
  lender: string;
  setLender: (s: string) => void;
  showEvidence: boolean;
  setShowEvidence: (v: boolean) => void;
  showConfidence: boolean;
  setShowConfidence: (v: boolean) => void;
  consoleOpen: boolean;
  setConsoleOpen: (v: boolean) => void;
  drawer: DrawerSource | null;
  setDrawer: (d: DrawerSource | null) => void;
  genieOpen: boolean;
  setGenieOpen: (v: boolean) => void;
  approvals: Record<string, 'approved' | 'rejected'>;
  setApproval: (borrowerId: string, state: 'approved' | 'rejected') => void;
  lastBorrowerId: string | null;
  setLastBorrowerId: (borrowerId: string | null) => void;
  savedLeads: Record<string, SavedLead>;
  saveLead: (lead: SavedLeadInput) => void;
  removeSavedLead: (borrowerId: string) => void;
  isLeadSaved: (borrowerId: string) => boolean;
  savedDrafts: Record<string, SavedDraft>;
  saveDraft: (draft: SavedDraftInput) => void;
  removeSavedDraft: (borrowerId: string) => void;
  workspaceStatus: 'loading' | 'ready' | 'error';
  workspaceError: string | null;
  refreshWorkspace: () => void;
}

const AppCtx = createContext<AppCtxValue | null>(null);

function readStored<T extends string>(key: string, fallback: T, allowed: readonly T[]): T {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw && (allowed as readonly string[]).includes(raw)) return raw as T;
  } catch {
    // ignore (SSR / private mode)
  }
  return fallback;
}

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

function readStoredString(key: string): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(key);
    return raw && raw.trim().length > 0 ? raw : null;
  } catch {
    return null;
  }
}

const THEMES: readonly Theme[] = ['dark', 'light'];
const ACCENTS: readonly Accent[] = ['bright', 'teal', 'navy', 'red'];
const DENSITIES: readonly Density[] = ['comfortable', 'compact'];

function mapByBorrower<T extends { borrower_id: string }>(items: T[]): Record<string, T> {
  return Object.fromEntries(items.map((item) => [item.borrower_id, item]));
}

export function AppProvider({ children }: PropsWithChildren) {
  const [theme, setThemeState] = useState<Theme>(() => readStored('mip.theme', 'dark', THEMES));
  const [accent, setAccentState] = useState<Accent>(() => readStored('mip.accent', 'bright', ACCENTS));
  const [density, setDensityState] = useState<Density>(() => readStored('mip.density', 'comfortable', DENSITIES));
  const [lender, setLender] = useState<string>('Summit Mortgage');
  const [showEvidence, setShowEvidence] = useState(true);
  const [showConfidence, setShowConfidence] = useState(true);
  // Console is opt-in so the first demo viewport uses the full prototype
  // layout. Presenter preference persists across reloads via localStorage
  // (same pattern as theme/accent/density).
  const [consoleOpen, setConsoleOpenState] = useState<boolean>(() =>
    readStoredBool('mip.consoleOpen', false),
  );
  const [drawer, setDrawer] = useState<DrawerSource | null>(null);
  const [genieOpen, setGenieOpen] = useState(false);
  const [approvals, setApprovals] = useState<Record<string, 'approved' | 'rejected'>>({});
  const [lastBorrowerIdState, setLastBorrowerIdState] = useState<string | null>(() =>
    readStoredString('mip.lastBorrowerId'),
  );
  const [savedLeads, setSavedLeads] = useState<Record<string, SavedLead>>({});
  const [savedDrafts, setSavedDrafts] = useState<Record<string, SavedDraft>>({});
  const [workspaceStatus, setWorkspaceStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [workspaceReloadToken, setWorkspaceReloadToken] = useState(0);

  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute('data-theme', theme);
    root.setAttribute('data-accent', accent);
    root.setAttribute('data-density', density);
    try {
      window.localStorage.setItem('mip.theme', theme);
      window.localStorage.setItem('mip.accent', accent);
      window.localStorage.setItem('mip.density', density);
    } catch {
      // ignore
    }
  }, [theme, accent, density]);

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
    try {
      if (lastBorrowerIdState) {
        window.localStorage.setItem('mip.lastBorrowerId', lastBorrowerIdState);
      } else {
        window.localStorage.removeItem('mip.lastBorrowerId');
      }
    } catch {
      // ignore
    }
  }, [lastBorrowerIdState]);

  useEffect(() => {
    const ctrl = new AbortController();
    setWorkspaceStatus((cur) => (cur === 'ready' ? cur : 'loading'));
    void api.workspace(ctrl.signal)
      .then((workspace) => {
        setSavedLeads(mapByBorrower(workspace.saved_leads));
        setSavedDrafts(mapByBorrower(workspace.saved_drafts));
        setWorkspaceStatus('ready');
        setWorkspaceError(null);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === 'AbortError') return;
        setWorkspaceStatus('error');
        setWorkspaceError(
          err instanceof Error
            ? `Couldn't load saved workspace: ${err.message}`
            : "Couldn't load saved workspace.",
        );
      });
    return () => ctrl.abort();
  }, [workspaceReloadToken]);

  const setTheme = useCallback((t: Theme) => setThemeState(t), []);
  const setAccent = useCallback((a: Accent) => setAccentState(a), []);
  const setDensity = useCallback((d: Density) => setDensityState(d), []);
  const setConsoleOpen = useCallback((v: boolean) => setConsoleOpenState(v), []);
  const setApproval = useCallback((borrowerId: string, state: 'approved' | 'rejected') => {
    setApprovals((cur) => ({ ...cur, [borrowerId]: state }));
  }, []);
  const setLastBorrowerId = useCallback((borrowerId: string | null) => {
    const next = borrowerId?.trim();
    setLastBorrowerIdState(next && next.length > 0 ? next : null);
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
  const saveDraft = useCallback((draft: SavedDraftInput) => {
    if (!draft.borrower_id || draft.body.trim().length === 0) return;
    const now = new Date().toISOString();
    setLastBorrowerIdState(draft.borrower_id);
    let prior: SavedDraft | undefined;
    setSavedDrafts((cur) => {
      prior = cur[draft.borrower_id];
      return {
        ...cur,
        [draft.borrower_id]: {
          ...prior,
          ...draft,
          channel: draft.channel,
          saved_at: prior?.saved_at ?? now,
          updated_at: now,
        },
      };
    });
    void api.saveWorkspaceDraft(draft)
      .then((saved) => {
        setSavedDrafts((cur) => ({ ...cur, [saved.borrower_id]: saved }));
        setWorkspaceStatus('ready');
        setWorkspaceError(null);
      })
      .catch((err: unknown) => {
        setSavedDrafts((cur) => {
          const { [draft.borrower_id]: _discard, ...rest } = cur;
          return prior ? { ...rest, [draft.borrower_id]: prior } : rest;
        });
        setWorkspaceStatus('error');
        setWorkspaceError(
          err instanceof Error
            ? `Couldn't save draft: ${err.message}`
            : "Couldn't save draft.",
        );
      });
  }, []);
  const removeSavedDraft = useCallback((borrowerId: string) => {
    let prior: SavedDraft | undefined;
    setSavedDrafts((cur) => {
      prior = cur[borrowerId];
      const { [borrowerId]: _discard, ...rest } = cur;
      return rest;
    });
    void api.deleteWorkspaceDraft(borrowerId)
      .then(() => {
        setWorkspaceStatus('ready');
        setWorkspaceError(null);
      })
      .catch((err: unknown) => {
        if (prior) setSavedDrafts((cur) => ({ ...cur, [borrowerId]: prior as SavedDraft }));
        setWorkspaceStatus('error');
        setWorkspaceError(
          err instanceof Error
            ? `Couldn't remove draft: ${err.message}`
            : "Couldn't remove draft.",
        );
      });
  }, []);
  const refreshWorkspace = useCallback(() => {
    setWorkspaceReloadToken((n) => n + 1);
  }, []);

  const value = useMemo<AppCtxValue>(
    () => ({
      theme, setTheme,
      accent, setAccent,
      density, setDensity,
      lender, setLender,
      showEvidence, setShowEvidence,
      showConfidence, setShowConfidence,
      consoleOpen, setConsoleOpen,
      drawer, setDrawer,
      genieOpen, setGenieOpen,
      approvals, setApproval,
      lastBorrowerId: lastBorrowerIdState,
      setLastBorrowerId,
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
      theme, setTheme, accent, setAccent, density, setDensity,
      lender, showEvidence, showConfidence, consoleOpen, setConsoleOpen,
      drawer, genieOpen, approvals, setApproval,
      lastBorrowerIdState, setLastBorrowerId,
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
