import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const session = vi.hoisted(() => ({ canAccessAdmin: false }));

vi.mock('../AppContext', () => ({
  useApp: () => ({
    canAccessAdmin: session.canAccessAdmin,
    lastBorrowerId: null,
  }),
}));

import { Rail } from './Rail';
import { RouteNav } from './RouteNav';

function renderNavigation(): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={['/']}>
      <Rail />
      <RouteNav />
    </MemoryRouter>,
  );
}

describe('role-aware navigation', () => {
  beforeEach(() => {
    session.canAccessAdmin = false;
  });

  it('fails closed in both navs while session access is loading, denied, or unavailable', () => {
    const html = renderNavigation();

    expect(html).not.toContain('href="/admin-config"');
    expect(html).not.toContain('Admin / settings');
    expect(html).not.toContain('>Admin<');
  });

  it('shows the Admin destination in both navs only after an affirmative session response', () => {
    session.canAccessAdmin = true;
    const html = renderNavigation();

    expect(html.match(/href="\/admin-config"/g)).toHaveLength(2);
    expect(html).toContain('Admin / settings');
    expect(html).toContain('>Admin<');
  });
});
