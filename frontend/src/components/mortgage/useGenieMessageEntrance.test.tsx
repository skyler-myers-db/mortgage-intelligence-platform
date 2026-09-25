/**
 * @vitest-environment happy-dom
 *
 * The Stopped note's one-shot entrance (audit 2026-09-21 `motion-v2`) versus
 * the server's confirmed copy that REPLACES it (audit `genie-03`): a new note
 * object with the same `stopId`. The replacement never replays the entrance;
 * a Stop of another turn still enters.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { GenieTurnNote } from '../../lib/genieInFlightTurn';
import { useGenieMessageEntrance } from './useGenieMessageEntrance';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** Renders each note the way a surface does: entering or not, by identity. */
function Harness({ notes }: { notes: readonly GenieTurnNote[] }) {
  const entrance = useGenieMessageEntrance({ isVisible: () => true, inFlight: null, notes });
  return (
    <ul>
      {notes.map((note) => (
        <li key={note.stopId} data-stop={note.stopId} data-entering={String(entrance.entering(note))}>
          {note.reason}
        </li>
      ))}
    </ul>
  );
}

function stopped(stopId: number, reason: string): GenieTurnNote {
  return { kind: 'stopped', reason, question: 'Which states lead?', atTurnIndex: 0, stopId };
}

describe('useGenieMessageEntrance: Stopped notes', () => {
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

  function entering(stopId: number): boolean {
    return container.querySelector(`[data-stop="${stopId}"]`)?.getAttribute('data-entering') === 'true';
  }

  it('a Stopped note enters; its confirmed replacement (same stopId) does not re-enter', () => {
    const first = stopped(7, 'Stopped before the answer arrived.');
    act(() => root.render(<Harness notes={[]} />));
    act(() => root.render(<Harness notes={[first]} />));
    expect(entering(7)).toBe(true);

    const confirmed = { ...first, reason: 'Stopped. This answer was not recorded and will not appear later.' };
    act(() => root.render(<Harness notes={[confirmed]} />));

    expect(container.textContent).toContain('not recorded');
    expect(entering(7)).toBe(false);
  });

  it('a Stopped note of another turn still enters after a replacement', () => {
    const first = stopped(7, 'Stopped before the answer arrived.');
    const confirmed = { ...first, reason: 'Stopped. This answer was not recorded and will not appear later.' };
    act(() => root.render(<Harness notes={[]} />));
    act(() => root.render(<Harness notes={[first]} />));
    act(() => root.render(<Harness notes={[confirmed]} />));

    const next = stopped(9, 'Stopped before the answer arrived.');
    act(() => root.render(<Harness notes={[confirmed, next]} />));

    expect(entering(9)).toBe(true);
    expect(entering(7)).toBe(false);
  });

  it('a note already present at mount never enters', () => {
    const present = stopped(3, 'Stopped before the answer arrived.');
    act(() => root.render(<Harness notes={[present]} />));

    expect(entering(3)).toBe(false);
  });
});
