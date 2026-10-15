import { useCallback, useEffect, useRef, useState, type ComponentType } from 'react';
import { useExitPresence } from '../../hooks/useExitRetained';
import { OPEN_SHORTCUTS_EVENT, hasOpenModal, registerKeyBinding } from '../../lib/keymap';
import type { ShortcutOverlayProps } from './ShortcutOverlay';

/**
 * The always-mounted, tiny half of the `?` shortcut sheet (audit
 * wow-power-4): it binds `?` in the global keymap scope and listens for the
 * `mip:open-shortcuts` window event (the identity menu and the Lead Queue
 * header dispatch it), then loads the sheet itself on first open, so none of
 * its markup or CSS ships in the initial chunk.
 *
 * The import is awaited here rather than through React.lazy + Suspense: a
 * stale chunk after a deploy must leave the sheet closed, not throw into the
 * shell's error boundary.
 *
 * Exit (motion-01 item 2e): once loaded, the sheet stays mounted while
 * present (useExitPresence on the dialog this Host owns), so a close plays
 * the `.cmdk` fade instead of unmounting it mid-frame.
 */
let loadedOverlay: ComponentType<ShortcutOverlayProps> | null = null;

function loadOverlay(): Promise<ComponentType<ShortcutOverlayProps>> {
  if (loadedOverlay) return Promise.resolve(loadedOverlay);
  return import('./ShortcutOverlay').then((module) => {
    // A vite:preloadError listener that calls preventDefault() resolves the
    // failed import to undefined instead of rejecting.
    if (!module?.ShortcutOverlay) throw new Error('Shortcut sheet chunk unavailable');
    loadedOverlay = module.ShortcutOverlay;
    return loadedOverlay;
  });
}

export function ShortcutOverlayHost() {
  const [Overlay, setOverlay] = useState<ComponentType<ShortcutOverlayProps> | null>(() => loadedOverlay);
  const [open, setOpen] = useState(false);
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const { present } = useExitPresence(open, dialogRef);

  const openSheet = useCallback(() => {
    setOpen(true);
    loadOverlay()
      .then((component) => setOverlay(() => component))
      .catch(() => setOpen(false));
  }, []);
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => registerKeyBinding({
    id: 'shortcut-sheet',
    scope: 'global',
    keys: ['?'],
    description: 'Show keyboard shortcuts',
    // Not over a modal layer: the sheet would stack on a drawer or dialog.
    when: () => !hasOpenModal(),
    run: openSheet,
  }), [openSheet]);

  useEffect(() => {
    window.addEventListener(OPEN_SHORTCUTS_EVENT, openSheet);
    return () => window.removeEventListener(OPEN_SHORTCUTS_EVENT, openSheet);
  }, [openSheet]);

  if (!present || !Overlay) return null;
  return <Overlay open={open} dialogRef={dialogRef} onClose={close} />;
}
