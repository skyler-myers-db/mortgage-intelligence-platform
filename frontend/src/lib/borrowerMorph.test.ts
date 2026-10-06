/**
 * @vitest-environment happy-dom
 *
 * The borrower-id morph's source marking (deviation:borrower-id-morph):
 * the capture-phase listener names a visible Lead Queue row id only for a
 * primary, unmodified, same-tab click on a link to that masked borrower.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  BORROWER_MORPH_NAME,
  borrowerMorphNameFor,
  borrowerMorphReleaseMs,
  clearBorrowerMorph,
  installBorrowerMorph,
} from './borrowerMorph';

const ID = 'B-0123456789ABC';
const OTHER = 'B-ZZZZZZZZZZZZZ';

type Rect = Pick<DOMRect, 'top' | 'left' | 'bottom' | 'right' | 'width' | 'height'>;

function rect(top: number, height: number, left = 0, width = 400): Rect {
  return { top, left, bottom: top + height, right: left + width, width, height };
}

function stubRect(element: Element, value: Rect) {
  element.getBoundingClientRect = () => ({ ...value, x: value.left, y: value.top, toJSON: () => value }) as DOMRect;
}

let uninstall: () => void = () => undefined;
let reduce = false;
// Keeps the page from navigating; the listener under test has already run in capture.
const stayOnPage = (event: Event) => event.preventDefault();

function page(rowTop = 100, rowHeight = 20) {
  document.body.innerHTML = `
    <main class="main">
      <table><tbody>
        <tr data-borrower-row="${ID}"><td><span class="lead-table__borrower">${ID}</span></td></tr>
        <tr data-borrower-row="${OTHER}"><td><span class="lead-table__borrower">${OTHER}</span></td></tr>
      </tbody></table>
      <a id="open" href="/borrower-360/${ID}">Open Borrower 360</a>
      <a id="other" href="/borrower-360/${OTHER}">Other</a>
      <a id="blank" href="/borrower-360/${ID}" target="_blank">New tab</a>
      <a id="raw" href="/borrower-360/12345">Raw id</a>
      <a id="offer" href="/offer-orchestrator/${ID}">Offer</a>
      <a id="external" href="https://example.test/borrower-360/${ID}">External</a>
    </main>`;
  stubRect(document.querySelector('.main')!, rect(0, 900, 0, 1200));
  stubRect(document.querySelector(`tr[data-borrower-row="${ID}"] .lead-table__borrower`)!, rect(rowTop, rowHeight));
  stubRect(document.querySelector(`tr[data-borrower-row="${OTHER}"] .lead-table__borrower`)!, rect(140, 20));
}

const source = (id = ID) => document.querySelector<HTMLElement>(`tr[data-borrower-row="${id}"] .lead-table__borrower`)!;

function click(selector: string, init: MouseEventInit = {}) {
  const link = document.querySelector(selector)!;
  // The app's own Link handler: the listener must run before it (capture).
  link.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ...init }));
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  reduce = false;
  Object.defineProperty(document, 'startViewTransition', { configurable: true, value: vi.fn() });
  vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
    matches: reduce && query.includes('prefers-reduced-motion'),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
  document.addEventListener('click', stayOnPage);
  page();
  uninstall = installBorrowerMorph();
});

afterEach(() => {
  uninstall();
  document.removeEventListener('click', stayOnPage);
  vi.useRealTimers();
  vi.restoreAllMocks();
  Reflect.deleteProperty(document, 'startViewTransition');
  document.body.innerHTML = '';
});

describe('borrower-id morph source', () => {
  it('names the visible row id of the linked masked borrower, and only that one', () => {
    click('#open');
    expect(source().style.getPropertyValue('view-transition-name')).toBe(BORROWER_MORPH_NAME);
    expect(source(OTHER).style.getPropertyValue('view-transition-name')).toBe('');
    expect(borrowerMorphNameFor(ID)).toBe(BORROWER_MORPH_NAME);
    expect(borrowerMorphNameFor(OTHER)).toBeUndefined();
  });

  it('ignores a raw id, a non-dossier link and another origin', () => {
    for (const selector of ['#raw', '#offer', '#external']) click(selector);
    expect(source().style.getPropertyValue('view-transition-name')).toBe('');
    expect(borrowerMorphNameFor(ID)).toBeUndefined();
  });

  it.each([
    ['meta', { metaKey: true }],
    ['ctrl', { ctrlKey: true }],
    ['shift', { shiftKey: true }],
    ['alt', { altKey: true }],
    ['middle', { button: 1 }],
  ] as const)('ignores a %s click', (_name, init) => {
    click('#open', init);
    expect(source().style.getPropertyValue('view-transition-name')).toBe('');
    expect(borrowerMorphNameFor(ID)).toBeUndefined();
  });

  it('ignores a target=_blank link', () => {
    click('#blank');
    expect(borrowerMorphNameFor(ID)).toBeUndefined();
  });

  it('needs a source that exists and is on screen inside .main', () => {
    stubRect(source(), rect(0, 0, 0, 0)); // a hidden Activity: zero rect
    click('#open');
    expect(borrowerMorphNameFor(ID)).toBeUndefined();
    stubRect(source(), rect(950, 20)); // below .main's visible rect
    click('#open');
    expect(borrowerMorphNameFor(ID)).toBeUndefined();
    source().closest('tr')!.remove();
    click('#open');
    expect(borrowerMorphNameFor(ID)).toBeUndefined();
  });

  it('does nothing without View Transitions or under reduced motion', () => {
    Reflect.deleteProperty(document, 'startViewTransition');
    click('#open');
    expect(borrowerMorphNameFor(ID)).toBeUndefined();
    Object.defineProperty(document, 'startViewTransition', { configurable: true, value: vi.fn() });
    reduce = true;
    click('#open');
    expect(source().style.getPropertyValue('view-transition-name')).toBe('');
    expect(borrowerMorphNameFor(ID)).toBeUndefined();
  });

  it('moves the mark: a second click clears the first source', () => {
    click('#open');
    click('#other');
    expect(source().style.getPropertyValue('view-transition-name')).toBe('');
    expect(source(OTHER).style.getPropertyValue('view-transition-name')).toBe(BORROWER_MORPH_NAME);
    expect(borrowerMorphNameFor(ID)).toBeUndefined();
    expect(borrowerMorphNameFor(OTHER)).toBe(BORROWER_MORPH_NAME);
  });

  it('expires after a second, and clears on demand (the location change)', () => {
    click('#open');
    vi.advanceTimersByTime(999);
    expect(borrowerMorphNameFor(ID)).toBe(BORROWER_MORPH_NAME);
    vi.advanceTimersByTime(1);
    expect(borrowerMorphNameFor(ID)).toBeUndefined();
    expect(source().style.getPropertyValue('view-transition-name')).toBe('');

    click('#open');
    clearBorrowerMorph();
    expect(borrowerMorphNameFor(ID)).toBeUndefined();
    expect(source().style.getPropertyValue('view-transition-name')).toBe('');
  });

  it('uninstalls its one listener', () => {
    uninstall();
    uninstall = () => undefined;
    click('#open');
    expect(borrowerMorphNameFor(ID)).toBeUndefined();
  });

  it('releases the target after --dur-base plus a frame margin', () => {
    document.documentElement.style.setProperty('--dur-base', '200ms');
    expect(borrowerMorphReleaseMs()).toBe(300);
    document.documentElement.style.setProperty('--dur-base', '0.25s');
    expect(borrowerMorphReleaseMs()).toBe(350);
    document.documentElement.style.removeProperty('--dur-base');
  });
});
