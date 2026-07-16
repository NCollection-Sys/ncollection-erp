import { PageHeader } from "../components/ui/PageHeader";
import { Button, Badge } from "../components/ui/primitives";
import { dataService, type StockItem } from "../mock/data";
import { useI18n } from "../i18n/I18nProvider";
import { aed, num } from "../lib/format";
import "./pages.css";

const STATUS: Record<StockItem["status"], { tone: "success" | "warning" | "danger"; key: string }> = {
  in_stock: { tone: "success", key: "inventory.stIn" },
  low: { tone: "warning", key: "inventory.stLow" },
  out: { tone: "danger", key: "inventory.stOut" },
};

export function InventoryPage() {
  const { t } = useI18n();
  const items = dataService.getStock();
  const totalValue = items.reduce((s, i) => s + i.onHand * i.unitCost, 0);
  const low = items.filter((i) => i.status === "low").length;
  const out = items.filter((i) => i.status === "out").length;

  return (
    <>
      <PageHeader
        title={t("inventory.title")}
        subtitle={t("inventory.subtitle")}
        actions={
          <>
            <Button variant="secondary" icon="purchase">{t("inventory.newPO")}</Button>
            <Button icon="plus">{t("inventory.newProduct")}</Button>
          </>
        }
      />

      <div className="nc-statstrip">
        <div className="nc-stat">
          <div className="nc-stat__label">{t("inventory.inventoryValue")}</div>
          <div className="nc-stat__value nc-tnum">{aed(totalValue, { compact: true })}</div>
        </div>
        <div className="nc-stat">
          <div className="nc-stat__label">{t("inventory.skus")}</div>
          <div className="nc-stat__value nc-tnum">{items.length}</div>
        </div>
        <div className="nc-stat">
          <div className="nc-stat__label">{t("inventory.lowStock")}</div>
          <div className="nc-stat__value nc-tnum" style={{ color: "var(--nc-warning)" }}>{low}</div>
        </div>
        <div className="nc-stat">
          <div className="nc-stat__label">{t("inventory.outStock")}</div>
          <div className="nc-stat__value nc-tnum" style={{ color: "var(--nc-danger)" }}>{out}</div>
        </div>
      </div>

      <div className="nc-card">
        <div className="nc-table-wrap">
          <table className="nc-table">
            <thead>
              <tr>
                <th>{t("inventory.sku")}</th>
                <th>{t("inventory.product")}</th>
                <th>{t("inventory.category")}</th>
                <th className="nc-num">{t("inventory.onHand")}</th>
                <th className="nc-num">{t("inventory.forecast")}</th>
                <th className="nc-num">{t("inventory.unitCost")}</th>
                <th>{t("inventory.status")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.sku}>
                  <td className="nc-cell-mono">{i.sku}</td>
                  <td className="nc-cell-strong">{i.name}</td>
                  <td>{i.category}</td>
                  <td className="nc-num nc-tnum">{num(i.onHand)}</td>
                  <td
                    className="nc-num nc-tnum"
                    style={{ color: i.forecast < 0 ? "var(--nc-danger)" : "var(--nc-text-muted)" }}
                  >
                    {i.forecast > 0 ? `+${i.forecast}` : i.forecast}
                  </td>
                  <td className="nc-num nc-tnum">{aed(i.unitCost)}</td>
                  <td>
                    <Badge tone={STATUS[i.status].tone}>{t(STATUS[i.status].key)}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
