import { Skeleton } from '../components/ui/Skeleton';

export function LeadQueueTableSkeleton() {
  return (
    <div className="surface lead-queue-skeleton mb-grid" aria-busy="true" role="status">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <Skeleton width={28} height={28} rounded="md" />
          <div>
            <div className="h-4">Loading ranked borrowers</div>
            <div className="mt-2">
              <span className="muted fs-12">Fetching the current queue with the selected filters.</span>
            </div>
          </div>
        </div>
        <Skeleton width={120} height={32} rounded="md" />
      </div>
      <div className="surface__body">
        <div className="lead-queue-skeleton__table">
          {Array.from({ length: 6 }).map((_, row) => (
            <div key={row} className="lead-queue-skeleton__row">
              {Array.from({ length: 9 }).map((__, col) => (
                <Skeleton
                  key={col}
                  width={col === 0 ? 18 : col === 1 ? 120 : col === 8 ? 84 : '100%'}
                  height={col === 0 ? 18 : 14}
                  rounded={col === 0 ? 'sm' : 'pill'}
                />
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
