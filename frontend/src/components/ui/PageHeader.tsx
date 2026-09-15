import type { ReactNode } from "react";

/** Every primary page should open with one of these: a small eyebrow label
 * (what section this belongs to), a clear title, a one-sentence plain-English
 * explanation of what the page is for, and optional actions on the right. */
function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="pg-header">
      <div className="pg-header__title-row">
        <div>
          {eyebrow && <span className="pg-header__eyebrow">{eyebrow}</span>}
          <h1 className="page-title">{title}</h1>
        </div>
        {actions && <div className="pg-header__actions">{actions}</div>}
      </div>
      {description && <p className="page-subtitle">{description}</p>}
    </header>
  );
}

export default PageHeader;
