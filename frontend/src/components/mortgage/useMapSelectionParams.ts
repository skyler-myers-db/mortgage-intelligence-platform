/**
 * useMapSelectionParams — the geography map's selection, owned by the URL
 * (audit dataviz-04). Home and Segment Intelligence pass the pair to
 * `<USChoroplethMap selection onSelectionChange>`, so the drill, the route's
 * "Clear geography", Back / Forward and a shared link agree.
 *
 * Writes keep every other search param and never reset the page scroll:
 * `useMainScroll` keeps the offset on a same-pathname push.
 */
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router';
import {
  MAP_STATE_PARAM,
  MAP_ZIP_PARAM,
  parseMapSelection,
  sameMapSelection,
  withMapSelection,
  type MapSelection,
  type MapSelectionChangeOptions,
} from './USChoroplethMap.selection';

export type SetMapSelection = (next: MapSelection, options?: MapSelectionChangeOptions) => void;

export function useMapSelectionParams(): [MapSelection, SetMapSelection] {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawState = searchParams.get(MAP_STATE_PARAM);
  const rawZip = searchParams.get(MAP_ZIP_PARAM);
  const selection = useMemo(() => parseMapSelection(rawState, rawZip), [rawState, rawZip]);
  const setSelection = useCallback<SetMapSelection>(
    (next, options) => {
      // A no-op click must not push a duplicate history entry.
      if (sameMapSelection(parseMapSelection(next.state, next.zip), selection)) return;
      setSearchParams((current) => withMapSelection(current, next), { replace: options?.replace ?? false });
    },
    [selection, setSearchParams],
  );
  return [selection, setSelection];
}
