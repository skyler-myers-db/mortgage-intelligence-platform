/**
 * Deploy-time tenant appearance (audit responsive-10, report 12.4 #9 white-
 * label, D-theme-nav-d; deviation:tenant-default-appearance and
 * deviation:lender-mark).
 *
 * scripts/deploy.sh exports MIP_DEFAULT_THEME, MIP_DEFAULT_ACCENT, the
 * preflight-validated MIP_LENDER_MARK_SHA256 and MIP_LENDER_NAME, and stages
 * the validated mark in frontend/.branding-stage; vite.config.ts reads them
 * and passes them here. The build then:
 *   - puts `<meta>` defaults before the theme-boot.js tag, so the first paint
 *     already wears the tenant theme / accent for a user who has not chosen
 *     (theme-boot.js and lib/themePreference.ts read them); the product
 *     defaults (dark, bright) emit nothing, so a default build is unchanged;
 *   - with a validated mark, emits it as `branding/lender-mark.<ext>` (a
 *     same-origin dist file, so CSP img-src 'self' covers it) and adds the
 *     mark URL (cache-busted by its hash) and the lender it was built for.
 *     A mark without a matching hash is never emitted.
 * Only index.html is transformed; bootModulePlugin derives index.home.html
 * from it afterwards. Validation runs in buildStart, not in the factory:
 * vitest and `vite dev` load vite.config.ts too, and a stale shell export
 * must not break them, while a build with bad input still fails.
 */

// Copies of lib/themePreference.ts's values, pinned equal by
// tenantAppearancePlugin.test.ts: this module has no relative import because
// vite.config.ts loads it, and Vite's native config loader (planned default)
// refuses extensionless imports, while the app tsconfig refuses `.ts` ones.
export const THEME_PREFERENCES = ['dark', 'light', 'system'] as const;
export const ACCENTS = ['bright', 'teal', 'navy', 'red'] as const;
export const DEFAULT_THEME_PREFERENCE = 'dark';
export const DEFAULT_ACCENT = 'bright';
export const TENANT_THEME_META = 'mip-default-theme';
export const TENANT_ACCENT_META = 'mip-default-accent';
export const LENDER_MARK_META = 'mip-lender-mark';
export const LENDER_MARK_LENDER_META = 'mip-lender-mark-lender';

export interface StagedLenderMark {
  ext: 'png' | 'webp';
  bytes: Uint8Array;
  sha256: string;
}

export interface TenantAppearanceOptions {
  theme?: string;
  accent?: string;
  markSha256?: string;
  lenderName?: string;
  stagedMark: StagedLenderMark | null;
}

/** The slice of Vite's plugin API used here, typed locally (see bootModulePlugin.ts). */
interface EmitContext {
  emitFile(file: { type: 'asset'; fileName: string; source: string | Uint8Array }): string;
}

export interface TenantAppearancePlugin {
  name: string;
  apply: 'build';
  buildStart(): void;
  transformIndexHtml(html: string, ctx: { filename?: string; path?: string }): string;
  generateBundle(this: EmitContext): void;
}

/** The tag every meta goes in front of, so it is in the DOM before theme-boot runs. */
export const THEME_BOOT_ANCHOR = '<script src="/theme-boot.js';
export const LENDER_MARK_DIR = 'branding';

const SHA256_RE = /^[0-9a-f]{64}$/;

interface Resolved {
  theme: string;
  accent: string;
  mark: { fileName: string; url: string; lender: string; bytes: Uint8Array } | null;
}

const escapeAttribute = (value: string) =>
  value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** Validates the options; throws on anything a build must not ship. */
export function resolveTenantAppearance(options: TenantAppearanceOptions): Resolved {
  const theme = options.theme?.trim() || DEFAULT_THEME_PREFERENCE;
  const accent = options.accent?.trim() || DEFAULT_ACCENT;
  if (!(THEME_PREFERENCES as readonly string[]).includes(theme)) {
    throw new Error(`mip:tenant-appearance: MIP_DEFAULT_THEME must be one of ${THEME_PREFERENCES.join(', ')}`);
  }
  if (!(ACCENTS as readonly string[]).includes(accent)) {
    throw new Error(`mip:tenant-appearance: MIP_DEFAULT_ACCENT must be one of ${ACCENTS.join(', ')}`);
  }
  const sha = options.markSha256?.trim() ?? '';
  if (!sha) return { theme, accent, mark: null };
  if (!SHA256_RE.test(sha)) throw new Error('mip:tenant-appearance: MIP_LENDER_MARK_SHA256 is not 64 lowercase hex');
  const staged = options.stagedMark;
  if (!staged || staged.sha256 !== sha) {
    throw new Error('mip:tenant-appearance: the staged lender mark does not match MIP_LENDER_MARK_SHA256');
  }
  const lender = options.lenderName?.trim() ?? '';
  if (!lender) throw new Error('mip:tenant-appearance: a lender mark needs MIP_LENDER_NAME');
  const fileName = `${LENDER_MARK_DIR}/lender-mark.${staged.ext}`;
  return { theme, accent, mark: { fileName, url: `/${fileName}?v=${sha.slice(0, 8)}`, lender, bytes: staged.bytes } };
}

/** The meta lines for one resolved appearance; empty at the product defaults. */
export function tenantMetaTags(resolved: Resolved): string[] {
  const tags: string[] = [];
  if (resolved.theme !== DEFAULT_THEME_PREFERENCE) tags.push(`<meta name="${TENANT_THEME_META}" content="${resolved.theme}">`);
  if (resolved.accent !== DEFAULT_ACCENT) tags.push(`<meta name="${TENANT_ACCENT_META}" content="${resolved.accent}">`);
  if (resolved.mark) {
    tags.push(`<meta name="${LENDER_MARK_META}" content="${resolved.mark.url}">`);
    tags.push(`<meta name="${LENDER_MARK_LENDER_META}" content="${escapeAttribute(resolved.mark.lender)}">`);
  }
  return tags;
}

/** index.html with each tag on its own line right before the theme-boot tag; throws without the anchor. */
export function insertTenantMetas(html: string, tags: readonly string[]): string {
  const at = html.indexOf(THEME_BOOT_ANCHOR);
  if (at === -1) throw new Error(`mip:tenant-appearance: index.html has no ${THEME_BOOT_ANCHOR} tag`);
  if (tags.length === 0) return html;
  const lineStart = html.lastIndexOf('\n', at) + 1;
  const indent = html.slice(lineStart, at);
  return `${html.slice(0, lineStart)}${tags.map((tag) => `${indent}${tag}\n`).join('')}${html.slice(lineStart)}`;
}

export function tenantAppearancePlugin(options: TenantAppearanceOptions): TenantAppearancePlugin {
  let resolved: Resolved | null = null;
  const current = (): Resolved => {
    resolved ??= resolveTenantAppearance(options);
    return resolved;
  };
  return {
    name: 'mip:tenant-appearance',
    apply: 'build',
    buildStart() {
      resolved = resolveTenantAppearance(options);
    },
    transformIndexHtml(html, ctx) {
      const file = (ctx.filename ?? ctx.path ?? '').replace(/\\/g, '/');
      if (!/(^|\/)index\.html$/.test(file)) return html;
      return insertTenantMetas(html, tenantMetaTags(current()));
    },
    generateBundle() {
      const { mark } = current();
      if (mark) this.emitFile({ type: 'asset', fileName: mark.fileName, source: mark.bytes });
    },
  };
}
