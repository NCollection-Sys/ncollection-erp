/*
 * NCollection brand logo — a monogram mark + wordmark, drawn in SVG/CSS
 * (no external image). The mark is an "N" formed from two uprights and a
 * diagonal inside a rounded tile with a subtle blue gradient; the wordmark
 * sets "NCollection" with a lighter "ERP" suffix. This is the demo's brand
 * signature and is intended to become the real logo direction.
 */

type LogoProps = {
  /** monogram tile size in px */
  size?: number;
  /** show the "NCollection ERP" wordmark next to the mark */
  showWordmark?: boolean;
  /** render light text (for dark grounds like the sidebar/login) */
  onDark?: boolean;
};

export function LogoMark({ size = 34 }: { size?: number }) {
  const id = "nclogo";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 44 44"
      role="img"
      aria-label="NCollection"
    >
      <defs>
        <linearGradient id={`${id}-g`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#2d7ab7" />
          <stop offset="1" stopColor="#1f5f8f" />
        </linearGradient>
      </defs>
      <rect x="0" y="0" width="44" height="44" rx="11" fill={`url(#${id}-g)`} />
      {/* N monogram: two uprights + diagonal */}
      <path
        d="M13 31V13.5c0-.4.5-.6.8-.3L31 30.5"
        fill="none"
        stroke="#ffffff"
        strokeWidth="3.4"
        strokeLinecap="round"
      />
      <path
        d="M13 13v18M31 13v18"
        fill="none"
        stroke="#ffffff"
        strokeWidth="3.4"
        strokeLinecap="round"
        opacity="0.92"
      />
      {/* accent dot echoing the KPI accent */}
      <circle cx="33.5" cy="11" r="3" fill="#c6ddf0" />
    </svg>
  );
}

export function Logo({ size = 34, showWordmark = true, onDark = false }: LogoProps) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        userSelect: "none",
      }}
    >
      <LogoMark size={size} />
      {showWordmark && (
        <span
          style={{
            display: "inline-flex",
            alignItems: "baseline",
            gap: 5,
            fontSize: size * 0.5,
            fontWeight: 700,
            letterSpacing: "-0.02em",
            lineHeight: 1,
            color: onDark ? "#f2f6fb" : "var(--nc-text)",
          }}
        >
          NCollection
          <span
            style={{
              fontSize: size * 0.3,
              fontWeight: 600,
              letterSpacing: "0.08em",
              color: onDark ? "#8fb6d6" : "var(--nc-secondary)",
              textTransform: "uppercase",
            }}
          >
            ERP
          </span>
        </span>
      )}
    </span>
  );
}
