import { Navigate, Route, Routes } from "react-router-dom";
import { useSession } from "./mock/session";
import { AppShell } from "./layout/AppShell";
import { LoginPage } from "./pages/Login";
import { DashboardPage } from "./pages/Dashboard";
import { SettingsPage } from "./pages/Settings";
import { SalesOrdersPage } from "./pages/SalesOrders";
import { InvoicesPage } from "./pages/Invoices";
import { CrmPage } from "./pages/Crm";
import { InventoryPage } from "./pages/Inventory";
import { EmailPreviewPage } from "./pages/EmailPreview";
import { PlaceholderPage } from "./pages/Placeholder";

export function App() {
  const { authed } = useSession();

  if (!authed) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/crm" element={<CrmPage />} />
        <Route path="/sales" element={<SalesOrdersPage />} />
        <Route path="/invoicing" element={<InvoicesPage />} />
        <Route path="/inventory" element={<InventoryPage />} />
        <Route
          path="/purchase"
          element={<PlaceholderPage module="purchase" />}
        />
        <Route path="/hr" element={<PlaceholderPage module="hr" />} />
        <Route
          path="/projects"
          element={<PlaceholderPage module="projects" />}
        />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/settings/emails" element={<EmailPreviewPage />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AppShell>
  );
}
