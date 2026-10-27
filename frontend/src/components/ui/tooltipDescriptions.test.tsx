/**
 * @vitest-environment happy-dom
 *
 * The tooltip descriptions' shared store (post-W5 tooltip-render-cost):
 * one ref-counted node per distinct plain-text description, every
 * description inside ONE hidden host (an unstyled `display: none` subtree),
 * and every trigger's accessible description unchanged. Before it, each
 * Tooltip portalled its own `<span hidden>` straight into <body>: seven per
 * Lead Queue row, a styled element each, mounted and unmounted by every
 * virtual-window shift.
 *
 * The accessible description is read the way accname computes it for
 * `aria-describedby`: each referenced node's text, joined by a space,
 * hidden or not.
 */
import { StrictMode } from 'react';
import { describe, expect, it } from 'vitest';
import { mount } from '../../test/render';
import { tooltipText } from '../../test/tooltipText';
import { Tooltip } from './Tooltip';
import { DESCRIPTION_HOST_ATTRIBUTE, descriptionId } from './tooltipDescriptions';

const HOST = `[${DESCRIPTION_HOST_ATTRIBUTE}]`;

function host(): HTMLElement | null {
  return document.body.querySelector<HTMLElement>(HOST);
}

/** Every mounted description node (shared or per-trigger), wherever it sits. */
function descriptions(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('[id^="tooltip-desc"]'));
}

function accessibleDescription(element: Element): string {
  return (element.getAttribute('aria-describedby') ?? '')
    .split(/\s+/)
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? '')
    .join(' ');
}

function byLabel(name: string): HTMLElement {
  const found = document.querySelector<HTMLElement>(`[aria-label="${name}"]`);
  if (!found) throw new Error(`no element named ${name}`);
  return found;
}

interface Row {
  id: string;
  clip: string;
}

/** A queue-row stand-in: one per-row text (the clip) and two shared ones. */
function QueueRow({ row }: { row: Row }) {
  return (
    <li>
      <Tooltip content={row.clip}>
        <span aria-label={`clip ${row.id}`}>{row.clip}</span>
      </Tooltip>
      <Tooltip content="Signal strength note">
        <span aria-label={`signal ${row.id}`}>s</span>
      </Tooltip>
      <Tooltip content="Reject">
        <button type="button" aria-label={`Reject ${row.id}`}>r</button>
      </Tooltip>
    </li>
  );
}

const ROWS: Row[] = Array.from({ length: 8 }, (_, index) => ({ id: `B${index}`, clip: `clip_${index}` }));

/** A window on ROWS: rows start..start+3, keyed, as a virtualized table renders them. */
function Window({ start = 0 }: { start?: number }) {
  return (
    <ul>
      {ROWS.slice(start, start + 4).map((row) => <QueueRow key={row.id} row={row} />)}
    </ul>
  );
}

