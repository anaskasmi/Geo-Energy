/**
 * Friendly empty/zero-result state with an optional example CTA (GEO-32 #8). Replaces the bare
 * placeholder text in the results panel when nothing has been scored yet, or when a scored area
 * returned no parcels — always pairing the explanation with a next step.
 */
export interface EmptyStateProps {
  title: string;
  hint: string;
  action?: { label: string; onClick: () => void };
}

export function EmptyState({ title, hint, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <p className="empty-state__title">{title}</p>
      <p className="empty-state__hint">{hint}</p>
      {action && (
        <button type="button" className="panel-btn panel-btn--primary" onClick={action.onClick}>
          {action.label}
        </button>
      )}
    </div>
  );
}
