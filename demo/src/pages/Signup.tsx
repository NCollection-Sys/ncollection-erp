import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Logo } from "../components/ui/Logo";
import { Button } from "../components/ui/primitives";
import { Icon } from "../components/ui/Icon";
import { useSession } from "../mock/session";
import { odooApi, type SignupResult } from "../api/odoo";
import { useI18n } from "../i18n/I18nProvider";
import "./login.css";

const SERVER_ERROR_KEYS: Record<NonNullable<SignupResult["error"]>, string> = {
  missing_fields: "signup.errorMissing",
  invalid_email: "signup.errorInvalidEmail",
  weak_password: "signup.errorWeakPassword",
  email_exists: "signup.errorEmailExists",
};

export function SignupPage() {
  const { login } = useSession();
  const { t } = useI18n();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (password !== confirm) {
      setError(t("signup.errorMismatch"));
      return;
    }
    if (password.length < 8) {
      setError(t("signup.errorWeakPassword"));
      return;
    }

    setSubmitting(true);
    try {
      const result = await odooApi.signup(name, email, password);
      if (!result.success) {
        setError(t(result.error ? SERVER_ERROR_KEYS[result.error] : "signup.errorServer"));
        return;
      }
      // Account created in the real database — log straight in.
      await login(email.trim().toLowerCase(), password);
      navigate("/dashboard");
    } catch {
      setError(t("signup.errorServer"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login">
      <aside className="login__brand">
        <div className="login__brand-inner">
          <Logo size={40} onDark />
          <div className="login__brand-copy">
            <h1>{t("signup.brandTitle")}</h1>
            <p>{t("signup.brandBody")}</p>
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

      <main className="login__form-side">
        <div className="login__card">
          <div className="login__card-logo">
            <Logo size={38} />
          </div>
          <h2 className="login__heading">{t("signup.heading")}</h2>
          <p className="login__sub">{t("signup.sub")}</p>

          <form onSubmit={onSubmit} className="login__form">
            <div className="nc-field">
              <label htmlFor="su-name">{t("signup.name")}</label>
              <input
                id="su-name"
                className="nc-input"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoComplete="name"
                required
              />
            </div>
            <div className="nc-field">
              <label htmlFor="su-email">{t("signup.email")}</label>
              <input
                id="su-email"
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
              <label htmlFor="su-password">{t("signup.password")}</label>
              <input
                id="su-password"
                className="nc-input nc-ltr"
                dir="ltr"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                minLength={8}
                required
              />
            </div>
            <div className="nc-field">
              <label htmlFor="su-confirm">{t("signup.confirm")}</label>
              <input
                id="su-confirm"
                className="nc-input nc-ltr"
                dir="ltr"
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
                minLength={8}
                required
              />
            </div>

            {error && (
              <p className="login__error" role="alert">
                {error}
              </p>
            )}

            <Button type="submit" block disabled={submitting}>
              {submitting ? t("signup.creating") : t("signup.create")}
            </Button>
          </form>

          <p className="login__signup">
            {t("signup.haveAccount")} <Link to="/login">{t("login.signIn")}</Link>
          </p>
        </div>

        <footer className="login__footer">{t("login.footer")}</footer>
      </main>
    </div>
  );
}
