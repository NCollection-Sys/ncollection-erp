import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/primitives";
import { dataService, type Lead } from "../mock/data";
import { useI18n } from "../i18n/I18nProvider";
import { aed } from "../lib/format";
import "./pages.css";

const STAGES: { key: Lead["stage"]; label: string }[] = [
  { key: "new", label: "crm.stageNew" },
  { key: "qualified", label: "crm.stageQualified" },
  { key: "proposition", label: "crm.stageProposition" },
  { key: "won", label: "crm.stageWon" },
];

export function CrmPage() {
  const { t } = useI18n();
  const leads = dataService.getLeads();
  const pipelineValue = leads
    .filter((l) => l.stage !== "won")
    .reduce((s, l) => s + l.value, 0);
  const wonValue = leads
    .filter((l) => l.stage === "won")
    .reduce((s, l) => s + l.value, 0);

  return (
    <>
      <PageHeader
        title={t("crm.title")}
        subtitle={t("crm.subtitle")}
        actions={<Button icon="plus">{t("crm.addLead")}</Button>}
      />

      <div className="nc-statstrip">
        <div className="nc-stat">
          <div className="nc-stat__label">{t("crm.openPipeline")}</div>
          <div className="nc-stat__value nc-tnum">{aed(pipelineValue, { compact: true })}</div>
        </div>
        <div className="nc-stat">
          <div className="nc-stat__label">{t("crm.wonThisMonth")}</div>
          <div className="nc-stat__value nc-tnum" style={{ color: "var(--nc-success)" }}>
            {aed(wonValue, { compact: true })}
          </div>
        </div>
        <div className="nc-stat">
          <div className="nc-stat__label">{t("crm.activeLeads")}</div>
          <div className="nc-stat__value nc-tnum">{leads.filter((l) => l.stage !== "won").length}</div>
        </div>
        <div className="nc-stat">
          <div className="nc-stat__label">{t("crm.winRate")}</div>
          <div className="nc-stat__value nc-tnum">34%</div>
        </div>
      </div>

      <div className="nc-kanban">
        {STAGES.map((stage) => {
          const cards = leads.filter((l) => l.stage === stage.key);
          return (
            <div key={stage.key} className="nc-kancol">
              <div className="nc-kancol__head">
                <span className="nc-kancol__title">{t(stage.label)}</span>
                <span className="nc-kancol__count">{cards.length}</span>
              </div>
              {cards.map((l) => (
                <div key={l.id} className="nc-kancard">
                  <div className="nc-kancard__title">{l.title}</div>
                  <div className="nc-kancard__company">
                    {l.company} · {l.contact}
                  </div>
                  <div className="nc-kancard__foot">
                    <span className="nc-kancard__value">{aed(l.value, { compact: true })}</span>
                    <span className="nc-activity__meta">{l.owner.split(" ")[0]}</span>
                  </div>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </>
  );
}
