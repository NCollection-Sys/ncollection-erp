import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

/* ---------- Button ---------- */
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost";
  size?: "md" | "sm";
  block?: boolean;
  icon?: IconName;
  children?: ReactNode;
};

export function Button({
  variant = "primary",
  size = "md",
  block,
  icon,
  children,
  className = "",
  ...rest
}: ButtonProps) {
  const cls = [
    "nc-btn",
    `nc-btn--${variant}`,
    size === "sm" ? "nc-btn--sm" : "",
    block ? "nc-btn--block" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button className={cls} {...rest}>
      {icon && <Icon name={icon} size={size === "sm" ? 15 : 17} />}
      {children}
    </button>
  );
}

/* ---------- Card ---------- */
export function Card({
  title,
  action,
  children,
  className = "",
  bodyClassName = "",
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`nc-card ${className}`}>
      {(title || action) && (
        <header className="nc-card__head">
          <span className="nc-card__title">{title}</span>
          {action}
        </header>
      )}
      <div className={`nc-card__body ${bodyClassName}`}>{children}</div>
    </section>
  );
}

/* ---------- Badge ---------- */
type BadgeTone = "success" | "warning" | "danger" | "info" | "neutral";
export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: BadgeTone;
  children: ReactNode;
}) {
  return <span className={`nc-badge nc-badge--${tone}`}>{children}</span>;
}

/* ---------- Trend chip ---------- */
export function Trend({ value }: { value: number }) {
  const up = value >= 0;
  return (
    <span className={`nc-trend ${up ? "nc-trend--up" : "nc-trend--down"}`}>
      <Icon name={up ? "arrowUp" : "arrowDown"} size={13} strokeWidth={2.4} />
      {Math.abs(value).toFixed(1)}%
    </span>
  );
}

/* ---------- Avatar ---------- */
export function Avatar({
  name,
  size = 34,
}: {
  name: string;
  size?: number;
}) {
  const initials = name
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <span
      className="nc-avatar"
      style={{ width: size, height: size, fontSize: size * 0.4 }}
    >
      {initials}
    </span>
  );
}

/* ---------- Field ---------- */
export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="nc-field">
      <label>{label}</label>
      {children}
    </div>
  );
}
