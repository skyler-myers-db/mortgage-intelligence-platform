/**
 * The tooltip descriptions' shared store (deviation:tooltip; post-W5
 * tooltip-render-cost). Every ui/Tooltip trigger names a hidden description
 * in its `aria-describedby`; this module keeps those descriptions cheap.
 *
 *  - ONE node per distinct description. A trigger whose content is plain
 *    text (a string or a number) names `descriptionId(text, shortcut)`, a
 *    pure function of the text, so every trigger with the same text names
 *    the same node: a Lead Queue row's score, signal-strength and Reject
 *    descriptions are one node each for the whole table. `retainDescription`
 *    creates the node on its first user and removes it with its last
 *    (ref-counted), so a virtual-window shift adds a node only for a text no
 *    mounted trigger carried, e.g. a new row's property ref.
 *  - ONE hidden host. Every description, shared or not (a trigger whose
 *    content is a React node portals its own), lives in a single
 *    `<div hidden data-tooltip-descriptions>` at the end of <body>. Its
 *    descendants are never styled (a `display: none` subtree), so adding
 *    or removing one costs no style recalc; each description also keeps its
 *    own `hidden`. The host leaves <body> with its last user.
 *
 * Accessibility is unchanged: `aria-describedby` may name hidden content,
 * and the accessible description is computed from the referenced node's
 * text whether it or an ancestor hides it (accname 1.2, "hidden and
 * referenced"). The node's shape is the one ui/tooltipController.ts reads:
 * `<span hidden id><span data-tooltip-text>text</span> (shortcut)</span>`.
 *
 * Before this (W5e), each trigger portalled its own description straight
 * into <body>: seven per queue row, ~123 at rest, and each virtual-window
 * shift mounted and unmounted 35-42 of them, every one a styled element
 * (~950 over focus-clearance-cost's scripted scroll).
 */

export const DESCRIPTION_HOST_ATTRIBUTE = 'data-tooltip-descriptions';

interface Entry {
  node: HTMLSpanElement;
  users: number;
  /** This entry's hold on the host. */
  releaseHost: () => void;
}

const entries = new Map<string, Entry>();
let host: HTMLDivElement | null = null;
/** Shared entries plus mounted per-trigger (React-node) descriptions. */
let hostUsers = 0;

/**
 * The id of the shared description for `text` (and `shortcut`): pure and
 * injective. encodeURIComponent never emits whitespace (ids must have none,
 * `aria-describedby` is a space-separated list) and always escapes `@`, so
 * the `@` before an encoded shortcut cannot come from the text: 'a@b' with
 * no shortcut is `…a%40b`, 'a' with shortcut 'b' is `…a@b`. A per-trigger
 * description's id (`tooltip-desc_r_…_`, React's useId) never starts with
 * this prefix's hyphen.
 */
export function descriptionId(text: string, shortcut?: string): string {
  const id = `tooltip-desc-${encodeURIComponent(text)}`;
  return shortcut ? `${id}@${encodeURIComponent(shortcut)}` : id;
}

/**
 * The host every description lives in, created on first use. Render may
 * call this (a React-node description portals into it); it never attaches
 * the host: `retainHost` does, from an effect.
 */
export function descriptionHost(): HTMLDivElement {
  if (!host) {
    host = document.createElement('div');
    host.hidden = true;
    host.setAttribute(DESCRIPTION_HOST_ATTRIBUTE, '');
  }
  return host;
}

/** Keep the host in <body> while anything uses it; returns the release. */
export function retainHost(): () => void {
  const element = descriptionHost();
  hostUsers += 1;
  // Re-attached when something emptied <body> under it (a test's teardown).
  if (!element.isConnected) document.body.append(element);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    hostUsers -= 1;
    if (hostUsers === 0) element.remove();
  };
}

function createNode(id: string, text: string, shortcut: string | undefined): HTMLSpanElement {
  const node = document.createElement('span');
  node.hidden = true;
  node.id = id;
  const slot = document.createElement('span');
  slot.setAttribute('data-tooltip-text', '');
  slot.textContent = text;
  node.append(slot);
  if (shortcut) node.append(` (${shortcut})`);
  return node;
}

/**
 * Hold the shared description for `text` (+ `shortcut`) under
 * `descriptionId(text, shortcut)`; returns the release. The first holder
 * creates the node, the last one's release removes it.
 */
export function retainDescription(text: string, shortcut?: string): () => void {
  const id = descriptionId(text, shortcut);
  let entry = entries.get(id);
  if (entry) {
    // Re-attached when something emptied <body> under it (a test's teardown).
    if (!entry.node.isConnected) document.body.append(descriptionHost());
  } else {
    entry = { node: createNode(id, text, shortcut), users: 0, releaseHost: retainHost() };
    entries.set(id, entry);
    descriptionHost().append(entry.node);
  }
  const held = entry;
  held.users += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    held.users -= 1;
    if (held.users > 0) return;
    entries.delete(id);
    held.node.remove();
    held.releaseHost();
  };
}