describe('tooltip descriptions', () => {
  it('one node per distinct text: every trigger with the same text names the same node', async () => {
    await mount(<Window />);

    // 4 rows x 3 triggers, 4 clips + 2 shared texts.
    expect(document.querySelectorAll('[data-tooltip]')).toHaveLength(12);
    expect(descriptions()).toHaveLength(6);
    const rejects = ROWS.slice(0, 4).map((row) => byLabel(`Reject ${row.id}`));
    expect(new Set(rejects.map((button) => button.getAttribute('aria-describedby'))).size).toBe(1);
    expect(rejects[0].getAttribute('aria-describedby')).toBe(descriptionId('Reject'));
  });

  it('keeps every trigger\'s accessible description and tooltip text', async () => {
    await mount(<Window />);

    for (const row of ROWS.slice(0, 4)) {
      expect(accessibleDescription(byLabel(`clip ${row.id}`))).toBe(row.clip);
      expect(accessibleDescription(byLabel(`signal ${row.id}`))).toBe('Signal strength note');
      expect(accessibleDescription(byLabel(`Reject ${row.id}`))).toBe('Reject');
      expect(tooltipText(byLabel(`Reject ${row.id}`))).toBe('Reject');
    }
  });

  it('lives in one hidden host at the end of <body>, never as a styled child of <body>', async () => {
    await mount(
      <>
        <Window />
        <Tooltip content={<>Rich <strong>text</strong></>}>
          <button type="button" aria-label="rich">x</button>
        </Tooltip>
      </>,
    );

    const box = host();
    expect(box?.parentElement).toBe(document.body);
    expect(box?.hidden).toBe(true);
    expect(document.body.querySelectorAll(`${HOST}`)).toHaveLength(1);
    expect(descriptions()).toHaveLength(7);
    for (const node of descriptions()) {
      expect(node.parentElement, node.id).toBe(box);
      expect(node.hidden, node.id).toBe(true);
    }
    expect(document.body.querySelectorAll(':scope > [id^="tooltip-desc"]')).toHaveLength(0);
  });

  it('a window shift adds nodes only for new texts and keeps the shared ones in place', async () => {
    const view = await mount(<Window />);
    const reject = document.getElementById(descriptionId('Reject'));
    const before = new Set(descriptions().map((node) => node.id));

    await view.rerender(<Window start={2} />);

    const after = descriptions();
    const added = after.filter((node) => !before.has(node.id)).map((node) => node.id);
    const removed = [...before].filter((id) => !document.getElementById(id));
    expect(added).toEqual([descriptionId('clip_4'), descriptionId('clip_5')]);
    expect(removed).toEqual([descriptionId('clip_0'), descriptionId('clip_1')]);
    expect(after).toHaveLength(6);
    // The same node, not a re-created twin.
    expect(document.getElementById(descriptionId('Reject'))).toBe(reject);
    for (const row of ROWS.slice(2, 6)) expect(accessibleDescription(byLabel(`clip ${row.id}`))).toBe(row.clip);
  });

  it('is ref-counted: a node leaves with its last trigger, and the host with its last node', async () => {
    function Pair({ count }: { count: number }) {
      return (
        <>
          {Array.from({ length: count }, (_, index) => (
            <Tooltip key={index} content="Reject">
              <button type="button" aria-label={`Reject ${index}`}>r</button>
            </Tooltip>
          ))}
        </>
      );
    }
    const view = await mount(<Pair count={2} />);
    const node = document.getElementById(descriptionId('Reject'));
    expect(node).not.toBeNull();

    await view.rerender(<Pair count={1} />);
    expect(document.getElementById(descriptionId('Reject'))).toBe(node);
    expect(accessibleDescription(byLabel('Reject 0'))).toBe('Reject');

    await view.rerender(<Pair count={0} />);
    expect(document.getElementById(descriptionId('Reject'))).toBeNull();
    expect(host()).toBeNull();
  });

  it('a content change moves the trigger to the new text and frees the old node', async () => {
    const gated = 'Approval needs the Approver role';
    const view = await mount(
      <Tooltip content="Reject"><button type="button" aria-label="Reject B1">r</button></Tooltip>,
    );
    await view.rerender(
      <Tooltip content={gated}><button type="button" aria-label="Reject B1">r</button></Tooltip>,
    );

    expect(accessibleDescription(byLabel('Reject B1'))).toBe(gated);
    expect(document.getElementById(descriptionId('Reject'))).toBeNull();
    expect(descriptions()).toHaveLength(1);
  });

  it('a shortcut is part of the description: "Close" and "Close (Esc)" are two nodes', async () => {
    await mount(
      <>
        <Tooltip content="Close"><button type="button" aria-label="close a">x</button></Tooltip>
        <Tooltip content="Close" shortcut="Esc"><button type="button" aria-label="close b">x</button></Tooltip>
      </>,
    );

    expect(accessibleDescription(byLabel('close a'))).toBe('Close');
    expect(accessibleDescription(byLabel('close b'))).toBe('Close (Esc)');
    expect(tooltipText(byLabel('close b'))).toBe('Close');
    expect(descriptions()).toHaveLength(2);
  });

  it('ids are injective over text and shortcut, and never hold whitespace', () => {
    const pairs: Array<[string, string | undefined]> = [
      ['a@b', undefined],
      ['a', 'b'],
      ['a b', undefined],
      ['a%20b', undefined],
      ['Command palette', '⌘K'],
      ['Command palette (⌘K)', undefined],
      ['', undefined],
      ['line one\nline two', undefined],
    ];
    const ids = pairs.map(([text, shortcut]) => descriptionId(text, shortcut));

    expect(new Set(ids).size).toBe(pairs.length);
    for (const id of ids) expect(id).not.toMatch(/\s/);
  });

  it('a React-node content keeps its own description, in the same host', async () => {
    await mount(
      <>
        <Tooltip content={<>Rich <strong>text</strong></>}>
          <button type="button" aria-label="rich a">x</button>
        </Tooltip>
        <Tooltip content={<>Rich <strong>text</strong></>}>
          <button type="button" aria-label="rich b">x</button>
        </Tooltip>
      </>,
    );

    const a = byLabel('rich a').getAttribute('aria-describedby');
    expect(a).not.toBe(byLabel('rich b').getAttribute('aria-describedby'));
    expect(accessibleDescription(byLabel('rich a'))).toBe('Rich text');
    expect(document.getElementById(a ?? '')?.parentElement).toBe(host());
  });

  it('survives StrictMode\'s double effects with exactly one live node', async () => {
    await mount(
      <StrictMode>
        <Window />
      </StrictMode>,
    );

    expect(descriptions()).toHaveLength(6);
    expect(accessibleDescription(byLabel('Reject B0'))).toBe('Reject');
  });

  it('re-attaches the host when <body> was emptied under a mounted trigger', async () => {
    await mount(<Tooltip content="Reject"><button type="button" aria-label="Reject B1">r</button></Tooltip>);
    host()?.remove();

    const second = document.body.appendChild(document.createElement('div'));
    await mount(<Tooltip content="Reject"><button type="button" aria-label="Reject B2">r</button></Tooltip>, {
      container: second,
    });

    expect(host()?.parentElement).toBe(document.body);
    expect(accessibleDescription(byLabel('Reject B2'))).toBe('Reject');
    second.remove();
  });

  it('puts a shared node something detached back into the host on its next holder', async () => {
    await mount(<Tooltip content="Reject"><button type="button" aria-label="Reject B1">r</button></Tooltip>);
    document.getElementById(descriptionId('Reject'))?.remove();

    await mount(<Tooltip content="Reject"><button type="button" aria-label="Reject B2">r</button></Tooltip>);

    expect(document.getElementById(descriptionId('Reject'))?.parentElement).toBe(host());
    expect(accessibleDescription(byLabel('Reject B1'))).toBe('Reject');
    expect(accessibleDescription(byLabel('Reject B2'))).toBe('Reject');
  });
});
