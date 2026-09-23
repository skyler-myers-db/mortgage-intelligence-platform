import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { Link } from 'react-router';
import type { ThemePreference } from '../AppContext';
import { Icon } from '../Icon';
import { ROUTES } from '../../lib/routeMeta';

/**
 * The identity menu's popup (audit 2026-09-21 shell-06). Its stylesheet,
 * IdentityMenuPanel.css, is loaded by IdentityMenu on demand (at idle, on
 * trigger hover / focus, and before the first open), so the initial
 * stylesheet carries only the topbar trigger.
 *
 * `role="menu"` owns: an Appearance group of `menuitemradio` theme choices,
 * a separator, then `menuitem`s for Keyboard shortcuts and the Glossary. The
 * identity header sits outside the menu (a menu may own only items, groups
 * and separators) and describes it.
 */

const THEME_ITEMS: ReadonlyArray<{ value: ThemePreference; label: string }> = [
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
  { value: 'system', label: 'System' },
];

type MenuItem =
  | { kind: 'theme'; value: ThemePreference; label: string }
  | { kind: 'shortcuts'; label: string }
  | { kind: 'glossary'; label: string };

export const MENU_ITEMS: readonly MenuItem[] = [
  ...THEME_ITEMS.map((item) => ({ kind: 'theme' as const, ...item })),
  { kind: 'shortcuts', label: 'Keyboard shortcuts' },
  { kind: 'glossary', label: 'Glossary' },
];

export interface IdentityView {
  name: string;
  email: string | null;
  roles: readonly string[];
  status: 'loading' | 'ready' | 'error';
}

export interface IdentityMenuPanelProps {
  menuId: string;
  view: IdentityView;
  /** Item that takes focus when the menu opens (0 = first, -1 = last). */
  initialIndex: number;
  themePreference: ThemePreference;
  onTheme: (value: ThemePreference) => void;
  onShortcuts: () => void;
  /** The Glossary link was followed; close without refocusing the trigger. */
  onNavigate: () => void;
}

export default function IdentityMenuPanel({
  menuId,
  view,
  initialIndex,
  themePreference,
  onTheme,
  onShortcuts,
  onNavigate,
}: IdentityMenuPanelProps) {
  const count = MENU_ITEMS.length;
  const [cursor, setCursor] = useState(() => (initialIndex < 0 ? count - 1 : initialIndex));
  const itemRefs = useRef<Array<HTMLElement | null>>([]);
  const headingId = `${menuId}-who`;

  useEffect(() => {
    itemRefs.current[cursor]?.focus({ preventScroll: true });
  }, [cursor]);

  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setCursor((index) => (event.key === 'ArrowDown' ? (index + 1) % count : (index - 1 + count) % count));
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      setCursor(event.key === 'Home' ? 0 : count - 1);
    } else if (event.key === ' ' && MENU_ITEMS[cursor]?.kind === 'glossary') {
      // Space activates a menuitem; a link only follows Enter natively.
      event.preventDefault();
      itemRefs.current[cursor]?.click();
    }
  };

  const itemProps = (index: number, extraClass = '') => ({
    ref: (el: HTMLElement | null) => {
      itemRefs.current[index] = el;
    },
    tabIndex: index === cursor ? 0 : -1,
    className: `filter-menu__item${extraClass}${index === cursor ? ' is-focused' : ''}`,
    onMouseEnter: () => setCursor(index),
  });

  return (
    <div className="filter-menu identity-menu__panel">
      <div className="identity-menu__who" id={headingId}>
        <span className="identity-menu__name">{view.name}</span>
        {view.email && <span className="identity-menu__email">{view.email}</span>}
        {view.roles.length > 0 && (
          <span className="identity-menu__roles">
            {view.roles.map((role) => (
              <span key={role} className="chip chip--neutral chip--compact">{role}</span>
            ))}
          </span>
        )}
      </div>
      <div
        id={menuId}
        role="menu"
        aria-label="Account"
        aria-describedby={headingId}
        className="identity-menu__list"
        onKeyDown={onMenuKeyDown}
      >
        <div role="group" aria-label="Appearance" className="identity-menu__group">
          <span className="identity-menu__group-label" aria-hidden="true">Appearance</span>
          {MENU_ITEMS.map((item, index) => {
            if (item.kind !== 'theme') return null;
            const checked = themePreference === item.value;
            return (
              <button
                key={item.value}
                type="button"
                role="menuitemradio"
                aria-checked={checked}
                {...itemProps(index, checked ? ' is-selected' : '')}
                onClick={() => onTheme(item.value)}
              >
                {item.label}
                {checked && <Icon name="check" size={12} />}
              </button>
            );
          })}
        </div>
        <div role="separator" className="identity-menu__sep" />
        {MENU_ITEMS.map((item, index) => {
          if (item.kind === 'theme') return null;
          if (item.kind === 'glossary') {
            return (
              <Link key={item.kind} to={ROUTES.glossary.pattern} role="menuitem" {...itemProps(index)} onClick={onNavigate}>
                {item.label}
                <Icon name={ROUTES.glossary.icon} size={12} />
              </Link>
            );
          }
          return (
            <button key={item.kind} type="button" role="menuitem" {...itemProps(index)} onClick={onShortcuts}>
              {item.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
