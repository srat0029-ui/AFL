import type { ReactNode } from "react";

/** A helpful empty state that tells the user what to do next, never just
 * "No data." Optionally carries a call-to-action button. */
function EmptyState({ title, description, action }: { title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <strong style={{ display: "block", color: "var(--text-h)", marginBottom: description ? "0.3rem" : 0 }}>{title}</strong>
      {description && <div>{description}</div>}
      {action && <div style={{ marginTop: "0.75rem" }}>{action}</div>}
    </div>
  );
}

export default EmptyState;
