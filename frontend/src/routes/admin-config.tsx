const SECTIONS = [
  'Market rate assumptions',
  'Offer eligibility thresholds',
  'Suppression placeholders',
  'Data source readiness',
  'Demo lender config',
  'Audit settings',
];

export default function AdminConfig() {
  return (
    <section className="page grid" style={{ gap: 20 }}>
      <div>
        <div className="eyebrow">Admin Config</div>
        <h1 className="h1">Rules, thresholds, and demo assumptions</h1>
      </div>
      <div className="grid grid-3">
        {SECTIONS.map((x) => (
          <div className="card card-body" key={x}>
            <strong>{x}</strong>
            <p className="muted">Editable configuration placeholder.</p>
          </div>
        ))}
      </div>
    </section>
  );
}
