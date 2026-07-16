import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider } from "./theme/useTheme";
import { I18nProvider } from "./i18n/I18nProvider";
import { SessionProvider } from "./mock/session";
import { App } from "./App";
import "./theme/global.css";
import "./components/ui/ui.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      <ThemeProvider>
        <SessionProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </SessionProvider>
      </ThemeProvider>
    </I18nProvider>
  </StrictMode>,
);
