import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Logo } from "../components/ui/Logo";
import { Button } from "../components/ui/primitives";
import { Icon } from "../components/ui/Icon";
import { useSession } from "../mock/session";
import { useI18n } from "../i18n/I18nProvider";
import "./login.css";

export function LoginPage() {
  const { login } = useSession();
  const { t } = useI18n();
  const navigate = useNavigate();
  const [email, setEmail] = useState("layla@albarari.ae");
  const [password, setPassword] = useState("demo-password");
  const [remember, setRemember] = useState(true);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    login();
    navigate("/dashboard");
  }

  return (
    <div className="login">
      {/* Left — branded panel */}
      <aside className="login__brand">
        <div className="login__brand-inner">
          <Logo size={40} onDark />
          <div className="login__brand-copy">
            <h1>{t("login.brandTitle")}</h1>
            <p>{t("login.brandBody")}</p>
          </div>
          <ul className="login__points">
            <li>
              <Icon name="check" size={16} /> {t("login.point1")}
            </li>
            <li>
              <Icon name="check" size={16} /> {t("login.point2")}
            </li>
            <li>
              <Icon name="check" size={16} /> {t("login.point3")}
            </li>
          </ul>
        </div>
        <div className="login__brand-glow" aria-hidden="true" />
      </aside>

      {/* Right — form */}
      <main className="login__form-side">
        <div className="login__card">
          <div className="login__card-logo">
            <Logo size={38} />
          </div>
          <h2 className="login__heading">{t("login.heading")}</h2>
          <p className="login__sub">{t("login.sub")}</p>

          <form onSubmit={onSubmit} className="login__form">
            <div className="nc-field">
              <label htmlFor="email">{t("login.email")}</label>
              <input
                id="email"
                className="nc-input nc-ltr"
                dir="ltr"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                required
              />
            </div>
            <div className="nc-field">
              <label htmlFor="password">{t("login.password")}</label>
              <input
                id="password"
                className="nc-input nc-ltr"
                dir="ltr"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </div>

            <div className="login__row">
              <label className="login__remember">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                />
                {t("login.remember")}
              </label>
              <a href="#reset" className="login__forgot">
                {t("login.forgot")}
              </a>
            </div>

            <Button type="submit" block>
              {t("login.signIn")}
            </Button>
          </form>

          <p className="login__signup">
            {t("login.noWorkspace")} <a href="#trial">{t("login.startTrial")}</a>
          </p>
        </div>

        <footer className="login__footer">{t("login.footer")}</footer>
      </main>
    </div>
  );
}
