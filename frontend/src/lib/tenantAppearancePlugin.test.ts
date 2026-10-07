/**
 * lib/tenantAppearancePlugin (audit responsive-10, 12.4 #9): the deploy-time
 * tenant theme / accent defaults and the reviewed lender mark, as the build
 * writes them into index.html and dist/branding.
 */
import { describe, expect, it } from 'vitest';
import * as plugin from './tenantAppearancePlugin';
import { tenantAppearancePlugin, type StagedLenderMark, type TenantAppearanceOptions } from './tenantAppearancePlugin';
import * as preference from './themePreference';

const INDEX = [
  '<!doctype html>',
  '<html lang="en">',
  '  <head>',
  '    <meta name="theme-color" content="#04101F" />',
  '    <script src="/theme-boot.js?v=00000000"></script>',
  '    <script src="/boot-watchdog.js?v=00000000"></script>',
  '  </head>',
  '  <body><div id="root"></div></body>',
  '</html>',
].join('\n');
const SHA = 'ab12cd34'.repeat(8);
const PNG: StagedLenderMark = { ext: 'png', bytes: new Uint8Array([137, 80, 78, 71]), sha256: SHA };

interface Emitted { type: 'asset'; fileName: string; source: string | Uint8Array }

function build(options: TenantAppearanceOptions, html = INDEX, filename = '/repo/frontend/index.html') {
  const plugin = tenantAppearancePlugin(options);
  const emitted: Emitted[] = [];
  plugin.buildStart();
  const out = plugin.transformIndexHtml(html, { filename, path: '/index.html' });
  plugin.generateBundle.call({ emitFile: (file: Emitted) => (emitted.push(file), file.fileName) });
  return { out, emitted };
}

const metas = (html: string) => html.split('\n').filter((line) => line.includes('<meta name="mip-'));

describe('tenantAppearancePlugin', () => {
  it('uses exactly the values lib/themePreference.ts exports', () => {
    expect([...plugin.THEME_PREFERENCES]).toEqual([...preference.THEME_PREFERENCES]);
    expect([...plugin.ACCENTS]).toEqual([...preference.ACCENTS]);
    for (const name of [
      'DEFAULT_THEME_PREFERENCE', 'DEFAULT_ACCENT', 'TENANT_THEME_META', 'TENANT_ACCENT_META', 'LENDER_MARK_META', 'LENDER_MARK_LENDER_META',
    ] as const) {
      expect(plugin[name], name).toBe(preference[name]);
    }
  });

  it('writes nothing and emits nothing at the product defaults', () => {
    for (const options of [{ stagedMark: null }, { theme: 'dark', accent: 'bright', stagedMark: null }, { theme: '', accent: ' ', markSha256: '', stagedMark: null }]) {
      const { out, emitted } = build(options);
      expect(out).toBe(INDEX);
      expect(emitted).toEqual([]);
    }
  });

  it('puts each tenant meta on its own line right before the theme-boot tag', () => {
    const { out } = build({ theme: 'light', accent: 'navy', markSha256: SHA, lenderName: 'Fixture Test Lending', stagedMark: PNG });
    const lines = out.split('\n');
    const boot = lines.findIndex((line) => line.includes('<script src="/theme-boot.js'));
    expect(lines.slice(boot - 4, boot)).toEqual([
      '    <meta name="mip-default-theme" content="light">',
      '    <meta name="mip-default-accent" content="navy">',
      `    <meta name="mip-lender-mark" content="/branding/lender-mark.png?v=${SHA.slice(0, 8)}">`,
      '    <meta name="mip-lender-mark-lender" content="Fixture Test Lending">',
    ]);
    expect(metas(out)).toHaveLength(4);
  });

  it('omits each default on its own: dark theme, bright accent', () => {
    expect(metas(build({ theme: 'dark', accent: 'teal', stagedMark: null }).out)).toEqual(['    <meta name="mip-default-accent" content="teal">']);
    expect(metas(build({ theme: 'system', accent: 'bright', stagedMark: null }).out)).toEqual(['    <meta name="mip-default-theme" content="system">']);
  });

  it('validates in buildStart, never in the factory (vitest and vite dev load the config too)', () => {
    const bad = tenantAppearancePlugin({ theme: 'sepia', stagedMark: null });
    expect(() => bad.buildStart()).toThrow(/MIP_DEFAULT_THEME must be one of dark, light, system/);
    expect(() => tenantAppearancePlugin({ accent: 'magenta', stagedMark: null }).buildStart()).toThrow(
      /MIP_DEFAULT_ACCENT must be one of bright, teal, navy, red/,
    );
  });

  it('never emits a staged mark the preflight hash does not name', () => {
    const { out, emitted } = build({ markSha256: '', lenderName: 'Fixture Test Lending', stagedMark: PNG });
    expect(metas(out)).toEqual([]);
    expect(emitted).toEqual([]);
    expect(() => build({ markSha256: 'cd'.repeat(32), lenderName: 'Fixture Test Lending', stagedMark: PNG })).toThrow(/does not match/);
    expect(() => build({ markSha256: SHA, lenderName: 'Fixture Test Lending', stagedMark: null })).toThrow(/does not match/);
    expect(() => build({ markSha256: SHA, lenderName: '  ', stagedMark: PNG })).toThrow(/needs MIP_LENDER_NAME/);
    expect(() => build({ markSha256: SHA.toUpperCase(), lenderName: 'Fixture Test Lending', stagedMark: PNG })).toThrow(/64 lowercase hex/);
  });

  it('emits the validated mark under branding/ by file name only, cache-busted by its hash', () => {
    const webp: StagedLenderMark = { ext: 'webp', bytes: new Uint8Array([82, 73, 70, 70]), sha256: SHA };
    const { out, emitted } = build({ markSha256: SHA, lenderName: 'Fixture Test Lending', stagedMark: webp });
    expect(emitted).toEqual([{ type: 'asset', fileName: 'branding/lender-mark.webp', source: webp.bytes }]);
    expect(out).toContain(`<meta name="mip-lender-mark" content="/branding/lender-mark.webp?v=${SHA.slice(0, 8)}">`);
  });

  it('escapes the lender name as an attribute value', () => {
    const { out } = build({ markSha256: SHA, lenderName: `A&B "Q" 'R' <S> Lending`, stagedMark: PNG });
    expect(out).toContain('<meta name="mip-lender-mark-lender" content="A&amp;B &quot;Q&quot; &#39;R&#39; &lt;S&gt; Lending">');
  });

  it('throws without the theme-boot anchor, and transforms only index.html', () => {
    expect(() => build({ theme: 'light', stagedMark: null }, INDEX.replace('/theme-boot.js', '/other.js'))).toThrow(/no <script src="\/theme-boot\.js/);
    const other = build({ theme: 'light', stagedMark: null }, INDEX, '/repo/frontend/index.home.html');
    expect(other.out).toBe(INDEX);
  });
});
