/* Formatting helpers — AED currency, GCC market. */

export function aed(value: number, opts: { compact?: boolean } = {}): string {
  if (opts.compact && Math.abs(value) >= 1000) {
    const k = value / 1000;
    return `AED ${k % 1 === 0 ? k : k.toFixed(1)}k`;
  }
  return `AED ${value.toLocaleString("en-AE", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })}`;
}

export function num(value: number): string {
  return value.toLocaleString("en-AE");
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}
