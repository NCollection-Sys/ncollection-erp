import { PageHeader } from "../components/ui/PageHeader";
import { KpiCard } from "../components/ui/KpiCard";
import { Card, Button, Badge } from "../components/ui/primitives";
import { Icon, type IconName } from "../components/ui/Icon";
import { RevenueLineChart, CustomersBarChart } from "../components/ui/charts";
import { dataService } from "../mock/data";
import { useRoleDef, useSession } from "../mock/session";
import { useI18n } from "../i18n/I18nProvider";
import { aed, num } from "../lib/format";
import "./pages.css";

const ACTIVITY_ICON: Record<string, IconName> = {
  call: "crm",
  meeting: "users",
  email: "mail",
  todo: "clipboard",
};

export function DashboardPage() {
  const role = useRoleDef();
  const { userName } = useSession();
  const { t } = useI18n();
  const kpis = dataService.getKpis();
  const revenue = dataService.getRevenueSeries();
  const customers = dataService.getTopCustomers();
  const activities = dataService.getActivities();

  const w = new Set(role.widgets);
  const showFinancial = w.has("financial");
  const showPipeline = w.has("pipeline");
  const showOps = w.has("operations");

  const tiles = [
    showPipeline && {
      label: t("dashboard.salesThisMonth"),
      value: aed(kpis.salesThisMonth, { compact: true }),
      icon: "sales" as IconName,
      trend: kpis.salesTrend,
      sub: t("dashboard.vsLastMonth"),
    },
    showFinancial && {
      label: t("dashboard.receivables"),
      value: aed(kpis.receivables, { compact: true }),
      icon: "invoicing" as IconName,
      sub: t("dashboard.openInvoices"),
    },
    showFinancial && {
      label: t("dashboard.payables"),
      value: aed(kpis.payables, { compact: true }),
      icon: "wallet" as IconName,
      sub: t("dashboard.dueWithin30"),
    },
    showFinancial && {
      label: t("dashboard.cashBank"),
      value: aed(kpis.cashBalance, { compact: true }),
      icon: "wallet" as IconName,
      sub: t("dashboard.across3"),
    },
    showOps && {
      label: t("dashboard.inventoryValue"),
      value: aed(kpis.inventoryValue, { compact: true }),
      icon: "inventory" as IconName,
      sub: t("dashboard.productsLow"),
    },
    {
      label: t("dashboard.openActivities"),
      value: num(kpis.openActivities),
      icon: "activity" as IconName,
      sub: t("dashboard.assignedTeam"),
    },
    {
      label: t("dashboard.pendingApprovals"),
      value: num(kpis.pendingApprovals),
      icon: "check" as IconName,
      sub: t("dashboard.awaitingSignoff"),
    },
  ].filter(Boolean) as {
    label: string;
    value: string;
    icon: IconName;
    trend?: number;
    sub?: string;
  }[];

  const firstName = userName.split(" ")[0];

  return (
    <>
      <PageHeader
        title={t("dashboard.greeting", { name: firstName })}
        subtitle={t("dashboard.subtitle", {
          company: t("topbar.company"),
          role: t(`roles.${role.key}`),
        })}
        actions={
          <>
            <Button variant="secondary" icon="plus" size="md">
              {t("dashboard.newQuotation")}
            </Button>
            <Button icon="plus">{t("dashboard.newInvoice")}</Button>
          </>
        }
      />

      <div className="nc-kpi-grid">
        {tiles.slice(0, 4).map((t2) => (
          <KpiCard key={t2.label} {...t2} />
        ))}
      </div>
      {tiles.length > 4 && (
        <div className="nc-kpi-grid">
          {tiles.slice(4, 8).map((t2) => (
            <KpiCard key={t2.label} {...t2} />
          ))}
        </div>
      )}

      {(showFinancial || showPipeline) && (
        <div className="nc-grid-2">
          <Card
            title={t("dashboard.revenueTitle")}
            action={<Badge tone="success">{t("dashboard.ytd")}</Badge>}
          >
            <RevenueLineChart labels={revenue.labels} data={revenue.data} />
          </Card>
          <Card
            title={t("dashboard.topCustomers")}
            action={<span className="nc-label">{t("dashboard.thisQuarter")}</span>}
          >
            <CustomersBarChart labels={customers.labels} data={customers.data} />
          </Card>
        </div>
      )}

      <div className="nc-grid-2">
        <Card
          title={t("dashboard.recentActivity")}
          action={<a href="#all" className="nc-label">{t("common.viewAll")}</a>}
        >
          <div className="nc-activity">
            {activities.map((a) => (
              <div key={a.id} className="nc-activity__item">
                <span className="nc-activity__icon">
                  <Icon name={ACTIVITY_ICON[a.type]} size={16} />
                </span>
                <div className="nc-activity__body">
                  <div className="nc-activity__summary">{a.summary}</div>
                  <div className="nc-activity__meta">
                    {a.due} · {a.who}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title={t("dashboard.quickActions")}>
          <div className="nc-quick">
            <Button variant="secondary" icon="sales">{t("dashboard.newQuotation")}</Button>
            <Button variant="secondary" icon="invoicing">{t("dashboard.newInvoice")}</Button>
            <Button variant="secondary" icon="purchase">{t("dashboard.newPO")}</Button>
            <Button variant="secondary" icon="crm">{t("dashboard.addLead")}</Button>
            <Button variant="secondary" icon="users">{t("dashboard.inviteUser")}</Button>
          </div>
          <div className="nc-note" style={{ marginTop: 18 }}>
            <Icon name="activity" size={18} />
            {t("dashboard.approvalNote", { n: kpis.pendingApprovals })}
          </div>
        </Card>
      </div>
    </>
  );
}
