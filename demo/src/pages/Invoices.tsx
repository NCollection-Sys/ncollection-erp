import { PageHeader } from "../components/ui/PageHeader";
import { Button, Badge } from "../components/ui/primitives";
import { dataService, type Invoice } from "../mock/data";
import { useI18n } from "../i18n/I18nProvider";
import { aed, shortDate } from "../lib/format";
import "./pages.css";

const STATUS: Record<Invoice["status"], { tone: "success" | "info" | "warning" | "danger" | "neutral"; key: string }> = {
  paid: { tone: "success", key: "invoices.stPaid" },
  open: { tone: "info", key: "invoices.stOpen" },
  overdue: { tone: "danger", key: "invoices.stOverdue" },
  draft: { tone: "neutral", key: "invoices.stDraft" },
};

export function InvoicesPage() {
  const { t } = useI18n();
  const invoices = dataService.getInvoices();

  const outstanding = invoices
    .filter((i) => i.status === "open" || i.status === "overdue")
    .reduce((s, i) => s + i.amount, 0);
  const overdueTotal = invoices
    .filter((i) => i.status === "overdue")
    .reduce((s, i) => s + i.amount, 0);
  const paidTotal = invoices
    .filter((i) => i.status === "paid")
    .reduce((s, i) => s + i.amount, 0);

  return (
    <>
      <PageHeader
        title={t("invoices.title")}
        subtitle={t("invoices.subtitle")}
        actions={
          <>
            <Button variant="secondary" icon="mail">{t("invoices.sendReminders")}</Button>
            <Button icon="plus">{t("invoices.newInvoice")}</Button>
          </>
        }
      />

      <div className="nc-statstrip">
        <div className="nc-stat">
          <div className="nc-stat__label">{t("invoices.outstanding")}</div>
          <div className="nc-stat__value nc-tnum">{aed(outstanding, { compact: true })}</div>
        </div>
        <div className="nc-stat">
          <div className="nc-stat__label">{t("invoices.overdue")}</div>
          <div className="nc-stat__value nc-tnum" style={{ color: "var(--nc-danger)" }}>
            {aed(overdueTotal, { compact: true })}
          </div>
        </div>
        <div className="nc-stat">
          <div className="nc-stat__label">{t("invoices.paid30")}</div>
          <div className="nc-stat__value nc-tnum">{aed(paidTotal, { compact: true })}</div>
        </div>
        <div className="nc-stat">
          <div className="nc-stat__label">{t("invoices.vatCollected")}</div>
          <div className="nc-stat__value nc-tnum">{aed(21430, { compact: true })}</div>
        </div>
      </div>

      <div className="nc-card">
        <div className="nc-table-wrap">
          <table className="nc-table">
            <thead>
              <tr>
                <th>{t("invoices.invoice")}</th>
                <th>{t("invoices.customer")}</th>
                <th>{t("invoices.invoiceDate")}</th>
                <th>{t("invoices.dueDate")}</th>
                <th className="nc-num">{t("invoices.amount")}</th>
                <th>{t("invoices.status")}</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((i) => (
                <tr key={i.ref}>
                  <td className="nc-cell-mono">{i.ref}</td>
                  <td className="nc-cell-strong">{i.customer}</td>
                  <td>{shortDate(i.date)}</td>
                  <td>{shortDate(i.dueDate)}</td>
                  <td className="nc-num nc-tnum">{aed(i.amount)}</td>
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
