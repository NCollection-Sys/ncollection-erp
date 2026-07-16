import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "../components/ui/PageHeader";
import { Card, Button } from "../components/ui/primitives";
import { LogoMark } from "../components/ui/Logo";
import { useI18n } from "../i18n/I18nProvider";
import "./email.css";

type Template = "invite" | "invoice" | "reset";

export function EmailPreviewPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [tpl, setTpl] = useState<Template>("invite");

  const templates: { key: Template; label: string }[] = [
    { key: "invite", label: t("email.tabInvite") },
    { key: "invoice", label: t("email.tabInvoice") },
    { key: "reset", label: t("email.tabReset") },
  ];

  return (
    <>
      <PageHeader
        title={t("email.title")}
        subtitle={t("email.subtitle")}
        actions={
          <Button variant="secondary" onClick={() => navigate("/settings")}>
            {t("email.backToSettings")}
          </Button>
        }
      />

      <div className="email">
        <div className="email__tabs">
          {templates.map((tb) => (
            <button
              key={tb.key}
              className={`nc-tab ${tpl === tb.key ? "nc-tab--active" : ""}`}
              onClick={() => setTpl(tb.key)}
            >
              {tb.label}
            </button>
          ))}
        </div>

        <Card>
          <div className="email__frame">
            <div className="email__mail">
              <div className="email__header">
                <LogoMark size={30} />
                <span className="email__brand">{t("email.brand")}</span>
              </div>
              <div className="email__body">{renderBody(tpl, t)}</div>
              <div className="email__footer">
                <p>{t("email.footerCompany")}</p>
                <p>{t("email.footerReceived")}</p>
                <p className="email__footer-fine">{t("email.footerFine")}</p>
              </div>
            </div>
          </div>
        </Card>
      </div>
    </>
  );
}

function renderBody(tpl: Template, t: (k: string) => string) {
  if (tpl === "invite") {
    return (
      <>
        <h2>{t("email.inviteHeading")}</h2>
        <p>{t("email.inviteBody")}</p>
        <a className="email__cta" href="#accept">{t("email.inviteCta")}</a>
        <p className="email__muted">{t("email.inviteMuted")}</p>
      </>
    );
  }
  if (tpl === "invoice") {
    return (
      <>
        <h2>{t("email.invoiceHeading")}</h2>
        <p>{t("email.invoiceGreeting")}</p>
        <p>{t("email.invoiceBody")}</p>
        <div className="email__invoice">
          <div className="email__invoice-row">
            <span>{t("email.subtotal")}</span>
            <span>AED 121,333</span>
          </div>
          <div className="email__invoice-row">
            <span>{t("email.vat")}</span>
            <span>AED 6,067</span>
          </div>
          <div className="email__invoice-row email__invoice-row--total">
            <span>{t("email.totalDue")}</span>
            <span>AED 127,400</span>
          </div>
        </div>
        <a className="email__cta" href="#view">{t("email.invoiceCta")}</a>
        <p className="email__muted">TRN: 100234567800003</p>
      </>
    );
  }
  return (
    <>
      <h2>{t("email.resetHeading")}</h2>
      <p>{t("email.resetBody")}</p>
      <a className="email__cta" href="#reset">{t("email.resetCta")}</a>
      <p className="email__muted">{t("email.resetMuted")}</p>
    </>
  );
}
