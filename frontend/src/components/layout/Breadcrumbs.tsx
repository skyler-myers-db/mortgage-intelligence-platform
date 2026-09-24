import { Link, useLocation } from 'react-router';
import { breadcrumbTrail } from '../../lib/breadcrumbs';
import { useQueueContext } from '../../lib/queueContext';
import { PRODUCT_NAME, maskedBorrowerIdFor } from '../../lib/routeMeta';

/**
 * Topbar breadcrumbs (audit 2026-09-21 `shell-04`) in the prototype's
 * `.topbar__crumbs` BEM (Module 0 Prototype.html:1205-1219 passes a crumbs
 * array; `.sep` between, `.cur` on the last). The trail is a
 * `nav[aria-label=Breadcrumb]` list: ancestors are links, the current page
 * carries `aria-current="page"`. Top-level pages keep the product root
 * ("Mortgage Intelligence Platform / Lead Queue"); a detail trail drops it so
 * the queue label and the masked id fit the topbar's side track.
 */
export function Breadcrumbs() {
  const location = useLocation();
  const borrowerId = maskedBorrowerIdFor(location.pathname);
  const queue = useQueueContext(location.state, borrowerId);
  const trail = breadcrumbTrail(location.pathname, queue);
  const last = trail.length - 1;

  return (
    <nav className="topbar__crumbs" aria-label="Breadcrumb">
      {trail.length === 1 && (
        <>
          <span className="topbar__crumbs-root" aria-hidden="true">{PRODUCT_NAME}</span>
          <span className="sep" aria-hidden="true">/</span>
        </>
      )}
      <ol className="topbar__crumbs-list">
        {trail.map((crumb, index) => (
          <li key={`${index}:${crumb.label}`} className="topbar__crumb">
            {index > 0 && <span className="sep" aria-hidden="true">/</span>}
            {crumb.to && index < last ? (
              <Link
                to={crumb.to}
                state={crumb.state}
                className={`topbar__crumb-link${crumb.mono ? ' mono' : ''}`}
              >
                {crumb.label}
              </Link>
            ) : (
              <span className={`cur${crumb.mono ? ' mono' : ''}`} aria-current="page">
                {crumb.label}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
