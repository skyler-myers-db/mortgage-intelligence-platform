import { lazyModule, useLazyModule } from '../mortgage/useLazyModule';

/**
 * The stale-data note's code-split entry (audit delivery-06): a retained
 * value is rare, so no route or map chunk carries the note until one shows
 * up. The note loads the first time a read reports `lastGoodAt` and renders
 * nothing until then (and nothing if its chunk cannot load: it is a marker,
 * never a gate). The DeltaExplainer chunk, already lazy, imports the note
 * directly.
 */
const STALE_DATA_NOTE = lazyModule(() => import('./StaleDataNote'));

export function LazyStaleDataNote({ lastGoodAt, compact = false }: { lastGoodAt: string | number | null; compact?: boolean }) {
  const Note = useLazyModule(STALE_DATA_NOTE, lastGoodAt !== null).module?.StaleDataNote ?? null;
  return lastGoodAt !== null && Note ? <Note lastGoodAt={lastGoodAt} compact={compact} /> : null;
}
