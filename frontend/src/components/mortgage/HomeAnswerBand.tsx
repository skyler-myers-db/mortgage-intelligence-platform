import { useMemo } from 'react';
import type { HomeSummary, PortfolioPreview } from '../../types';
import { Icon } from '../Icon';
import { buildPortfolioStory } from '../../lib/portfolioStory';
import { HomeAnswerWho } from './HomeAnswerWho';
import { LastLoginSummary } from './LastLoginSummary';
import { OfferMixBar } from './OfferMixBar';
import './HomeAnswerBand.css';

/**
 * Home's answer band (2026-09-21 audit flow-05 + visual-06). The page asks
 * "Who should we contact, why now, and with what offer?"; this band answers
 * it in three columns — WHO (top five ranked contactable borrowers), WHY NOW
 * (changes since the last login) and WHAT TO OFFER (the offer mix) — under
 * one briefing sentence.
 *
 * It replaces two cards that answered none of the three: "Since your last
 * login" (now the WHY NOW column) and "Your book today" (now the briefing
 * line, the same verified sentence from lib/portfolioStory, held to a 72ch
 * measure). Every figure keeps an evidence path: the briefing's numbers are
 * the KPI cards' own values (each KPI carries its drawer), and every column
 * carries an EvidenceChip.
 *
 * New BEM block `.home-answer` (the prototype has no answer band); its parts
 * reuse prototype primitives: `.surface`, `.chip`, `.score`, `.evidence-chip`.
 */
export function HomeAnswerBand({
  preview,
  previewLoading = false,
  summary,
  summaryLoading = false,
}: {
  preview: PortfolioPreview | null;
  previewLoading?: boolean;
  summary: HomeSummary | null;
  summaryLoading?: boolean;
}) {
  const story = useMemo(() => buildPortfolioStory(preview), [preview]);

  return (
    <section className="surface home-answer" aria-labelledby="home-answer-title">
      <div className="surface__hdr home-answer__hdr">
        <div className="surface__icon"><Icon name="sparkle" size={14} /></div>
        <div className="home-answer__intro">
          <h2 className="h-4" id="home-answer-title">Today&apos;s briefing</h2>
          {previewLoading ? (
            <span className="skeleton home-answer__briefing-skeleton" aria-hidden="true" />
          ) : story.available ? (
            <p className="home-answer__briefing">{story.sentences.join(' ')}</p>
          ) : null}
          {!previewLoading && story.available && !story.allVerified && (
            <div className="home-answer__verdict" role="status">
              <Icon name="info" size={11} />
              <span>Some figures could not be verified against the snapshot — review before presenting.</span>
            </div>
          )}
        </div>
      </div>
      <div className="home-answer__grid">
        <HomeAnswerWho />
        <LastLoginSummary summary={summary} loading={summaryLoading} />
        <OfferMixBar preview={preview} loading={previewLoading} />
      </div>
    </section>
  );
}
