import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Icon } from "../components/ui/Icon";
import { Avatar } from "../components/ui/primitives";
import { useTheme } from "../theme/useTheme";
import { useI18n } from "../i18n/I18nProvider";
import { useSession } from "../mock/session";
import { ROLE_LIST } from "../lib/roles";

export function Topbar({ onMenu }: { onMenu: () => void }) {
  const { theme, toggle } = useTheme();
  const { t, lang, toggleLang } = useI18n();
  const { role, setRole, userName, logout } = useSession();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <header className="topbar">
      <button className="topbar__menu-btn" onClick={onMenu} aria-label="Open menu">
        <Icon name="menu" size={20} />
      </button>

      <div className="topbar__search">
        <Icon name="search" size={17} />
        <input placeholder={t("topbar.search")} />
      </div>

      <div className="topbar__actions">
        {/* Demo-only role switcher */}
        <label className="topbar__role" title="Demo: switch role to preview role-aware UI">
          <span className="topbar__role-label">{t("topbar.viewingAs")}</span>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as typeof role)}
          >
            {ROLE_LIST.map((r) => (
              <option key={r.key} value={r.key}>
                {t(`roles.${r.key}`)}
              </option>
            ))}
          </select>
        </label>

        <button
          className="topbar__lang"
          onClick={toggleLang}
          aria-label="Toggle language"
          title={lang === "en" ? "التبديل إلى العربية" : "Switch to English"}
        >
          {lang === "en" ? "ع" : "EN"}
        </button>

        <button
          className="topbar__icon-btn"
          onClick={toggle}
          aria-label="Toggle theme"
          title={theme === "light" ? "Switch to dark" : "Switch to light"}
        >
          <Icon name={theme === "light" ? "moon" : "sun"} size={18} />
        </button>

        <button className="topbar__icon-btn topbar__bell" aria-label="Notifications">
          <Icon name="bell" size={18} />
          <span className="topbar__dot" />
        </button>

        <div className="topbar__user-wrap">
          <button
            className="topbar__user"
            onClick={() => setMenuOpen((o) => !o)}
          >
            <Avatar name={userName} size={32} />
            <span className="topbar__user-name">{userName}</span>
            <Icon name="chevronDown" size={15} />
          </button>
          {menuOpen && (
            <div className="topbar__menu" onMouseLeave={() => setMenuOpen(false)}>
              <div className="topbar__menu-head">
                <strong>{userName}</strong>
                <span>{t("topbar.company")}</span>
              </div>
              <button
                className="topbar__menu-item"
                onClick={() => {
                  setMenuOpen(false);
                  navigate("/settings");
                }}
              >
                <Icon name="settings" size={16} /> {t("topbar.settings")}
              </button>
              <button
                className="topbar__menu-item topbar__menu-item--danger"
                onClick={() => {
                  logout();
                  navigate("/login");
                }}
              >
                <Icon name="logout" size={16} /> {t("topbar.signOut")}
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
