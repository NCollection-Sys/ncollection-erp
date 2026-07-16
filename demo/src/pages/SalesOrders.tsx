import { useState } from "react";
import { PageHeader } from "../components/ui/PageHeader";
import { Button, Badge } from "../components/ui/primitives";
import { dataService, type SalesOrder } from "../mock/data";
import { useI18n } from "../i18n/I18nProvider";
import { aed, shortDate } from "../lib/format";
import "./pages.css";

const STATUS: Record<SalesOrder["status"], { tone: "success" | "info" | "neutral" | "danger"; key: string }> = {
  quotation: { tone: "info", key: "sales.stQuotation" },
  confirmed: { tone: "success", key: "sales.stConfirmed" },
  done: { tone: "neutral", key: "sales.stDelivered" },
  cancelled: { tone: "danger", key: "sales.stCancelled" },
};

const TABS = [
  { id: "All", key: "sales.tabAll" },
  { id: "Quotations", key: "sales.tabQuotations" },
  { id: "Confirmed", key: "sales.tabConfirmed" },
  { id: "Delivered", key: "sales.tabDelivered" },
] as const;

export function SalesOrdersPage() {
  const { t } = useI18n();
  const orders = dataService.getSalesOrders();
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("All");

  const filtered = orders.filter((o) => {
    if (tab === "All") return true;
    if (tab === "Quotations") return o.status === "quotation";
    if (tab === "Confirmed") return o.status === "confirmed";
    if (tab === "Delivered") return o.status === "done";
    return true;
  });

  const totalValue = orders
    .filter((o) => o.status !== "cancelled")
    .reduce((s, o) => s + o.amount, 0);
  const confirmed = orders.filter((o) => o.status === "confirmed").length;
  const quotations = orders.filter((o) => o.status === "quotation").length;

  return (
    <>
      <PageHeader
        title={t("sales.title")}
        subtitle={t("sales.subtitle")}
        actions={<Button icon="plus">{t("sales.newQuotation")}</Button>}
      />

      <div className="nc-statstrip">
        <div className="nc-stat">
          <div className="nc-stat__label">{t("sales.openPipeline")}</div>
          <div className="nc-stat__value nc-tnum">{aed(totalValue, { compact: true })}</div>
        </div>
        <div className="nc-stat">
          <div className="nc-stat__label">{t("sales.quotations")}</div>
          <div className="nc-stat__value nc-tnum">{quotations}</div>
        </div>
        <div className="nc-stat">
          <div className="nc-stat__label">{t("sales.confirmed")}</div>
          <div className="nc-stat__value nc-tnum">{confirmed}</div>
        </div>
        <div className="nc-stat">
          <div className="nc-stat__label">{t("sales.totalOrders")}</div>
          <div className="nc-stat__value nc-tnum">{orders.length}</div>
        </div>
      </div>

      <div className="nc-toolbar">
        <div className="nc-toolbar__tabs">
          {TABS.map((tb) => (
            <button
              key={tb.id}
              className={`nc-tab ${tab === tb.id ? "nc-tab--active" : ""}`}
              onClick={() => setTab(tb.id)}
            >
              {t(tb.key)}
            </button>
          ))}
        </div>
      </div>

      <div className="nc-card">
        <div className="nc-table-wrap">
          <table className="nc-table">
            <thead>
              <tr>
                <th>{t("sales.reference")}</th>
                <th>{t("sales.customer")}</th>
                <th>{t("sales.salesperson")}</th>
                <th>{t("sales.date")}</th>
                <th className="nc-num">{t("sales.amount")}</th>
                <th>{t("sales.status")}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((o) => (
                <tr key={o.ref}>
                  <td className="nc-cell-mono">{o.ref}</td>
                  <td className="nc-cell-strong">{o.customer}</td>
                  <td>{o.salesperson}</td>
                  <td>{shortDate(o.date)}</td>
                  <td className="nc-num nc-tnum">{aed(o.amount)}</td>
                  <td>
                    <Badge tone={STATUS[o.status].tone}>{t(STATUS[o.status].key)}</Badge>
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
