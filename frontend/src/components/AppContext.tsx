import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from 'react';

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

const THEMES: readonly Theme[] = ['dark', 'light'];
const ACCENTS: readonly Accent[] = ['bright', 'teal', 'navy', 'red'];
const DENSITIES: readonly Density[] = ['comfortable', 'compact'];

export function AppProvider({ children }: PropsWithChildren) {
  const [theme, setThemeState] = useState<Theme>(() => readStored('mip.theme', 'dark', THEMES));
  const [accent, setAccentState] = useState<Accent>(() => readStored('mip.accent', 'bright', ACCENTS));
  const [density, setDensityState] = useState<Density>(() => readStored('mip.density', 'comfortable', DENSITIES));
  const [lender, setLender] = useState<string>('Summit Mortgage');
  const [showEvidence, setShowEvidence] = useState(true);
  const [showConfidence, setShowConfidence] = useState(true);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [drawer, setDrawer] = useState<DrawerSource | null>(null);
  const [genieOpen, setGenieOpen] = useState(false);
  const [approvals, setApprovals] = useState<Record<string, 'approved' | 'rejected'>>({});

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

  const setTheme = useCallback((t: Theme) => setThemeState(t), []);
  const setAccent = useCallback((a: Accent) => setAccentState(a), []);
  const setDensity = useCallback((d: Density) => setDensityState(d), []);
  const setApproval = useCallback((borrowerId: string, state: 'approved' | 'rejected') => {
    setApprovals((cur) => ({ ...cur, [borrowerId]: state }));
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
    }),
    [
      theme, setTheme, accent, setAccent, density, setDensity,
      lender, showEvidence, showConfidence, consoleOpen, drawer,
      genieOpen, approvals, setApproval,
    ]
  );

  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}

export function useApp(): AppCtxValue {
  const v = useContext(AppCtx);
  if (!v) throw new Error('useApp must be used inside <AppProvider>');
  return v;
}
