import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Icon } from '../Icon';
import { sessionQueryOptions } from '../../lib/sessionQuery';
import { lenderMarkLender, lenderMarkUrl } from '../../lib/themePreference';

/**
 * The reviewed lender mark beside the tenant name (audit responsive-10,
 * report 12.4 #9; deviation:lender-mark): the topbar tenant pill and the
 * Console 'Configured tenant' chip only. Never on a borrower-facing, draft,
 * preview, evidence, export, email, copy or print surface (print.css hides
 * the topbar and the Console), and AppContext never exposes it.
 *
 * A build carries a mark only when deploy preflight validated it against the
 * source-controlled registry (lib/tenantAppearancePlugin). Without the meta
 * this is the building glyph and subscribes to nothing, so the default build
 * (and every Topbar unit suite, which mounts without a QueryClientProvider)
 * is unchanged. With it, the image shows only once /api/session names the
 * same lender the build validated the mark for; while loading, on an error,
 * on a mismatch and after a failed image load it is the building glyph.
 */
export function LenderMark({ iconSize }: { iconSize: number }) {
  const url = lenderMarkUrl();
  if (!url) return <Icon name="building" size={iconSize} />;
  return <SessionBoundLenderMark url={url} iconSize={iconSize} />;
}

function SessionBoundLenderMark({ url, iconSize }: { url: string; iconSize: number }) {
  const session = useQuery(sessionQueryOptions());
  const [failed, setFailed] = useState(false);
  const lender = session.data?.lender_name?.trim();
  if (failed || !lender || lender !== lenderMarkLender()) return <Icon name="building" size={iconSize} />;
  return (
    <img
      className="lender-mark"
      src={url}
      alt=""
      width={16}
      height={16}
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}
