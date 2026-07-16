import { useState, type ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import "./shell.css";

export function AppShell({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="shell">
      <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />
      {mobileOpen && (
        <div
          className="shell__scrim"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}
      <div className="shell__main">
        <Topbar onMenu={() => setMobileOpen(true)} />
        <main className="shell__content">{children}</main>
      </div>
    </div>
  );
}
