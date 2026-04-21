import { Link } from 'react-router-dom';
import type { LeadSummary } from '../../types';

export function LeadTable({ leads }: { leads: LeadSummary[] }) {
  return (
    <div className="card" style={{ overflow: 'hidden' }}>
      <div className="card-header">
        <strong>AI-prioritized lead queue</strong>
        <span className="chip warning">Human approval required</span>
      </div>
      <table>
        <thead>
          <tr><th>Borrower</th><th>Zip</th><th>Segments</th><th>Offer</th><th>Why now</th><th>Score</th><th>Action</th></tr>
        </thead>
        <tbody>
          {leads.map((lead) => (
            <tr key={lead.borrower_id}>
              <td><strong style={{ color: 'var(--text-1)' }}>{lead.display_name}</strong><div className="mono muted">{lead.borrower_id}</div></td>
              <td className="mono">{lead.zip}</td>
              <td>{lead.segment_codes.map((s) => <span className="chip" key={s} style={{ marginRight: 4 }}>{s}</span>)}</td>
              <td>{lead.recommended_offer}</td>
              <td>{lead.why_now}<br /><button className="evidence-chip">evidence</button></td>
              <td><span className="score">{lead.opportunity_score}</span></td>
              <td><Link className="button" to={`/borrower-360/${lead.borrower_id}`}>Open 360</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
