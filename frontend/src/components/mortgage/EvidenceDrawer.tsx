export function EvidenceDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <aside className={`drawer ${open ? '' : 'hidden'}`} aria-label="Evidence drawer">
      <div className="card-header">
        <div>
          <div className="eyebrow">Data source & lineage</div>
          <strong>Cotality source evidence</strong>
        </div>
        <button className="button ghost" onClick={onClose}>Close</button>
      </div>
      <div className="card-body grid">
        <p className="muted">Every recommendation traces to Cotality public records, voluntary lien, mortgage market analytics, CLIP, Owner Link, listing, permit, AVM, or HPI signals.</p>
        {['cotality.public_records.deed_mortgage', 'cotality.liens.voluntary_lien', 'cotality.owner_link.customer_360', 'mip_demo.gold.evidence_events'].map((x, i) => (
          <div key={x} className="card card-body">
            <div className="eyebrow">Layer {i + 1}</div>
            <div className="mono">{x}</div>
          </div>
        ))}
      </div>
    </aside>
  );
}
