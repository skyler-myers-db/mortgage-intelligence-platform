/**
 * Virtual screen-reader harness (post-W5 screen-reader pass, docs/accessibility/VPAT.md).
 *
 * Drives @guidepup/virtual-screen-reader over happy-dom and records what it
 * speaks as a committed golden transcript beside the suites
 * (`transcripts/<flow>.txt`, written by `toMatchFileSnapshot`). A transcript
 * that changes fails the suite: review the new text, then update it on
 * purpose with `npm --prefix frontend run test -- src/test/screenReader -u`.
 * CI never writes one (Vitest refuses to create or rewrite a snapshot under
 * `CI`).
 *
 * The reader builds its tree from the DOM through the W3C mappings (ACCNAME,
 * HTML-AAM, CORE-AAM, WAI-ARIA 1.2). It is NOT a real screen reader, and every
 * suite here states what it cannot model; the short list:
 *
 *   - Pronunciation. It speaks the text it is given: U+2212 stays the "−"
 *     character. NVDA, JAWS and VoiceOver are expected to read U+2212 as
 *     "minus" from their own symbol tables; that reading is unverified here.
 *   - aria-activedescendant. The reader never moves to the active option on
 *     an arrow key; it speaks "active descendant, <name>" only as part of
 *     the focused combobox's own phrase (`reportFocus()` re-reads it).
 *   - Inserted role="alert" elements. Only a mutation INSIDE a live region is
 *     spoken, so an alert element inserted into a non-live parent is silent
 *     here although browsers raise an alert event for it. The suites assert
 *     such alerts on the DOM instead.
 *   - A <textarea>'s typed text in its FOCUS announcement: the focus phrase
 *     carries input and select values only. The virtual cursor does read
 *     the text when it moves through the field, because React mirrors a
 *     textarea's value into its text content (form-validation.txt).
 *   - Native modality. Only `aria-modal="true"` limits the virtual cursor; a
 *     `<dialog>` opened with showModal() (the drawer, the palette, the review)
 *     is announced on entry but the page behind it stays readable here. The
 *     browser's inert top layer is proven by the Playwright fixture specs.
 *   - Speech timing, interruption and queueing (polite versus assertive),
 *     verbosity settings, braille, browse versus focus mode, and anything a
 *     browser computes from layout (visibility, clipping, scrolling).
 *
 * Use it from a happy-dom suite:
 *
 *   registerReaderHooks();          // once, at the top of the suite
 *   const reader = await startReader();
 *   await reader.step('Tab to the skip link', async () => { ... });
 *   await expectTranscript('shell', reader);
 */
import { virtual } from '@guidepup/virtual-screen-reader';
import { act } from 'react';
import { afterEach, beforeEach, expect } from 'vitest';
import { installLocalStorage } from '../installLocalStorage';

/**
 * happy-dom holds each MutationObserver callback only through a WeakRef, so a
 * garbage collection during a long full-suite run would silently drop the
 * reader's observer and with it every later live-region announcement
 * (src/components/mortgage/genieAnnouncer.test-support.ts proves the drop).
 * The reader creates its observer synchronously inside `virtual.start()`,
 * so the global WeakRef is swapped for this strong stand-in only for that
 * synchronous call.
 */
class StrongRef<T extends object> {
  readonly #target: T;
  constructor(target: T) {
    this.#target = target;
  }
  deref(): T {
    return this.#target;
  }
}

interface WeakRefHost {
  WeakRef: unknown;
}

interface Step {
  title: string;
  phrases: string[];
}

/** One recorded walk: named steps, each holding the phrases spoken during it. */
export class ReaderSession {
  readonly #steps: Step[] = [];
  /** Where the running step's phrases start in the reader's log. */
  #from = 0;

