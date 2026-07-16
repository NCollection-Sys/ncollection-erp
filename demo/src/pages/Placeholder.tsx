import { PageHeader } from "../components/ui/PageHeader";
import { Card, Button } from "../components/ui/primitives";
import { Icon, type IconName } from "../components/ui/Icon";
import { useI18n } from "../i18n/I18nProvider";
import type { ModuleKey } from "../lib/roles";

const MODULE_META: Partial<
  Record<ModuleKey, { titleKey: string; blurbKey: string; featuresKey: string; icon: IconName }>
> = {
  purchase: {
    titleKey: "placeholder.purchaseTitle",
    blurbKey: "placeholder.purchaseBlurb",
    featuresKey: "placeholder.purchaseFeatures",
    icon: "purchase",
  },
  hr: {
    titleKey: "placeholder.hrTitle",
    blurbKey: "placeholder.hrBlurb",
    featuresKey: "placeholder.hrFeatures",
    icon: "hr",
  },
  projects: {
    titleKey: "placeholder.projectsTitle",
    blurbKey: "placeholder.projectsBlurb",
    featuresKey: "placeholder.projectsFeatures",
    icon: "projects",
  },
};

export function PlaceholderPage({ module }: { module: ModuleKey }) {
  const { t, tList } = useI18n();
  const meta = MODULE_META[module];
  if (!meta) return null;

  const title = t(meta.titleKey);
  const features = tList(meta.featuresKey);

  return (
    <>
      <PageHeader title={title} subtitle={t(meta.blurbKey)} />
      <Card>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            textAlign: "center",
            padding: "40px 20px",
            gap: 18,
          }}
        >
          <span
            style={{
              display: "grid",
              placeItems: "center",
              width: 64,
              height: 64,
              borderRadius: 16,
              background: "var(--nc-info-soft)",
              color: "var(--nc-accent-fg)",
            }}
          >
            <Icon name={meta.icon} size={30} />
          </span>
          <div>
            <h2 style={{ fontSize: "1.3rem", marginBottom: 6 }}>
              {t("placeholder.workspace", { name: title })}
            </h2>
            <p style={{ color: "var(--nc-text-muted)", maxWidth: "46ch" }}>
              {t("placeholder.included", { name: title })}
            </p>
          </div>
          {features.length > 0 && (
            <ul
              style={{
                listStyle: "none",
                padding: 0,
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: "10px 28px",
                textAlign: "start",
                marginTop: 4,
              }}
            >
              {features.map((f) => (
                <li
                  key={f}
                  style={{ display: "flex", alignItems: "center", gap: 9, color: "var(--nc-text)" }}
                >
                  <Icon name="check" size={16} style={{ color: "var(--nc-success)" }} />
                  {f}
                </li>
              ))}
            </ul>
          )}
          <Button variant="secondary" style={{ marginTop: 8 }}>
            {t("placeholder.explore", { name: title })}
          </Button>
        </div>
      </Card>
    </>
  );
}
