import { NavLink } from 'react-router-dom';
import { Icon, type IconName } from '../Icon';
import { useApp } from '../AppContext';
import { preloadRouteForPath } from '../../lib/routePreloaders';

/**
 * Secondary route nav — chip strip matching the prototype's `.filter` /
 * `.layout-tabs` styling. The prototype is a single screen; our app splits
 * Module 0 across eight routes for the linear user flow (portfolio → segments →
 * leads → borrower → offer → …). This sub-nav is the only deviation from the
 * prototype's composition, and uses prototype-native chip styling so it still
 * reads as part of the same design system.
 */

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
}

const BASE_ITEMS: NavItem[] = [
  { to: '/',                      label: 'Home',            icon: 'home' },
  { to: '/analytics',             label: 'Analytics',       icon: 'flow' },
  { to: '/portfolio-builder',     label: 'Portfolio',       icon: 'target' },
  { to: '/segment-intelligence',  label: 'Segments',        icon: 'layers' },
  { to: '/lead-queue',            label: 'Leads',           icon: 'user' },
  { to: '/ask-genie',             label: 'Ask Genie',       icon: 'sparkle' },
  { to: '/glossary',              label: 'Glossary',        icon: 'info' },
  { to: '/admin-config',          label: 'Admin',           icon: 'settings' },
];

export function RouteNav() {
  const { canAccessAdmin, lastBorrowerId } = useApp();
  const detailItems: NavItem[] = [
    {
      to: lastBorrowerId ? `/borrower-360/${lastBorrowerId}` : '/borrower-360',
      label: 'Borrower 360',
      icon: 'doc',
    },
    {
      to: lastBorrowerId ? `/offer-orchestrator/${lastBorrowerId}` : '/offer-orchestrator',
      label: 'Offer',
      icon: 'bolt',
    },
  ];
  const baseItems = canAccessAdmin
    ? BASE_ITEMS
    : BASE_ITEMS.filter((item) => item.to !== '/admin-config');
  const items = [
    ...baseItems.slice(0, 5),
    ...detailItems,
    ...baseItems.slice(5),
  ];
  return (
    <nav aria-label="Main navigation" className="route-nav">
      {items.map((i) => {
        const end = i.to === '/';
        return (
          <NavLink
            key={i.to}
            to={i.to}
            end={end}
            onMouseEnter={() => preloadRouteForPath(i.to)}
            onFocus={() => preloadRouteForPath(i.to)}
            className={({ isActive }) => `filter ${isActive ? 'is-active' : ''}`}
          >
            <Icon name={i.icon} size={12} />
            <span className="filter__value">{i.label}</span>
          </NavLink>
        );
      })}
    </nav>
  );
}
