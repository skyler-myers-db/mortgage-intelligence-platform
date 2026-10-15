/**
 * The filter omnibox popover (wow-power-6, W5e item 3c; deviation:filter-omnibox).
 * Its own lazy chunk with lib/filterGrammar, loaded on the omnibox's first
 * open (routes/lead-queue.omnibox.tsx). It never imports the transport or
 * the query keys: the route hands the count read down (`countOptions`,
 * lib/apiClients/leadCount.ts), so the initial transport chunk is not split.
 *
 * A WAI-ARIA APG combobox input; the readings as a listbox of chips, each
 * "STATE · TX (from "TX")"; a "Not recognized: …" line; and a polite
 * role=status count region, mounted empty and aria-busy only while the count
 * loads ("1,234 borrowers match", or "Count unavailable"). The count is the
 * audit-free GET /api/leads/count over the recognized readings only (an
 * allowlisted query, lib/apiClients/leadCount.ts): it starts with one
 * recognized token, 200 ms after the typing pauses, aborts a superseded read,
 * keeps the previous number while the next one loads and stops when the
 * popover closes. Enter applies the readings (filters replaced, the column
 * state, sort and direction kept); Escape closes through the escape stack.
 * "Ask Genie instead" appears only when something was not recognized and only
 * PREFILLS the floating Genie composer with the typed text on a click
 * (lib/genieOpen); the person still presses Ask, so the text reaches Genie's
 * guards only then. The typed text is never stored, put in a URL or sent to
 * RUM.
 */
import { useEffect, useEffectEvent, useId, useMemo, useRef, useState, type RefObject } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { leadCountQuery, type LeadCountSource } from '../../lib/leadCountQuery';
import { parseFilterGrammar } from '../../lib/filterGrammar';
import { formatCount } from '../../lib/formatters';
import { openGenie } from '../../lib/genieOpen';
import { pushEscapeLayer } from '../../lib/escapeStack';

export const OMNIBOX_COUNT_DEBOUNCE_MS = 200;
const COUNT_STALE_MS = 60_000;

/** The count status line: empty, a number, or the fixed failure copy. */
function countText(total: number | undefined, failed: boolean): string {
  if (failed) return 'Count unavailable';
  if (total === undefined) return '';
  return `${formatCount(total)} ${total === 1 ? 'borrower matches' : 'borrowers match'}`;
}

export function LeadQueueOmniboxPopover({
  rootRef,
  countOptions,
  onClose,
  onApply,
}: {
  /** The pill and this popover: a pointer-down outside closes. */
  rootRef: RefObject<HTMLElement | null>;
  /** The audit-free count read for an allowlisted query (lib/apiClients/leadCount leadCountQueryOptions). */
  countOptions: LeadCountSource;
  /** `refocus` false: a pointer-down elsewhere keeps the focus it moved. */
  onClose: (refocus?: boolean) => void;
  onApply: (params: URLSearchParams) => void;
}) {
  const [text, setText] = useState('');
  const [countQuery, setCountQuery] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listId = useId();
  const statusId = useId();
  const reading = useMemo(() => parseFilterGrammar(text), [text]);
  const recognized = reading.chips.length > 0;
  const nextQuery = recognized ? leadCountQuery(reading.params) : '';

  useEffect(() => {
    const timer = window.setTimeout(() => setCountQuery(nextQuery), OMNIBOX_COUNT_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [nextQuery]);
  const count = useQuery({
    ...countOptions(countQuery),
    enabled: countQuery !== '',
    placeholderData: keepPreviousData,
    staleTime: COUNT_STALE_MS,
    retry: false,
  });
  const showCount = recognized && countQuery !== '';
  const loading = showCount && count.isFetching;

  const onEscape = useEffectEvent(() => onClose());
  const onOutside = useEffectEvent((event: PointerEvent) => {
    if (event.target instanceof Node && rootRef.current?.contains(event.target)) return;
    onClose(false);
  });
  useEffect(() => pushEscapeLayer(() => {
    onEscape();
  }), []);
  useEffect(() => {
    const listener = (event: PointerEvent) => onOutside(event);
    window.addEventListener('pointerdown', listener);
    return () => window.removeEventListener('pointerdown', listener);
  }, []);
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  return (
    <div className="filter-menu lead-queue-omnibox__panel" role="group" aria-label="Filter leads" data-testid="lead-queue-omnibox-panel">
      <input
        ref={inputRef}
        className="form-input lead-queue-omnibox__input"
        type="text"
        role="combobox"
        aria-label="Filter leads by state, segment, product, status or score"
        aria-autocomplete="list"
        aria-haspopup="listbox"
        aria-expanded={recognized}
        aria-controls={recognized ? listId : undefined}
        aria-describedby={statusId}
        autoComplete="off"
        spellCheck={false}
        placeholder="TX IL heloc aged>7"
        value={text}
        onChange={(event) => setText(event.currentTarget.value)}
        onKeyDown={(event) => {
          if (event.key !== 'Enter') return;
          event.preventDefault();
          if (recognized) onApply(reading.params);
        }}
        data-testid="lead-queue-omnibox-input"
      />
      {recognized && (
        <div id={listId} className="lead-queue-omnibox__chips" role="listbox" aria-label="Filters read from your text">
          {reading.chips.map((chip, index) => (
            <span
              key={`${chip.key}-${chip.value}-${index}`}
              className="filter is-active lead-queue-omnibox__chip"
              role="option"
              aria-selected="false"
            >
              <span className="filter__label">{chip.label}</span>
              <span className="filter__value">{` · ${chip.value} (from "${chip.source}")`}</span>
            </span>
          ))}
        </div>
      )}
      {reading.unrecognized.length > 0 && (
        <p className="lead-queue-omnibox__unrecognized" data-testid="lead-queue-omnibox-unrecognized">
          Not recognized: {reading.unrecognized.join(', ')}
        </p>
      )}
      <p id={statusId} className="lead-queue-omnibox__count" role="status" aria-busy={loading || undefined}>
        {showCount ? countText(count.data?.total_matching, count.isError) : ''}
      </p>
      <div className="lead-queue-omnibox__actions">
        <button
          type="button"
          className="btn btn--primary btn--sm"
          disabled={!recognized}
          onClick={() => onApply(reading.params)}
          data-testid="lead-queue-omnibox-apply"
        >
          Apply
        </button>
        {reading.unrecognized.length > 0 && (
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => {
              openGenie({ prompt: text });
              onClose(false);
            }}
            data-testid="lead-queue-omnibox-ask-genie"
          >
            Ask Genie instead
          </button>
        )}
      </div>
    </div>
  );
}