  /** Run `action` and record every phrase the reader speaks while it runs. */
  async step(title: string, action: () => Promise<void>): Promise<string[]> {
    this.#from = (await virtual.spokenPhraseLog()).length;
    await action();
    await settle();
    const phrases = (await virtual.spokenPhraseLog()).slice(this.#from);
    this.#steps.push({ title, phrases });
    return phrases;
  }

  /**
   * Turn the reader off and on again over `container`: the cursor goes back
   * to the top of the document (it speaks "document"), and focus goes back to
   * the body, as when a page is first read.
   */
  async restart(container: Node = document.body): Promise<void> {
    await virtual.stop();
    if (document.activeElement instanceof HTMLElement) {
      const focused = document.activeElement;
      await act(async () => focused.blur());
    }
    // stop() cleared the reader's log: the running step reads it from the start.
    this.#from = 0;
    await startVirtual(container);
  }

  /** The transcript as committed: `## <step>` headers, one spoken phrase per line. */
  text(): string {
    return `${this.#steps.map(({ title, phrases }) => [`## ${title}`, ...phrases].join('\n')).join('\n\n')}\n`;
  }

  /** Every phrase spoken in every step, in order. */
  phrases(): string[] {
    return this.#steps.flatMap(({ phrases }) => phrases);
  }
}

/**
 * Let React commit and the reader's microtask-delayed handlers (focus moves,
 * live-region mutations) run, inside act so no update escapes it.
 */
export async function settle(turns = 4): Promise<void> {
  await act(async () => {
    for (let turn = 0; turn < turns; turn += 1) {
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  });
}

async function startVirtual(container: Node): Promise<void> {
  const host = globalThis as unknown as WeakRefHost;
  const weakRef = host.WeakRef;
  host.WeakRef = StrongRef;
  let started: Promise<void>;
  try {
    started = virtual.start({ container });
  } finally {
    host.WeakRef = weakRef;
  }
  await act(async () => {
    await started;
  });
}

/** Start the reader over `container` (the whole body by default). */
export async function startReader(container: Node = document.body): Promise<ReaderSession> {
  await startVirtual(container);
  return new ReaderSession();
}

/**
 * Visit every landmark once, from the cursor, the way NVDA's D or
 * VoiceOver's landmark rotor does; the reader wraps at the end, so the walk
 * stops when a landmark phrase comes round again.
 */
export async function walkLandmarks(limit = 30): Promise<void> {
  const seen = new Set<string>();
  for (let moves = 0; moves < limit; moves += 1) {
    await nextLandmark();
    const spoken = (await virtual.lastSpokenPhrase()) ?? '';
    if (seen.has(spoken)) return;
    seen.add(spoken);
  }
  throw new Error(`walkLandmarks: no landmark came round again in ${limit} moves`);
}

/** Turn the reader off (it clears its spoken log and stops observing). */
export async function stopReader(): Promise<void> {
  await virtual.stop();
}

/**
 * Register the walk hooks in the CALLING suite: every walk starts from the
 * default persisted UI state (in a full-suite worker happy-dom's storage is
 * real, and another suite's leftovers, an open Console or a moved Genie
 * panel, would change what the reader hears), and the reader is stopped
 * after each test. Explicit, so importing a helper from this module adds no
 * hook to a suite that did not ask for one; a one-off use outside the
 * screen-reader suites calls `stopReader()` itself.
 */
export function registerReaderHooks(): void {
  beforeEach(() => {
    installLocalStorage();
    try {
      window.sessionStorage.clear();
    } catch {
      /* no session storage in this environment */
    }
  });
  afterEach(stopReader);
}

/** Move the virtual cursor one item forward (a screen reader's "next item"). */
export async function next(): Promise<void> {
  await act(async () => {
    await virtual.next();
  });
}

/**
 * Move forward until the last spoken phrase satisfies `match`, failing after
 * `limit` moves so a missing target never loops (the reader wraps around).
 */
export async function nextUntil(match: string | RegExp, limit = 300): Promise<void> {
  for (let moves = 0; moves < limit; moves += 1) {
    await next();
    const spoken = (await virtual.lastSpokenPhrase()) ?? '';
    if (typeof match === 'string' ? spoken === match : match.test(spoken)) return;
  }
  throw new Error(`the virtual screen reader never spoke ${String(match)} in ${limit} moves`);
}

/** Move the virtual cursor one item back (a screen reader's "previous item"). */
export async function previous(): Promise<void> {
  await act(async () => {
    await virtual.previous();
  });
}

/** Move back until the last spoken phrase satisfies `match` (bounded, as nextUntil). */
export async function previousUntil(match: string | RegExp, limit = 300): Promise<void> {
  for (let moves = 0; moves < limit; moves += 1) {
    await previous();
    const spoken = (await virtual.lastSpokenPhrase()) ?? '';
    if (typeof match === 'string' ? spoken === match : match.test(spoken)) return;
  }
  throw new Error(`the virtual screen reader never spoke ${String(match)} in ${limit} moves back`);
}

/** Read forward until "end of document", the way a reader's say-all does. */
export async function readToEnd(limit = 600): Promise<void> {
  await nextUntil('end of document', limit);
}

/** Move to the next landmark (NVDA D, VoiceOver's landmark rotor). */
export async function nextLandmark(): Promise<void> {
  await act(async () => {
    await virtual.perform(virtual.commands.moveToNextLandmark);
  });
}

/** Move back to the previous form landmark (a named form; VoiceOver's landmark rotor). */
export async function previousForm(): Promise<void> {
  await act(async () => {
    await virtual.perform(virtual.commands.moveToPreviousForm);
  });
}

/** Move to the next heading (NVDA / JAWS H). */
export async function nextHeading(): Promise<void> {
  await act(async () => {
    await virtual.perform(virtual.commands.moveToNextHeading);
  });
}

/** Press a key on the item under the virtual cursor (it is focused first). */
export async function press(key: string): Promise<void> {
  await act(async () => {
    await virtual.press(key);
  });
}

/** Activate the item under the virtual cursor (a click, as NVDA Enter in browse mode). */
export async function activate(): Promise<void> {
  await act(async () => {
    await virtual.click();
  });
}

/**
 * Re-announce the focused element (NVDA+Tab, VoiceOver VO-F3): the reader
 * speaks it again only when its phrase changed (a new active descendant, a
 * new state). Dispatched as the focusin the reader listens to.
 */
export async function reportFocus(): Promise<void> {
  const focused = document.activeElement;
  if (!(focused instanceof HTMLElement)) throw new Error('reportFocus: nothing is focused');
  await act(async () => {
    focused.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
  });
}

/**
 * Compare the walk with its committed golden transcript,
 * `src/test/screenReader/transcripts/<name>.txt`.
 */
export async function expectTranscript(name: string, session: ReaderSession): Promise<void> {
  await expect(session.text()).toMatchFileSnapshot(`./transcripts/${name}.txt`);
}
