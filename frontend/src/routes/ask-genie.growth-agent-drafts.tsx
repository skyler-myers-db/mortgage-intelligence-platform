import { Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import type { GrowthAgentNotificationDraft } from '../types';

interface GrowthAgentDraftPanelProps {
  drafts: GrowthAgentNotificationDraft[];
}

export function GrowthAgentDraftPanel({ drafts }: GrowthAgentDraftPanelProps) {
  if (drafts.length === 0) return null;

  return (
    <div className="surface surface--inset mt-3" aria-label="Growth Agent notification drafts">
      <div className="surface__hdr">
        <Icon name="doc" size={14} className="icon-accent" />
        <div>
          <div className="h-4">Draft-only handoff</div>
          <div className="muted fs-12">Review Slack and Teams copy before any external action.</div>
        </div>
        <div className="spacer" />
        <Chip variant="warning" icon="shield">Not sent</Chip>
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
                    {draft.channel === 'slack' ? 'Slack draft' : 'Teams draft'}
                  </div>
                  <div className="growth-agent-card__trigger">{draft.title}</div>
                </div>
              </div>
              <p className="growth-agent-card__copy">{draft.body}</p>
              <Chip variant="neutral" icon="audit">Status: {draft.status}</Chip>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}
