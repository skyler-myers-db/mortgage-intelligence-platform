import { openShortcutOverlay } from '../../lib/keymap';

/**
 * The table header's keyboard line (audit tables-03, wow-power-4): the few
 * keys that matter, as keycaps, and a button that opens the `?` sheet, which
 * lists the rest. With single-key shortcuts switched off in the Console it
 * says so instead of advertising keys that do nothing.
 */
export function LeadTableKeyboardHint({
  singleKeysOn,
  approverActive,
}: {
  singleKeysOn: boolean;
  approverActive: boolean;
}) {
  return (
    <>
      {singleKeysOn ? (
        <>
          With the table focused: <kbd>J</kbd> <kbd>K</kbd> move, <kbd>Enter</kbd> opens a row
          {approverActive && <>, <kbd>A</kbd> reviews the outreach before approval, <kbd>R</kbd> rejects</>}
          .
        </>
      ) : (
        <>Single-key shortcuts are off (Console).</>
      )}
      {' '}
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        onClick={openShortcutOverlay}
        aria-keyshortcuts={singleKeysOn ? '?' : undefined}
        data-testid="lead-shortcuts-open"
      >
        Keyboard shortcuts
      </button>
    </>
  );
}
