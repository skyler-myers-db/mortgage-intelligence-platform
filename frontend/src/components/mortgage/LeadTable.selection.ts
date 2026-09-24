/**
 * Row-selection arithmetic for the ranked-borrower table (audit tables-07).
 * Pure, so the rules are pinned without a DOM:
 *
 *   - rangeIds: Shift-click (and Shift+X) select every row between the anchor
 *     (the last plain toggle) and the target, in on-screen order, limited to
 *     rows that may be selected.
 *   - pruneTo: the selection a render may act on is the stored selection
 *     intersected with the rows on screen, so a row a filter change took off
 *     screen can never be counted, exported, assigned or approved.
 */

/**
 * The selectable ids from `anchor` to `target` inclusive, in `orderedIds`
 * order (either direction). Without a usable anchor it is just the target.
 */
export function rangeIds(
  orderedIds: readonly string[],
  anchor: string | null,
  target: string,
  selectable: ReadonlySet<string>,
): string[] {
  const to = orderedIds.indexOf(target);
  if (to < 0) return [];
  const from = anchor === null ? -1 : orderedIds.indexOf(anchor);
  if (from < 0) return selectable.has(target) ? [target] : [];
  const [start, end] = from <= to ? [from, to] : [to, from];
  return orderedIds.slice(start, end + 1).filter((id) => selectable.has(id));
}

/** `selected` limited to `currentIds`, as a new set (the input is not changed). */
export function pruneTo(selected: ReadonlySet<string>, currentIds: ReadonlySet<string>): Set<string> {
  const kept = new Set<string>();
  for (const id of selected) {
    if (currentIds.has(id)) kept.add(id);
  }
  return kept;
}
