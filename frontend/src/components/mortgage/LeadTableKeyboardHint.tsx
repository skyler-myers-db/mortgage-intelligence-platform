import { openShortcutOverlay } from '../../lib/keymap';

/**
 * The table header's keyboard line (audit tables-03, wow-power-4): the few
 * keys that matter, as keycaps. With single-key shortcuts switched off in
 * the Console it says so instead of advertising keys that do nothing.
 *
 * One short line, like the hint it replaced: the header must not grow, or
 * the table scroller loses rows at 1440x900 (queue-layout fixture spec).
 * The button that opens the `?` sheet sits in the header actions row,
 * beside the view and export controls of the same height.
 */
export function LeadTableKeyboardHint({
  singleKeysOn,
  approverActive,
}: {
  singleKeysOn: boolean;
  approverActive: boolean;
}) {
  if (!singleKeysOn) return <>Single-key shortcuts are off (Console).</>;
  return (
    <>
      <kbd>J</kbd> <kbd>K</kbd> move, <kbd>Enter</kbd> opens
      {approverActive && <>, <kbd>A</kbd> reviews then approves, <kbd>R</kbd> rejects</>}
      {' '}in the focused table.
    </>
  );
}

/** Opens the `?` sheet, which lists every key the page has bound. */
export function LeadTableShortcutsButton({ singleKeysOn }: { singleKeysOn: boolean }) {
  return (
    <button
      type="button"
      className="btn btn--ghost btn--sm"
      onClick={openShortcutOverlay}
      aria-label="Keyboard shortcuts"
      aria-keyshortcuts={singleKeysOn ? '?' : undefined}
      data-testid="lead-shortcuts-open"
    >
      Shortcuts
    </button>
  );
}
