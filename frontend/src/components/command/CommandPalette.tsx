import { useCallback, useEffect, useRef, useState, type ComponentType } from 'react';
import { hasOpenModal, registerKeyBinding } from '../../lib/keymap';
import { createIdlePreloader } from '../../lib/prefetch';
import { ShortcutOverlayHost } from './ShortcutOverlayHost';

/**
 * The shell's keyboard launchers: the ⌘K palette and the `?` shortcut sheet
 * host (the sheet itself is a lazy chunk). One mount point in AppShell.
 *
 * The palette is a shell HOST around a lazy dialog (audit 2026-09-21
 * `bundle-04` items 1-2): this module keeps the `open` state, the ⌘K binding
 * and the preloads, so the first paint no longer carries the palette's
 * surface, its borrower search and its action registry
 * (CommandPaletteDialog.tsx). The dialog chunk loads:
 *   - on idle, once the shell has mounted (a no-op under the idle
 *     preloader's rules, e.g. Save-Data);
 *   - on the first Meta or Control keydown (the start of ⌘K / Ctrl+K), from
 *     one self-removing capture listener;
 *   - at the latest on ⌘K itself, which opens the palette as soon as it
 *     arrives. Once loaded the dialog stays mounted, so it plays its exit.
 *
 * The import is awaited here, as ShortcutOverlayHost does, rather than
 * rendered through React.lazy + Suspense: AppShell has no boundary around
 * <CommandPalette/>, so a stale chunk after a deploy must leave the palette
 * closed, not throw into the root.
 */

export interface CommandPaletteDialogProps {
  open: boolean;
  onClose: () => void;
}

let loadedDialog: ComponentType<CommandPaletteDialogProps> | null = null;

/** Load (once) and return the dialog component. Exported for tests. */
export function loadCommandPaletteDialog(): Promise<ComponentType<CommandPaletteDialogProps>> {
  if (loadedDialog) return Promise.resolve(loadedDialog);
  return import('./CommandPaletteDialog').then((module) => {
    // A vite:preloadError listener that calls preventDefault() resolves the
    // failed import to undefined instead of rejecting.
    if (!module?.CommandPaletteDialog) throw new Error('Command palette chunk unavailable');
    loadedDialog = module.CommandPaletteDialog;
    return loadedDialog;
  });
}

function preloadCommandPaletteDialog(): void {
  loadCommandPaletteDialog().catch(() => {
    // A speculative preload never changes behaviour; ⌘K retries the import.
  });
}

const preloadDialogOnIdle = createIdlePreloader(() => loadCommandPaletteDialog(), 5000);

export function CommandPalette() {
  return (
    <>
      <ShortcutOverlayHost />
      <CommandPaletteHost />
    </>
  );
}

function CommandPaletteHost() {
  const [Dialog, setDialog] = useState<ComponentType<CommandPaletteDialogProps> | null>(() => loadedDialog);
  const [open, setOpen] = useState(false);

  const close = useCallback(() => {
    setOpen(false);
  }, []);
  const openPalette = useCallback(() => {
    setOpen(true);
    loadCommandPaletteDialog()
      .then((component) => setDialog(() => component))
      // A chunk that cannot load leaves the palette closed.
      .catch(() => setOpen(false));
  }, []);

  // The global listener is bound once, so it can't read `open` from a stale
  // closure — track the latest value in a ref (updated in an effect, never
  // during render).
  const openRef = useRef(open);
  useEffect(() => {
    openRef.current = open;
  }, [open]);

  // Global ⌘K / Ctrl+K toggle, with symmetric teardown on both edges. A
  // modifier chord in the shared keymap registry (audit wow-power-4): it
  // works while typing and stays on when single-key shortcuts are off.
  useEffect(() => registerKeyBinding({
    id: 'command-palette',
    scope: 'global',
    keys: ['Mod+K'],
    description: 'Open or close the command palette',
    allowInEditable: true,
    // Never open over ANY modal layer: every modal surface is a native
    // <dialog> (the drawers, the approve review, "Leave without saving?",
    // the ? sheet, the session dialog), and under one the palette would open
    // inert and unseen and swallow the next Escape. Closing an open palette
    // still works.
    when: () => openRef.current || !hasOpenModal(),
    run: () => {
      if (openRef.current) close();
      else openPalette();
    },
  }), [close, openPalette]);

  // Warm the dialog chunk: on idle, and on the first Meta / Control keydown.
  useEffect(() => {
    if (loadedDialog) return undefined;
    const cancelIdle = preloadDialogOnIdle();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Meta' && event.key !== 'Control' && !event.metaKey && !event.ctrlKey) return;
      window.removeEventListener('keydown', onKeyDown, true);
      preloadCommandPaletteDialog();
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => {
      cancelIdle();
      window.removeEventListener('keydown', onKeyDown, true);
    };
  }, []);

  if (!Dialog) return null;
  return <Dialog open={open} onClose={close} />;
}
