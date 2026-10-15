import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { preloadIntentProps } from '../../lib/preloadIntent';

const BORROWER_PATH = '/borrower-360/';

/**
 * A Genie table cell's drill-down link (2026-09-21 audit bundle-09 item 1).
 *
 * A masked borrower id opens Borrower 360; hovering or focusing it warms
 * that route's CHUNK (preloadIntentProps: chunks only, never a query, and
 * nothing under Save-Data), so the click does not wait on a cold download.
 * The dossier read itself writes a VIEW_BORROWER audit row, so it stays
 * click-only. Other cell links (a state's Lead Queue) render unchanged.
 */
export function GenieCellLink({ href, children }: { href: string; children: ReactNode }) {
  const intent = href.startsWith(BORROWER_PATH) ? preloadIntentProps(href) : null;
  return (
    <Link className="mono" to={href} {...intent}>
      {children}
    </Link>
  );
}
