export function KpiCard({ label, value, delta }: { label: string; value: string; delta?: string }) {
  return (
    <div className="card kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value num">{value}</div>
      {delta && <div className="delta">▲ {delta}</div>}
      <button className="evidence-chip" style={{ marginTop: 12 }}>source</button>
    </div>
  );
}
