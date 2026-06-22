import { useState } from 'react';
import type { GenieActionSuggestion } from '../../types';
import { safeSegmentName } from '../../lib/segmentMetadata';
import { Icon } from '../Icon';

function segmentPreviewLabel(value: unknown): string | null {
  return safeSegmentName(value);
}

export function actionPreview(action: GenieActionSuggestion): string[] {
  const criteria = action.criteria ?? {};
  const filters = criteria.result_filters && typeof criteria.result_filters === 'object'
    ? criteria.result_filters as Record<string, unknown>
    : {};
  const preview: string[] = [];
  const zips = Array.isArray(filters.zips) ? filters.zips : [];
  if (zips.length > 0) preview.push(`${zips.length} ZIP${zips.length === 1 ? '' : 's'}: ${zips.slice(0, 5).join(', ')}${zips.length > 5 ? '…' : ''}`);
  const states = Array.isArray(filters.states) ? filters.states : [];
  if (states.length > 0) preview.push(`States: ${states.join(', ')}`);
  const segments = Array.isArray(filters.segment_codes) ? filters.segment_codes : [];
  if (segments.length > 0) {
    const labels = segments
      .map(segmentPreviewLabel)
      .filter((label): label is string => Boolean(label));
    const uniqueLabels = [...new Set(labels)];
    if (uniqueLabels.length > 0) {
      preview.push(
        `Segments: ${uniqueLabels.join(', ')} (${filters.segment_mode === 'all' ? 'all selected segments' : 'any selected segment'})`,
      );
    }
  }
  if (typeof filters.target_lender_ref === 'string' && filters.target_lender_ref.length > 0) {
    preview.push(`Target lien holder: ${filters.target_lender_ref}`);
  }
  if (action.borrower_ids && action.borrower_ids.length > 0) {
    preview.push(`${action.borrower_ids.length} borrower${action.borrower_ids.length === 1 ? '' : 's'} bound by ID`);
  }
  if (typeof criteria.row_count === 'number') preview.push(`${criteria.row_count.toLocaleString()} result row${criteria.row_count === 1 ? '' : 's'}`);
  return preview;
}

export function GenieActions({
  actions,
  onAction,
}: {
  actions: GenieActionSuggestion[];
  onAction: (action: GenieActionSuggestion) => void | Promise<void>;
}) {
  const [confirmActionId, setConfirmActionId] = useState<string | null>(null);
  const [pendingActionId, setPendingActionId] = useState<string | null>(null);

  return (
    <div className="genie-actions">
      <div className="eyebrow">Governed actions</div>
      {actions.slice(0, 5).map((action) => {
        const confirming = confirmActionId === action.id;
        const pending = pendingActionId === action.id;
        const preview = actionPreview(action);
        return (
          <div key={action.id} className="genie-action">
            <div className="genie-action__body">
              <div className="genie-action__label">{action.label}</div>
              <div className="genie-action__desc">{action.description}</div>
              {preview.length > 0 && (
                <div className="genie-action__preview">
                  {preview.slice(0, 4).map((item) => (
                    <span key={item} className="chip chip--neutral">{item}</span>
                  ))}
                </div>
              )}
            </div>
            {confirming ? (
              <div className="genie-action__confirm">
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  disabled={Boolean(pendingActionId)}
                  aria-label={`Confirm ${action.label}`}
                  onClick={async () => {
                    setPendingActionId(action.id);
                    setConfirmActionId(null);
                    try {
                      await onAction(action);
                    } finally {
                      setPendingActionId(null);
                    }
                  }}
                >
                  {pending ? 'Recording…' : 'Confirm'}
                </button>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={Boolean(pendingActionId)}
                  aria-label={`Cancel ${action.label}`}
                  onClick={() => setConfirmActionId(null)}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                disabled={Boolean(pendingActionId)}
                aria-label={`Run ${action.label}`}
                onClick={() => setConfirmActionId(action.id)}
              >
                <Icon name="play" size={12} />
                {pending ? 'Recording…' : 'Run'}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
