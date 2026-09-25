/**
 * @vitest-environment happy-dom
 *
 * Collapsed earlier turns (audit 2026-09-21 `genie-08`): with three or more
 * settled turns, a turn this surface never saw as the latest renders as its
 * digest with the full answer unmounted. A turn that became earlier because
 * a new answer landed stays as the reader saw it. State is keyed by response
 * object identity, so the store's 20-turn head eviction never re-collapses
 * or re-expands anything.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { GenieAnswer as GenieAnswerShape } from '../../types';
import { GenieCollapsedTurn } from './GenieCollapsedTurn';
import { useGenieTurnCollapse } from './useGenieTurnCollapse';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function turn(n: number, overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: `Answer ${n}: **Illinois** leads with ${n},000 borrowers.`,
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    message_id: `msg-${n}`,
    ...overrides,
  } as GenieAnswerShape;
}

function Thread({ responses }: { responses: readonly GenieAnswerShape[] }) {
  const collapse = useGenieTurnCollapse(responses);
  return (
    <>
      {responses.map((response) => {
        const full = <div className="full-answer">{response.answer}</div>;
        const presentation = collapse.presentation(response);
        return (
          <div className="turn" key={response.message_id}>
            {presentation === 'full' ? (
              full
            ) : (
              <GenieCollapsedTurn
                payload={response}
                expanded={presentation === 'expanded'}
                onToggle={() => collapse.toggle(response)}
              >
                {full}
              </GenieCollapsedTurn>
            )}
          </div>
        );
      })}
    </>
  );
}

describe('useGenieTurnCollapse + GenieCollapsedTurn', () => {
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
  });

  const renderThread = (responses: readonly GenieAnswerShape[]) => act(() => root.render(<Thread responses={responses} />));
  const turns = () => Array.from(container.querySelectorAll('.turn'));
  const states = () =>
    turns().map((el) => {
      if (!el.querySelector('.genie-collapse')) return 'full';
      return el.querySelector('.full-answer') ? 'expanded' : 'collapsed';
    });

  it('collapses every turn but the latest of a 3-turn thread restored at mount', () => {
    renderThread([turn(1), turn(2), turn(3)]);
    expect(states()).toEqual(['collapsed', 'collapsed', 'full']);
    const first = turns()[0];
    // The full answer is not mounted; the digest is the displayed wording.
    expect(first.querySelector('.full-answer')).toBeNull();
    expect(first.querySelector('.genie-collapse__digest')?.textContent).toBe('Answer 1: Illinois leads with 1,000 borrowers.');
    const toggle = first.querySelector('button.genie-collapse__toggle');
    expect(toggle?.textContent).toBe('Show full answer');
    expect(toggle?.getAttribute('aria-expanded')).toBe('false');
    expect(toggle?.hasAttribute('aria-controls')).toBe(false);
  });

  it('describes each collapsed toggle by its own digest, so same-named toggles differ', () => {
    renderThread([turn(1), turn(2), turn(3)]);
    const toggles = Array.from(container.querySelectorAll<HTMLButtonElement>('button.genie-collapse__toggle'));
    const descriptions = toggles.map((toggle) => {
      const id = toggle.getAttribute('aria-describedby');
      return id ? document.getElementById(id)?.textContent : null;
    });
    expect(toggles.map((toggle) => toggle.textContent)).toEqual(['Show full answer', 'Show full answer']);
    expect(descriptions).toEqual([
      'Answer 1: Illinois leads with 1,000 borrowers.',
      'Answer 2: Illinois leads with 2,000 borrowers.',
    ]);
    // Open, the digest is gone and so is the description.
    act(() => toggles[0].click());
    expect(toggles[0].hasAttribute('aria-describedby')).toBe(false);
  });

  it('leaves a 2-turn thread alone', () => {
    renderThread([turn(1), turn(2)]);
    expect(states()).toEqual(['full', 'full']);
  });

  it('keeps the previous latest as it was when a new answer lands under it', () => {
    const restored = [turn(1), turn(2), turn(3)];
    renderThread(restored);
    expect(states()).toEqual(['collapsed', 'collapsed', 'full']);
    renderThread([...restored, turn(4)]);
    // Turn 3 became "earlier" on screen: it stays in full (no shift above).
    expect(states()).toEqual(['collapsed', 'collapsed', 'full', 'full']);
  });

  it('never collapses a turn already shown in full while the thread was short', () => {
    const one = turn(1);
    const two = turn(2);
    renderThread([one, two]);
    renderThread([one, two, turn(3)]);
    expect(states()).toEqual(['full', 'full', 'full']);
  });

  it('opens and closes a collapsed turn on its toggle, mounting the answer only while open', () => {
    renderThread([turn(1), turn(2), turn(3)]);
    const toggle = () => turns()[0].querySelector<HTMLButtonElement>('button.genie-collapse__toggle')!;
    act(() => toggle().click());
    expect(states()).toEqual(['expanded', 'collapsed', 'full']);
    expect(toggle().textContent).toBe('Collapse answer');
    expect(toggle().getAttribute('aria-expanded')).toBe('true');
    act(() => toggle().click());
    expect(states()).toEqual(['collapsed', 'collapsed', 'full']);
  });

  it('keeps each turn state through the store cap evicting the head (identity, not index)', () => {
    const all = [turn(1), turn(2), turn(3), turn(4)];
    renderThread(all);
    act(() => turns()[2].querySelector<HTMLButtonElement>('button.genie-collapse__toggle')!.click());
    expect(states()).toEqual(['collapsed', 'collapsed', 'expanded', 'full']);
    // A new answer lands and the cap drops the oldest turn: indices shift.
    const five = turn(5);
    renderThread([...all.slice(1), five]);
    expect(states()).toEqual(['collapsed', 'expanded', 'full', 'full']);
  });

  it('collapses everything but the latest after a History restore creates new objects', () => {
    const live = [turn(1), turn(2), turn(3)];
    renderThread(live.slice(0, 1));
    renderThread(live.slice(0, 2));
    renderThread(live);
    expect(states()).toEqual(['full', 'full', 'full']);
    renderThread([turn(1), turn(2), turn(3)]);
    expect(states()).toEqual(['collapsed', 'collapsed', 'full']);
  });

  it('never collapses a withheld or failed turn', () => {
    renderThread([turn(1, { source: 'refused' }), turn(2, { source: 'degraded' }), turn(3), turn(4)]);
    expect(states()).toEqual(['full', 'full', 'collapsed', 'full']);
  });

  it('digests the metric first, then the summary', () => {
    renderThread([turn(1, { metric_value: '5.25 years' }), turn(2, { summary: 'Demand *concentrates* in three states.' }), turn(3)]);
    const digests = Array.from(container.querySelectorAll('.genie-collapse__digest')).map((el) => el.textContent);
    expect(digests).toEqual(['5.25 years', 'Demand concentrates in three states.']);
  });
});
