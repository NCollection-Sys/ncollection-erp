import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { translations, type Lang } from "./translations";

type Dir = "ltr" | "rtl";
type I18nValue = {
  lang: Lang;
  dir: Dir;
  t: (key: string, params?: Record<string, string | number>) => string;
  tList: (key: string) => string[];
  setLang: (l: Lang) => void;
  toggleLang: () => void;
};

const I18nContext = createContext<I18nValue | null>(null);
const STORAGE_KEY = "nc-lang";

function initialLang(): Lang {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "ar" ? "ar" : "en";
}

/** Resolve a dotted key path against a nested dictionary. */
function resolve(dict: unknown, path: string): string | undefined {
  return path.split(".").reduce<unknown>((acc, part) => {
    if (acc && typeof acc === "object" && part in (acc as object)) {
      return (acc as Record<string, unknown>)[part];
    }
    return undefined;
  }, dict) as string | undefined;
}

function interpolate(str: string, params?: Record<string, string | number>): string {
  if (!params) return str;
  return str.replace(/\{(\w+)\}/g, (_, k) =>
    k in params ? String(params[k]) : `{${k}}`,
  );
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang);
  const dir: Dir = lang === "ar" ? "rtl" : "ltr";

  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute("lang", lang);
    root.setAttribute("dir", dir);
    localStorage.setItem(STORAGE_KEY, lang);
  }, [lang, dir]);

  const t = useCallback(
    (key: string, params?: Record<string, string | number>) => {
      const value =
        resolve(translations[lang], key) ?? resolve(translations.en, key) ?? key;
      return interpolate(value, params);
    },
    [lang],
  );

  const tList = useCallback(
    (key: string): string[] => {
      const raw =
        (resolve(translations[lang], key) as unknown) ??
        (resolve(translations.en, key) as unknown);
      return Array.isArray(raw) ? (raw as string[]) : [];
    },
    [lang],
  );

  const value = useMemo<I18nValue>(
    () => ({
      lang,
      dir,
      t,
      tList,
      setLang: setLangState,
      toggleLang: () => setLangState((l) => (l === "en" ? "ar" : "en")),
    }),
    [lang, dir, t, tList],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}
