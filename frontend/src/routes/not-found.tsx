import { Link, useLocation } from 'react-router';
import { PageShell } from '../components/layout/PageShell';
import { Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';

export default function NotFoundRoute() {
  const { pathname } = useLocation();

  return (
    <PageShell
      eyebrow="Not found"
      title="Page not found"
      lede="This workspace route does not exist. Use the product navigation or return to the lead workflow."
      heroRight={(
        <Chip variant="warning" icon="info">
          404
        </Chip>
      )}
    >
      <section className="surface not-found">
        <div className="surface__body not-found__body">
          <div className="not-found__icon" aria-hidden="true">
            <Icon name="info" size={18} />
          </div>
          <div className="not-found__copy">
            <div className="h-3">Unknown route</div>
            <p className="muted">
              <span className="mono">{pathname}</span> is not part of Module 0.
            </p>
          </div>
          <div className="section-actions not-found__actions">
            <Link to="/lead-queue" className="btn btn--primary">
              Open lead queue
              <Icon name="chevright" size={14} />
            </Link>
            <Link to="/" className="btn btn--ghost">
              <Icon name="home" size={14} />
              Go home
            </Link>
          </div>
        </div>
      </section>
    </PageShell>
  );
}
