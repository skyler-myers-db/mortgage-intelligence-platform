/**
 * Instant shell (audit bundle-10): index.html used to ship an empty
 * `<div id="root">`, so nothing painted until the ~105 KiB br entry chunk
 * executed. This `transformIndexHtml` hook (vite.config.ts; dev and build)
 * puts a static skeleton of the app shell inside #root: the rail, the topbar
 * and the main column with an empty route-nav band and RouteFallback's
 * text-free hero. It paints with the stylesheet, and createRoot replaces it on
 * the first commit.
 *
 * Rules (pinned by shellSkeleton.test.tsx against the real AppShell and
 * RouteFallback markup):
 *   - only existing prototype / app classes: `.app-shell` > `.rail`,
 *     `.topbar`, `.main`, the `.route-nav` band as `.main`'s first child,
 *     and `.main__content > .main__inner > .proto-hero` with
 *     `.skeleton--eyebrow/--title/--lede`. No new CSS (initial CSS +0), no
 *     style attributes;
 *   - only divs, no landmarks, no roles, no ids and no text nodes, and the
 *     whole skeleton is aria-hidden, so role and label locators (and screen
 *     readers) never meet it;
 *   - the Console gutter comes free: theme-boot.js sets data-console on
 *     <html> before paint, and `[data-console="open"] .main` pads the column
 *     (01-app-shell.css). Theme, accent and density come the same way.
 *
 * Declared departure: the prototype (design_files/Module 0 Prototype.html)
 * has no pre-JS state; this is the shell's own markup, drawn early.
 */

const div = (className: string, children = '') => `<div class="${className}">${children}</div>`;

/** The skeleton inside #root. One line, no whitespace: no text nodes. */
export const SHELL_SKELETON_HTML = `<div class="app-shell" aria-hidden="true">${
  div('rail')
}${
  div('topbar')
}${
  div(
    'main',
    div('route-nav')
      + div(
        'main__content',
        div(
          'main__inner',
          div(
            'proto-hero',
            `<div>${div('skeleton skeleton--eyebrow')}${div('skeleton skeleton--title')}${div('skeleton skeleton--lede')}</div>`,
          ),
        ),
      ),
  )
}</div>`;

const EMPTY_ROOT = '<div id="root"></div>';

/** index.html with the skeleton inside #root; throws unless the empty root occurs once. */
export function injectShellSkeleton(html: string): string {
  const at = html.indexOf(EMPTY_ROOT);
  if (at === -1 || html.indexOf(EMPTY_ROOT, at + 1) !== -1) {
    throw new Error(`expected ${EMPTY_ROOT} exactly once in index.html`);
  }
  return `${html.slice(0, at)}<div id="root">${SHELL_SKELETON_HTML}</div>${html.slice(at + EMPTY_ROOT.length)}`;
}

/** Typed locally: `vite` types would pull Node's globals into the app typecheck. */
export interface ShellSkeletonPlugin {
  name: string;
  transformIndexHtml: (html: string) => string;
}

export function shellSkeleton(): ShellSkeletonPlugin {
  return {
    name: 'mip:shell-skeleton',
    transformIndexHtml: injectShellSkeleton,
  };
}
