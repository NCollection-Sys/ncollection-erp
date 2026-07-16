import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "../components/ui/PageHeader";
import { Card, Button, Badge, Avatar, Field } from "../components/ui/primitives";
import { Icon } from "../components/ui/Icon";
import { LogoMark } from "../components/ui/Logo";
import { dataService } from "../mock/data";
import { ROLE_LIST } from "../lib/roles";
import { useI18n } from "../i18n/I18nProvider";
import "./settings.css";

type Tab = "company" | "users" | "appearance";

export function SettingsPage() {
  const { t } = useI18n();
  const company = dataService.getCompany();
  const users = dataService.getUsers();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("company");
  const [colors, setColors] = useState(company.branding);

  const seatPct = Math.round((company.seatsUsed / company.seatsLimit) * 100);

  return (
    <>
      <PageHeader
        title={t("settings.title")}
        subtitle={t("settings.subtitle")}
        actions={
          <Button variant="secondary" icon="mail" onClick={() => navigate("/settings/emails")}>
            {t("settings.emailTemplates")}
          </Button>
        }
      />

      <div className="settings">
        <nav className="settings__nav">
          <button
            className={`settings__navitem ${tab === "company" ? "settings__navitem--active" : ""}`}
            onClick={() => setTab("company")}
          >
            <Icon name="building" size={17} /> {t("settings.tabCompany")}
          </button>
          <button
            className={`settings__navitem ${tab === "users" ? "settings__navitem--active" : ""}`}
            onClick={() => setTab("users")}
          >
            <Icon name="users" size={17} /> {t("settings.tabUsers")}
          </button>
          <button
            className={`settings__navitem ${tab === "appearance" ? "settings__navitem--active" : ""}`}
            onClick={() => setTab("appearance")}
          >
            <Icon name="palette" size={17} /> {t("settings.tabAppearance")}
          </button>
        </nav>

        <div className="settings__panel">
          {tab === "company" && (
            <Card title={t("settings.companyInfo")}>
              <div className="settings__logo-row">
                <div className="settings__logo-tile">
                  <LogoMark size={54} />
                </div>
                <div>
                  <div className="nc-cell-strong">{t("settings.companyLogo")}</div>
                  <p className="nc-section-sub">{t("settings.logoHint")}</p>
                  <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                    <Button variant="secondary" size="sm">{t("settings.uploadLogo")}</Button>
                    <Button variant="ghost" size="sm">{t("settings.remove")}</Button>
                  </div>
                </div>
              </div>

              <div className="settings__form-grid">
                <Field label={t("settings.companyName")}>
                  <input className="nc-input" defaultValue={company.name} />
                </Field>
                <Field label={t("settings.legalName")}>
                  <input className="nc-input" defaultValue={company.legalName} />
                </Field>
                <Field label={t("settings.trn")}>
                  <input className="nc-input nc-tnum nc-ltr" dir="ltr" defaultValue={company.trn} />
                </Field>
                <Field label={t("settings.phone")}>
                  <input className="nc-input nc-ltr" dir="ltr" defaultValue={company.phone} />
                </Field>
                <Field label={t("settings.email")}>
                  <input className="nc-input nc-ltr" dir="ltr" defaultValue={company.email} />
                </Field>
                <Field label={t("settings.website")}>
                  <input className="nc-input nc-ltr" dir="ltr" defaultValue={company.website} />
                </Field>
                <div className="settings__full">
                  <Field label={t("settings.address")}>
                    <input className="nc-input nc-ltr" dir="ltr" defaultValue={`${company.address}, ${company.city}`} />
                  </Field>
                </div>
              </div>

              <div className="settings__actions">
                <Button variant="ghost">{t("common.cancel")}</Button>
                <Button icon="check">{t("common.save")}</Button>
              </div>
            </Card>
          )}

          {tab === "users" && (
            <Card
              title={t("settings.teamMembers")}
              action={
                <Badge tone={seatPct >= 90 ? "warning" : "info"}>
                  {t("settings.seatsUsed", { used: company.seatsUsed, limit: company.seatsLimit })}
                </Badge>
              }
            >
              <div className="settings__invite">
                <input className="nc-input" placeholder={t("settings.invitePlaceholder")} />
                <select className="nc-input settings__role-select" defaultValue="employee">
                  {ROLE_LIST.map((r) => (
                    <option key={r.key} value={r.key}>{t(`roles.${r.key}`)}</option>
                  ))}
                </select>
                <Button icon="plus">{t("settings.invite")}</Button>
              </div>

              <div className="settings__seatbar">
                <div className="settings__seatbar-track">
                  <div className="settings__seatbar-fill" style={{ width: `${seatPct}%` }} />
                </div>
                <span className="nc-section-sub">
                  {t("settings.seatsRemaining", { n: company.seatsLimit - company.seatsUsed })}
                  {seatPct >= 90 && t("settings.seatsWarn")}
                </span>
              </div>

              <div className="nc-table-wrap" style={{ marginTop: 8 }}>
                <table className="nc-table">
                  <thead>
                    <tr>
                      <th>{t("settings.member")}</th>
                      <th>{t("settings.role")}</th>
                      <th>{t("settings.status")}</th>
                      <th>{t("settings.lastActive")}</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u) => (
                      <tr key={u.id}>
                        <td>
                          <div className="settings__member">
                            <Avatar name={u.name} size={32} />
                            <div>
                              <div className="nc-cell-strong">{u.name}</div>
                              <div className="nc-section-sub">{u.email}</div>
                            </div>
                          </div>
                        </td>
                        <td>{t(`roles.${u.role}`)}</td>
                        <td>
                          <Badge
                            tone={
                              u.status === "active"
                                ? "success"
                                : u.status === "invited"
                                  ? "info"
                                  : "neutral"
                            }
                          >
                            {u.status === "active"
                              ? t("settings.stActive")
                              : u.status === "invited"
                                ? t("settings.stInvited")
                                : t("settings.stInactive")}
                          </Badge>
                        </td>
                        <td className="nc-section-sub">{u.lastActive}</td>
                        <td className="nc-num">
                          <Button variant="ghost" size="sm">{t("common.manage")}</Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          {tab === "appearance" && (
            <div className="settings__appearance">
              <Card title={t("settings.brandColors")}>
                <p className="nc-section-sub" style={{ marginBottom: 18 }}>
                  {t("settings.colorsHint")}
                </p>
                <div className="settings__colors">
                  <ColorField
                    label={t("settings.primary")}
                    value={colors.primary}
                    onChange={(v) => setColors({ ...colors, primary: v })}
                  />
                  <ColorField
                    label={t("settings.secondary")}
                    value={colors.secondary}
                    onChange={(v) => setColors({ ...colors, secondary: v })}
                  />
                  <ColorField
                    label={t("settings.sidebar")}
                    value={colors.sidebar}
                    onChange={(v) => setColors({ ...colors, sidebar: v })}
                  />
                </div>
                <div className="settings__actions">
                  <Button variant="ghost" onClick={() => setColors(company.branding)}>
                    {t("common.reset")}
                  </Button>
                  <Button icon="check">{t("settings.saveAppearance")}</Button>
                </div>
              </Card>

              <Card title={t("settings.livePreview")}>
                <div className="settings__preview">
                  <div className="settings__preview-bar" style={{ background: colors.sidebar }}>
                    <LogoMark size={20} />
                    <span>{t("topbar.company")}</span>
                  </div>
                  <div className="settings__preview-body">
                    <div
                      className="settings__preview-chip"
                      style={{ background: colors.primary }}
                    >
                      {t("settings.previewPrimary")}
                    </div>
                    <button
                      className="settings__preview-btn"
                      style={{ background: colors.primary }}
                    >
                      {t("settings.previewBtn")}
                    </button>
                    <a style={{ color: colors.secondary, fontWeight: 600 }} href="#preview">
                      {t("settings.sampleLink")}
                    </a>
                  </div>
                </div>
                <p className="nc-section-sub" style={{ marginTop: 14 }}>
                  {t("settings.hexNote")}
                </p>
              </Card>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function ColorField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="settings__color">
      <label>{label}</label>
      <div className="settings__color-input">
        <input
          type="color"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-label={label}
        />
        <input
          className="nc-input nc-tnum"
          value={value.toUpperCase()}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    </div>
  );
}
