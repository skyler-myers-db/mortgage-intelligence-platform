import { Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import type { GrowthAgentNotificationDraft } from '../types';

interface GrowthAgentDraftPanelProps {
  drafts: GrowthAgentNotificationDraft[];
}

export function GrowthAgentDraftPanel({ drafts }: GrowthAgentDraftPanelProps) {
  if (drafts.length === 0) return null;

  return (
    <div className="surface surface--inset mt-3" aria-label="Watchlist notifications">
      <div className="surface__hdr">
        <Icon name="doc" size={14} className="icon-accent" />
        <div>
          <div className="h-4">Watchlist notifications</div>
          <div className="muted fs-12">Slack alerts and Teams operations briefs from saved watchlist runs.</div>
        </div>
      </div>
      <div className="surface__body">
        <div className="growth-agent__cards">
          {drafts.map((draft) => (
            <article key={draft.draft_id} className="growth-agent-card">
              <div className="growth-agent-card__head">
                <span className="growth-agent-card__icon">
                  <Icon name={draft.channel === 'slack' ? 'chat' : 'doc'} size={16} />
                </span>
                <div>
                  <div className="growth-agent-card__title">
                    {draft.channel === 'slack' ? 'Slack alert' : 'Teams operations brief'}
                  </div>
                  <div className="growth-agent-card__trigger">{draft.title}</div>
                </div>
              </div>
              <div className="chip-row mt-2">
                <Chip variant={draft.generation_mode === 'supervisor' ? 'success' : 'neutral'}>
                  {draft.generator_label ?? 'Governed notification framework'}
                </Chip>
              </div>
              <div className="muted fs-12 mt-2">
                {draft.strategy_summary ?? 'Reviewed internal notification framing.'}
              </div>
              <p
                className={`growth-agent-card__copy${draft.channel === 'teams' ? ' growth-agent-card__copy--structured' : ''}`}
              >
                {draft.body}
              </p>
              <Chip variant="neutral" icon="audit">Status: {draft.status}</Chip>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}
