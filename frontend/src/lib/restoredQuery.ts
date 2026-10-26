/**
 * Whether a query's data is the persisted snapshot (lib/queryPersist) rather
 * than a read this document made (2026-09-21 audit delivery-05, the W5a
 * integrator ruling on restored age): a restored query keeps its dehydrated
 * `dataUpdatedAt`, which predates this document's own start. FetchedAt shows
 * that true age; a surface that would draw a NEW claim from the snapshot (a
 * "rate moved since your last visit" row, a borrower's spread history) holds
 * it back until the fresh read lands.
 */
export function isRestoredSnapshot(dataUpdatedAt: number | null): boolean {
  return dataUpdatedAt !== null && dataUpdatedAt > 0 && dataUpdatedAt < performance.timeOrigin;
}
