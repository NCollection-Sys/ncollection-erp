import { Icon, type IconName } from "./Icon";
import { Trend } from "./primitives";

/*
 * KPI tile — the visual descendant of the Odoo `.o_ncollection_kpi_card`:
 * white surface, 4px primary left-accent, uppercase label, large bold value.
 * Adds an icon chip and an optional trend chip / subtitle.
 */
export function KpiCard({
  label,
  value,
  icon,
  trend,
  sub,
}: {
  label: string;
  value: string;
  icon: IconName;
  trend?: number;
  sub?: string;
}) {
  return (
    <div className="nc-kpi">
      <div className="nc-kpi__top">
        <span className="nc-label">{label}</span>
        <span className="nc-kpi__icon">
          <Icon name={icon} size={17} />
        </span>
      </div>
      <div className="nc-kpi__value nc-tnum">{value}</div>
      <div className="nc-kpi__sub">
        {trend !== undefined && <Trend value={trend} />}
        {sub && <span>{sub}</span>}
      </div>
    </div>
  );
}
