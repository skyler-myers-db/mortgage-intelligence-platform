import { useEffect, useId, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useApp, type ThemePreference } from '../AppContext';
import { Icon } from '../Icon';
import { api } from '../../lib/api';
import { pushEscapeLayer } from '../../lib/escapeStack';
import { preloadBestEffort } from '../../lib/lazyPreload';
import { createIdlePreloader } from '../../lib/prefetch';
import type { SessionResponse } from '../../types';
import IdentityMenuPanel, { type IdentityView } from './IdentityMenuPanel';

/**
 * The window event the "Keyboard shortcuts" item dispatches. The shortcut
 * overlay (queue keyboard lane) listens for it; until that lands the item is a
 * harmless no-op. A CustomEvent keeps the shell free of the overlay's code.
 */
export const OPEN_SHORTCUTS_EVENT = 'mip:open-shortcuts';

// The popup's stylesheet ships in its own chunk: the initial CSS gate has no
// room for it. It is warmed at idle and on trigger hover / focus, and the
// menu opens only once it has loaded, so the popup never paints unstyled. A
// lazy JS panel was measured first and rejected: splitting the component out
// made rolldown hoist ~48 KB of shared entry modules into a common chunk
// that still loads on every page but escapes the initial-JS gate.
let panelStyles: Promise<unknown> | null = null;
function loadPanelStyles(): Promise<unknown> {
  panelStyles ??= import('./IdentityMenuPanel.css').catch((error: unknown) => {
    panelStyles = null;
    throw error;
  });
  return panelStyles;
}
const preloadPanelAtIdle = createIdlePreloader(loadPanelStyles, 5000);

/** What the menu header says. Only the actor's OWN forwarded identity. */
export function identityView(session: SessionResponse | undefined, isError: boolean): IdentityView {
  if (!session) {
    return {
      name: isError ? 'Identity not verified' : 'Checking identity…',
      email: null,
      roles: [],
      status: isError ? 'error' : 'loading',
    };
  }
  const email = session.actor_email?.trim() || null;
  const name = session.actor_display_name?.trim() || email || 'No forwarded identity';
  return { name, email, roles: session.role_labels ?? [], status: 'ready' };
}

/**
 * Topbar identity menu (audit 2026-09-21 `shell-06`). A WAI-ARIA menu button:
 * the trigger names who is signed in; the popup (IdentityMenuPanel)
 * shows the actor's own name, email and role tiers from `/api/session`, then
 * appearance (theme radio items), Keyboard shortcuts and the Glossary.
 *
 * Keyboard (APG menu button): Enter / Space / ArrowDown open on the first
 * item, ArrowUp on the last; ArrowUp / ArrowDown / Home / End move; Escape
 * closes onto the trigger (shared Escape stack); Tab or a click outside
 * closes. Additive to the prototype topbar (Module 0 Prototype.html:1206-1243
 * has no account control); it reuses the `.topbar__icon-btn` trigger and the
 * `.filter-menu` dropdown primitives.
 *
 * The email is display-only: nothing here writes it anywhere, and RUM and
 * client-error telemetry never read the DOM.
 */
export function IdentityMenu() {
  const { themePreference, setThemePreference } = useApp();
  const session = useQuery<SessionResponse>({
    queryKey: ['session', 'access'],
    queryFn: ({ signal }) => api.session(signal),
    retry: false,
  });
  const view = identityView(session.data, session.isError);
  const [open, setOpen] = useState(false);
  const [initialIndex, setInitialIndex] = useState(0);
  const menuId = useId();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => preloadPanelAtIdle(), []);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const popEscapeLayer = pushEscapeLayer(() => {
      setOpen(false);
      triggerRef.current?.focus();
    });
    window.addEventListener('pointerdown', onPointerDown);
    return () => {
      window.removeEventListener('pointerdown', onPointerDown);
      popEscapeLayer();
    };
  }, [open]);

  const openAt = (index: number) => {
    const show = () => {
      setInitialIndex(index);
      setOpen(true);
    };
    // A failed stylesheet fetch still opens the (base-styled) menu.
    void loadPanelStyles().then(show, show);
  };

  const closeOntoTrigger = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const warm = () => preloadBestEffort(loadPanelStyles);

  const triggerLabel = view.status === 'ready' && view.email
    ? `Account menu, signed in as ${view.name}`
    : 'Account menu';

  return (
    <div
      ref={rootRef}
      className="identity-menu"
      onBlur={(event) => {
        // Tab (or anything else) moving focus out of the menu closes it.
        if (open && !rootRef.current?.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        className={`topbar__icon-btn identity-menu__trigger ${open ? 'is-active' : ''}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={triggerLabel}
        title={triggerLabel}
        onPointerEnter={warm}
        onFocus={warm}
        onClick={() => (open ? setOpen(false) : openAt(0))}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            openAt(event.key === 'ArrowDown' ? 0 : -1);
          }
        }}
        data-testid="identity-menu-trigger"
      >
        <Icon name="user" size={15} />
      </button>
      {open && (
          <IdentityMenuPanel
            menuId={menuId}
            view={view}
            initialIndex={initialIndex}
            themePreference={themePreference}
            onTheme={(value: ThemePreference) => {
              setThemePreference(value);
              closeOntoTrigger();
            }}
            onShortcuts={() => {
              closeOntoTrigger();
              // After focus is back on the trigger, so the overlay can take it.
              window.dispatchEvent(new CustomEvent(OPEN_SHORTCUTS_EVENT));
            }}
            onNavigate={() => setOpen(false)}
          />
      )}
    </div>
  );
}
