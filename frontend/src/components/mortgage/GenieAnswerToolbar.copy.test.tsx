/**
 * @vitest-environment happy-dom
 *
 * Copy SQL / Copy answer (audit 2026-09-21 `genie-06`, slice 1): the answer
 * toolbar writes through navigator.clipboard, confirms visibly, and falls
 * back to a selectable field when the browser denies the clipboard.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer as GenieAnswerShape } from '../../types';
import { GenieAnswerToolbar, answerPlainText, answerSql } from './GenieAnswerToolbar';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SQL = 'SELECT state, count(*) AS borrowers FROM mip.gold.borrower_360 GROUP BY state';

function payload(overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: 'Texas leads with **900** in-the-money borrowers.\n- `TX`: 900\n- `CA`: 700',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    metric_value: '1,600 borrowers',
    table_rows: [
      { state: 'TX', borrowers: 900, avg_rate_spread_bps: 112, avg_equity_pct: 46, contactable: 30 },
      { state: 'CA', borrowers: 700, avg_rate_spread_bps: 98, avg_equity_pct: 51, contactable: 22 },
    ],
    proof: { sql_query: SQL, source_assets: ['mip.gold.borrower_360'], trusted: true },
    ...overrides,
  };
}

function installClipboard(writeText: ((text: string) => Promise<void>) | undefined) {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: writeText ? { writeText } : undefined,
  });
}

describe('answer plain text', () => {
  it('flattens markdown, keeps the headline metric, and includes EVERY row, not the capped table', () => {
    const text = answerPlainText(payload());
    expect(text.startsWith('1,600 borrowers')).toBe(true);
    // The wording the bubble displays, not the raw wire text.
    expect(text).toContain('Texas leads with 900 borrowers passing the refinance-economics screen.');
    expect(text).not.toContain('in-the-money');
    expect(text).not.toContain('**');
    expect(text).not.toContain('`');
    // Tab-separated rows with humanized headers, all five columns present.
    expect(text).toContain('State\tBorrowers\tAvg Rate Spread Bps\tAvg Equity Pct\tContactable');
    expect(text).toContain('TX\t900\t112\t46\t30');
    expect(text).toContain('CA\t700\t98\t51\t22');
  });

  it('uses the summary and each section of a deep-research answer', () => {
    const text = answerPlainText(
      payload({
        answer: 'stitched narrative',
        summary: 'Refinance demand concentrates in three states.',
        sections: [
          { title: 'Where the opportunity sits', question: 'q', answer: 'Texas leads.', table_rows: [{ state: 'TX', n: 1 }] },
        ],
        table_rows: [{ ignored: true }],
      }),
    );
    expect(text).toContain('Refinance demand concentrates in three states.');
    expect(text).toContain('Where the opportunity sits\nTexas leads.\nState\tN\nTX\t1');
    expect(text).not.toContain('stitched narrative');
    expect(text).not.toContain('ignored');
  });

  it('reads the SQL from the proof, then the top-level field, else nothing', () => {
    expect(answerSql(payload())).toBe(SQL);
    expect(answerSql(payload({ proof: null, sql_query: ' SELECT 1 ' }))).toBe('SELECT 1');
    expect(answerSql(payload({ proof: null, sql_query: null }))).toBeNull();
  });
});

describe('GenieAnswerToolbar', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    installClipboard(undefined);
  });

  const render = (p: GenieAnswerShape, announce?: boolean) =>
    act(() => root.render(<GenieAnswerToolbar payload={p} announce={announce} />));
  const button = (name: string) => {
    const el = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === name);
    if (!el) throw new Error(`${name} button not rendered`);
    return el;
  };
  const flush = () => act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
  const status = () => container.querySelector('.genie-answer__toolbar-status')?.textContent;

  it('Copy SQL writes the governed SQL and confirms visibly', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    installClipboard(writeText);
    render(payload());
    await act(async () => button('Copy SQL').click());
    await flush();
    expect(writeText).toHaveBeenCalledWith(SQL);
    expect(status()).toBe('SQL copied');
    expect(container.querySelector('textarea')).toBeNull();
    // The confirmation is visible text, not a second live region: the
    // floating panel owns the one announcer (a11y-06).
    expect(container.querySelector('[role="status"], [aria-live]')).toBeNull();
  });

  it('on /ask-genie (announce) the confirmation is a polite status region too', async () => {
    installClipboard(vi.fn().mockResolvedValue(undefined));
    render(payload(), true);
    await act(async () => button('Copy SQL').click());
    await flush();
    const region = container.querySelector('[role="status"]');
    expect(region?.classList.contains('genie-answer__toolbar-status')).toBe(true);
    expect(region?.textContent).toBe('SQL copied');
  });

  it('Copy answer writes the plain-text answer', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    installClipboard(writeText);
    render(payload());
    await act(async () => button('Copy answer').click());
    await flush();
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText.mock.calls[0][0]).toBe(answerPlainText(payload()));
    expect(status()).toBe('Answer copied');
  });

  it('a denied clipboard falls back to a selectable field holding the text', async () => {
    installClipboard(vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError')));
    render(payload());
    await act(async () => button('Copy SQL').click());
    await flush();
    expect(status()).toBe('Clipboard blocked');
    const field = container.querySelector<HTMLTextAreaElement>('.genie-answer__copy-fallback textarea');
    expect(field).not.toBeNull();
    expect(field?.value).toBe(SQL);
    expect(field?.readOnly).toBe(true);
    expect(container.textContent).toContain('blocked clipboard access');
    // "The text is selected above" holds without a click: the field has
    // focus and its whole text is selected; the hint describes it.
    expect(document.activeElement).toBe(field);
    expect(field?.selectionStart).toBe(0);
    expect(field?.selectionEnd).toBe(SQL.length);
    const hintId = field?.getAttribute('aria-describedby');
    expect(hintId && document.getElementById(hintId)?.textContent).toContain('copy it with your keyboard');
  });

  // An invariant, not a regression pin: the React Compiler memoizes an inline
  // ref callback without reactive deps, so the old inline `select()` ref held
  // it too. The stable ref + effect keeps it without relying on the compiler.
  it('the fallback selects once on appearance, not on every render', async () => {
    installClipboard(vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError')));
    render(payload());
    await act(async () => button('Copy SQL').click());
    await flush();
    const field = container.querySelector<HTMLTextAreaElement>('.genie-answer__copy-fallback textarea')!;
    // The user narrows the selection by hand, then something re-renders the
    // toolbar: their selection must survive.
    act(() => field.setSelectionRange(0, 6));
    render(payload());
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe(6);
  });

  it('no clipboard API at all also falls back instead of failing silently', async () => {
    installClipboard(undefined);
    render(payload());
    await act(async () => button('Copy answer').click());
    await flush();
    expect(container.querySelector<HTMLTextAreaElement>('.genie-answer__copy-fallback textarea')?.value).toBe(
      answerPlainText(payload()),
    );
  });

  it('offers Copy SQL only when the answer carries SQL', () => {
    render(payload({ proof: null, sql_query: null }));
    expect(Array.from(container.querySelectorAll('button')).map((b) => b.textContent?.trim())).toEqual(['Copy answer']);
  });
});
