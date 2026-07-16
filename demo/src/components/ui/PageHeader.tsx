import type { ReactNode } from "react";

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="nc-pageheader">
      <div>
        <h1 className="nc-pageheader__title">{title}</h1>
        {subtitle && <p className="nc-pageheader__sub">{subtitle}</p>}
      </div>
      {actions && <div className="nc-pageheader__actions">{actions}</div>}
    </div>
  );
}
