#!/usr/bin/env python3
"""P3-T03 — render the load-curve CSV to a committable SVG (no deps).

Reads scripts/perf/results/load_curve.csv (vus,read_p95_ms,rps,err_pct) and
writes an SVG with two series vs concurrent VUs: read p95 latency (left axis,
with the 500ms §9 budget line) and throughput RPS (right axis).

    python3 scripts/perf/plot_load_curve.py [csv] [out.svg]
"""
import csv
import sys

CSV = sys.argv[1] if len(sys.argv) > 1 else "scripts/perf/results/load_curve.csv"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs/perf/P3-T03_load_curve.svg"

W, H = 720, 380
PAD_L, PAD_R, PAD_T, PAD_B = 64, 64, 40, 56
PW, PH = W - PAD_L - PAD_R, H - PAD_T - PAD_B
BUDGET = 500  # §9 interactive p95 budget (ms)


def read_rows(path):
    with open(path, newline="") as fh:
        return [
            (float(r["vus"]), float(r["read_p95_ms"]), float(r["rps"]))
            for r in csv.DictReader(fh)
        ]


def main():
    rows = read_rows(CSV)
    if not rows:
        sys.exit("no rows in " + CSV)
    xs = [r[0] for r in rows]
    p95 = [r[1] for r in rows]
    rps = [r[2] for r in rows]
    xmax = max(xs) * 1.05
    ymax_ms = max(max(p95), BUDGET) * 1.15
    ymax_rps = max(rps) * 1.2 or 1

    def px(x):
        return PAD_L + (x / xmax) * PW

    def py_ms(y):
        return PAD_T + PH - (y / ymax_ms) * PH

    def py_rps(y):
        return PAD_T + PH - (y / ymax_rps) * PH

    def poly(xs_, ys_, fy):
        return " ".join(f"{px(x):.1f},{fy(y):.1f}" for x, y in zip(xs_, ys_))

    s = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'font-family="system-ui,sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#0e1116"/>',
        f'<text x="{PAD_L}" y="24" fill="#e8eaf0" font-size="15" font-weight="700">'
        f'P3-T03 load curve — read p95 &amp; throughput vs concurrent users</text>',
        # plot frame
        f'<rect x="{PAD_L}" y="{PAD_T}" width="{PW}" height="{PH}" fill="#151a21" '
        f'stroke="#2a313c"/>',
    ]
    # y grid (ms)
    for i in range(0, 6):
        y = ymax_ms * i / 5
        yy = py_ms(y)
        s.append(f'<line x1="{PAD_L}" y1="{yy:.1f}" x2="{PAD_L+PW}" y2="{yy:.1f}" '
                 f'stroke="#222831"/>')
        s.append(f'<text x="{PAD_L-8}" y="{yy+4:.1f}" fill="#8b93a1" font-size="11" '
                 f'text-anchor="end">{y:.0f}</text>')
    # x ticks (VUs)
    for x in xs:
        s.append(f'<text x="{px(x):.1f}" y="{PAD_T+PH+18:.1f}" fill="#8b93a1" '
                 f'font-size="11" text-anchor="middle">{x:.0f}</text>')
    s.append(f'<text x="{PAD_L+PW/2:.0f}" y="{H-14}" fill="#a9b1bd" font-size="12" '
             f'text-anchor="middle">concurrent virtual users (across 3 tenants)</text>')
    s.append(f'<text x="18" y="{PAD_T+PH/2:.0f}" fill="#5fd6e3" font-size="12" '
             f'text-anchor="middle" transform="rotate(-90 18 {PAD_T+PH/2:.0f})">'
             f'read p95 (ms)</text>')
    s.append(f'<text x="{W-16}" y="{PAD_T+PH/2:.0f}" fill="#e0a83e" font-size="12" '
             f'text-anchor="middle" transform="rotate(90 {W-16} {PAD_T+PH/2:.0f})">'
             f'throughput (req/s)</text>')
    # 500ms budget line
    by = py_ms(BUDGET)
    s.append(f'<line x1="{PAD_L}" y1="{by:.1f}" x2="{PAD_L+PW}" y2="{by:.1f}" '
             f'stroke="#ef6f88" stroke-dasharray="5 4"/>')
    s.append(f'<text x="{PAD_L+PW-6}" y="{by-6:.1f}" fill="#ef6f88" font-size="11" '
             f'text-anchor="end">500ms §9 budget</text>')
    # RPS series (right axis)
    s.append(f'<polyline points="{poly(xs, rps, py_rps)}" fill="none" '
             f'stroke="#e0a83e" stroke-width="2.5"/>')
    for x, y in zip(xs, rps):
        s.append(f'<circle cx="{px(x):.1f}" cy="{py_rps(y):.1f}" r="3.5" fill="#e0a83e"/>')
    # p95 series (left axis)
    s.append(f'<polyline points="{poly(xs, p95, py_ms)}" fill="none" '
             f'stroke="#5fd6e3" stroke-width="2.5"/>')
    for x, y in zip(xs, p95):
        s.append(f'<circle cx="{px(x):.1f}" cy="{py_ms(y):.1f}" r="3.5" fill="#5fd6e3"/>')
        s.append(f'<text x="{px(x):.1f}" y="{py_ms(y)-9:.1f}" fill="#cfe9ee" '
                 f'font-size="10" text-anchor="middle">{y:.0f}</text>')
    s.append("</svg>")

    with open(OUT, "w") as fh:
        fh.write("\n".join(s))
    print(f"wrote {OUT} ({len(rows)} points)")


if __name__ == "__main__":
    main()
