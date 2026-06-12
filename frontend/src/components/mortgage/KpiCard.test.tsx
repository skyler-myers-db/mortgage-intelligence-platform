import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { KpiCard } from './KpiCard';

/**
 * KpiCard rendering guarantees that drive the Day-0 empty-state contract
 * (hole-finder round 2 #13, 2026-04-23). The parent routes (home and
 * portfolio-builder) detect the empty-gold-tables shape and swap the
 * KPI numeric prop to `null`; KpiCard MUST then render an em-dash, never
 * a plausible-but-fake "0". The other cases pin the formatter so a
 * future refactor can't silently switch "123456" -> "123456" (no
 * thousand-separators) or suppress the value.
 */

describe('KpiCard value rendering', () => {
  it('renders an em-dash when valueAnimated is null (Day-0 empty state)', () => {
    const html = renderToStaticMarkup(
      <KpiCard label="Marketable population" valueAnimated={null} />,
    );
    // The em-dash (U+2014) is the contract the routes rely on — not a
    // hyphen, not "N/A". Assert it exactly so a drift to "-" is caught.
    expect(html).toContain('—');
    expect(html).not.toMatch(/>\s*0\s*</);
  });

  it('formats a numeric value with locale grouping by default', () => {
    const html = renderToStaticMarkup(
      <KpiCard label="Marketable population" valueAnimated={12345} />,
    );
    // Default formatter is `Math.round(n).toLocaleString()`. In node the
    // default locale is en-US so we assert the comma form; if the build
    // environment changes, this test is the right place to catch it.
    expect(html).toContain('12,345');
  });

  it('prefers a static `value` prop when `valueAnimated` is absent', () => {
    const html = renderToStaticMarkup(
      <KpiCard label="Avg rate" value="$2.18" />,
    );
    expect(html).toContain('$2.18');
  });

  it('applies the one-time entrance class to a settled value, but not to loading/em-dash states (re-audit #4 #2)', () => {
    // A real value on first appearance gets the sleek entrance class.
    const settled = renderToStaticMarkup(
      <KpiCard label="Marketable population A" valueAnimated={79730} />,
    );
    expect(settled).toContain('kpi__value--enter');
    // A loading card renders skeletons, no value, no entrance.
    const loadingCard = renderToStaticMarkup(
      <KpiCard label="Marketable population B" valueAnimated={79730} loading />,
    );
    expect(loadingCard).not.toContain('kpi__value--enter');
    // An em-dash (unknown/day-zero) must not animate a non-number.
    const dash = renderToStaticMarkup(
      <KpiCard label="Marketable population C" valueAnimated={null} />,
    );
    expect(dash).not.toContain('kpi__value--enter');
  });

  it('renders an em-dash (not "0") for a zero value only when prop is null', () => {
    // Sanity check: valueAnimated={0} still renders "0". It's the PARENT
    // (home.tsx / portfolio-builder.tsx) that translates a Day-0 zero into
    // `null` before handing the prop to KpiCard. That separation of
    // concerns is deliberate — KpiCard is a dumb formatter.
    const zero = renderToStaticMarkup(
      <KpiCard label="Population" valueAnimated={0} />,
    );
    expect(zero).toMatch(/>\s*0\s*</);
    const nul = renderToStaticMarkup(
      <KpiCard label="Population" valueAnimated={null} />,
    );
    expect(nul).toContain('—');
  });
});
