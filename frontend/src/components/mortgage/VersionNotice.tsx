import { Icon } from '../Icon';
import { useOptionalHealth } from '../HealthProvider';

/**
 * VersionNotice — "A new version is available", with a Reload button.
 *
 * Audit 2026-09-21 (`bundle-01`): a deploy re-hashes most JS chunks, so a tab
 * left open across a deploy eventually asks for a chunk that no longer exists.
 * The health body already carries `git_sha`; HealthProvider remembers the
 * first one it sees and raises `updateAvailable` when a later poll reports a
 * different one. This notice is the non-blocking surface for that signal:
 * nothing is interrupted, no work is lost, and the user reloads when ready.
 *
 * Reuses the `.degraded-banner` block (same slot, same icon + title + sub
 * layout) with the `--info` modifier, so it reads as information rather than
 * an amber warning. Renders nothing until a new build is actually detected,
 * and nothing outside a HealthProvider.
 */

function reloadPage(): void {
  window.location.reload();
}

export function VersionNotice({ onReload = reloadPage }: { onReload?: () => void } = {}) {
  const updateAvailable = useOptionalHealth()?.updateAvailable ?? false;
  if (!updateAvailable) return null;

  return (
    <div className="degraded-banner degraded-banner--info" role="status" aria-live="polite">
      <div className="degraded-banner__ico" aria-hidden="true">
        <Icon name="info" size={16} />
      </div>
      <div className="degraded-banner__body">
        <div className="degraded-banner__title">A new version is available</div>
        <div className="degraded-banner__sub">
          Reload when you are ready. Anything you have not saved on this page will be lost.
        </div>
      </div>
      <div className="degraded-banner__actions">
        <button type="button" className="btn btn--ghost btn--sm" onClick={onReload}>
          Reload
        </button>
      </div>
    </div>
  );
}
