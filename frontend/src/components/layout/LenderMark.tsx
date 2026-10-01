import { useState } from 'react';
import { Icon } from '../Icon';
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
 * this is the building glyph. With it, the image shows only once the session
 * names the same lender the build validated the mark for: the caller passes
 * `sessionLender`, AppContext's tenant label once /api/session has answered
 * (sessionStatus 'ready'), else null. While loading, on a session error, on a
 * mismatch and after a failed image load it is the building glyph.
 *
 * It reads the session through the caller instead of its own session query,
 * and the Topbar loads it lazily, only on a co-branded build: both keep the
 * default build's first paint free of its code (W5b initial-JS cap; an own
 * query import reshuffled the shared chunks, +0.33 KiB br measured).
 */
export function LenderMark({ iconSize, sessionLender }: { iconSize: number; sessionLender: string | null }) {
  const [failed, setFailed] = useState(false);
  const url = lenderMarkUrl();
  const lender = sessionLender?.trim();
  if (!url || failed || !lender || lender !== lenderMarkLender()) return <Icon name="building" size={iconSize} />;
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
