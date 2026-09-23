/**
 * @vitest-environment happy-dom
 *
 * The shared APG tabs primitive (2026-09-21 audit a11y-02 / stack-05),
 * extracted from the evidence drawer. Its consumers are pinned where they
 * render: EvidenceDrawer.lineage.test.tsx, analytics.tabs.keyboard.test.tsx,
 * BorrowerProofDrawer.tabs.test.tsx and filter-listbox.fixture.spec.ts.
 */

import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useTabs } from './useTabs';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type View = 'math' | 'evidence' | 'lineage';
const VIEWS: readonly View[] = ['math', 'evidence', 'lineage'];

function Harness() {
  const [view, setView] = useState<View>('math');
  const tabs = useTabs({ tabs: VIEWS, selected: view, onSelect: setView, idBase: 'probe' });
  return (
    <>
      <div {...tabs.tabListProps} aria-label="Views">
        {VIEWS.map((id) => (
          <button key={id} {...tabs.tabProps(id)}>
            {id}
          </button>
        ))}
      </div>
      <div {...tabs.panelProps(view)}>{view} panel</div>
    </>
  );
}

let container: HTMLDivElement;
let root: Root;

const tab = (id: View): HTMLButtonElement => document.getElementById(`probe-tab-${id}`) as HTMLButtonElement;
const panel = (): HTMLElement => container.querySelector<HTMLElement>('[role="tabpanel"]') as HTMLElement;

function press(key: string): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  act(() => {
    (document.activeElement ?? document.body).dispatchEvent(event);
  });
  return event;
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(<Harness />));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('useTabs', () => {
  it('wires tablist, tabs and the selected panel to each other', () => {
    const list = container.querySelector('[role="tablist"]');
    expect(list?.getAttribute('aria-orientation')).toBe('horizontal');
    expect(tab('math').getAttribute('aria-selected')).toBe('true');
    expect(tab('math').getAttribute('aria-controls')).toBe(panel().id);
    expect(panel().getAttribute('aria-labelledby')).toBe(tab('math').id);
    expect(VIEWS.map((id) => tab(id).tabIndex)).toEqual([0, -1, -1]);
  });

  it('ArrowRight / ArrowLeft select and focus the neighbouring tab, wrapping', () => {
    tab('math').focus();
    const right = press('ArrowRight');
    expect(right.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(tab('evidence'));
    expect(tab('evidence').getAttribute('aria-selected')).toBe('true');
    expect(panel().id).toBe('probe-panel-evidence');
    expect(VIEWS.map((id) => tab(id).tabIndex)).toEqual([-1, 0, -1]);

    press('ArrowLeft');
    press('ArrowLeft');
    expect(document.activeElement).toBe(tab('lineage'));
    expect(tab('lineage').getAttribute('aria-selected')).toBe('true');
    press('ArrowRight');
    expect(document.activeElement).toBe(tab('math'));
  });

  it('Home and End jump to the first and last tab; other keys are left alone', () => {
    tab('math').focus();
    press('End');
    expect(document.activeElement).toBe(tab('lineage'));
    expect(panel().textContent).toBe('lineage panel');
    press('Home');
    expect(document.activeElement).toBe(tab('math'));
    expect(press('ArrowDown').defaultPrevented).toBe(false);
    expect(tab('math').getAttribute('aria-selected')).toBe('true');
  });

  it('a click selects the tab', () => {
    act(() => tab('lineage').click());
    expect(tab('lineage').getAttribute('aria-selected')).toBe('true');
    expect(panel().getAttribute('aria-labelledby')).toBe('probe-tab-lineage');
  });
});
