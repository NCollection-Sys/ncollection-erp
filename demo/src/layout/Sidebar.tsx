import { NavLink } from "react-router-dom";
import { Logo } from "../components/ui/Logo";
import { Icon, type IconName } from "../components/ui/Icon";
import { useRoleDef } from "../mock/session";
import { useI18n } from "../i18n/I18nProvider";
import type { ModuleKey } from "../lib/roles";

type NavItem = { module: ModuleKey; key: string; to: string; icon: IconName };

const NAV: NavItem[] = [
  { module: "dashboard", key: "nav.dashboard", to: "/dashboard", icon: "dashboard" },
  { module: "crm", key: "nav.crm", to: "/crm", icon: "crm" },
  { module: "sales", key: "nav.sales", to: "/sales", icon: "sales" },
  { module: "purchase", key: "nav.purchase", to: "/purchase", icon: "purchase" },
  { module: "inventory", key: "nav.inventory", to: "/inventory", icon: "inventory" },
  { module: "invoicing", key: "nav.invoicing", to: "/invoicing", icon: "invoicing" },
  { module: "hr", key: "nav.hr", to: "/hr", icon: "hr" },
  { module: "projects", key: "nav.projects", to: "/projects", icon: "projects" },
];

export function Sidebar({
  mobileOpen,
  onClose,
}: {
  mobileOpen: boolean;
  onClose: () => void;
}) {
  const role = useRoleDef();
  const { t } = useI18n();
  const allowed = new Set(role.modules);
  const items = NAV.filter((n) => allowed.has(n.module));
  const showSettings = allowed.has("settings");

  return (
    <aside className={`sidebar ${mobileOpen ? "sidebar--open" : ""}`}>
      <div className="sidebar__brand">
        <Logo size={30} onDark />
      </div>

      <nav className="sidebar__nav" onClick={onClose}>
        <span className="sidebar__section">{t("nav.workspace")}</span>
        {items.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            className={({ isActive }) =>
              `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
            }
          >
            <Icon name={n.icon} size={18} />
            <span>{t(n.key)}</span>
          </NavLink>
        ))}

        {showSettings && (
          <>
            <span className="sidebar__section">{t("nav.administration")}</span>
            <NavLink
              to="/settings"
              className={({ isActive }) =>
                `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
              }
            >
              <Icon name="settings" size={18} />
              <span>{t("nav.settings")}</span>
            </NavLink>
          </>
        )}
      </nav>

      <div className="sidebar__foot">
        <span className="sidebar__plan-label">{t("nav.currentPlan")}</span>
        <div className="sidebar__plan">
          <span>{t("nav.planBusiness")}</span>
          <a href="#upgrade">{t("common.upgrade")}</a>
        </div>
      </div>
    </aside>
  );
}
