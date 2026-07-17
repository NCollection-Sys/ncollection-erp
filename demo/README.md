# NCollection ERP — Phase 1 Demo UI

A standalone, dummy-data demo of the **Phase 1 customer workspace** for NCollection ERP.
Built to be demoed **now** while the backend and database are still in progress, and
designed so the **design system and screens are reused later** when the UI is ported into
the real Odoo product.

> ⚠️ **Demo only.** Every number, name, and record on screen is mock data. There is no
> backend, no database, no real authentication. This is not the Phase 1 tasks marked "done"
> — it's a visual/interaction prototype of them.

## Run it

```bash
cd demo
npm install
npm run dev      # http://localhost:5173
```

Other scripts: `npm run build` (typecheck + production build), `npm run preview` (serve the build).

## What's in the demo

| Screen | Maps to task | Notes |
|--------|--------------|-------|
| Login | P1-T14 | Branded split-screen, remember-me, forgot-password, responsive |
| App shell (sidebar + topbar) | P1-T13 | White-labeled, role-aware navigation, theme toggle |
| Customer Dashboard | P1-T17 | KPIs, trend chips, revenue line chart, top-customers bar chart, quick actions — **role-aware** |
| Workspace Settings | P1-T12 / P1-T16 | Company Info (name, logo, TRN, address), User Management (invite/role/seat limit), Appearance (colors + live preview) |
| Email templates | P1-T18 | Branded invitation / invoice / password-reset previews |
| Sales, Invoices, CRM, Inventory | Phase 1 ERP surfaces | Realistic GCC data, shared table/kanban components |

**Try the role switcher** in the top bar ("Viewing as") — the sidebar and dashboard widgets
change per role (Owner sees everything; Employee sees only Dashboard + Projects; Accountant
sees financial widgets; Sales sees pipeline widgets). This mirrors the 8 NCollection roles.

Both **light and dark themes** are supported (toggle in the top bar).

### Bilingual: English & Arabic (RTL)

Use the **EN / ع** toggle in the top bar to switch language. Arabic mode:
- Sets `dir="rtl"` + `lang="ar"` on the document; the entire layout mirrors (sidebar moves
  to the right, KPI/card accents flip, tables and text right-align).
- Translates all chrome — navigation, page headers, KPI labels, table headers, statuses,
  roles, settings, login, and email templates. Proper nouns (company, customer, and person
  names) stay as entered data, exactly as a real bilingual GCC ERP behaves.
- Keeps Latin/numeric fields (email, phone, TRN, website) rendering left-to-right so digit
  groups don't reorder under bidi, while staying aligned to the RTL edge.
- Neutralizes `letter-spacing`/uppercase for Arabic (cursive script) and uses an
  Arabic-capable font stack.

All four combinations — **EN/AR × light/dark** — are verified. This RTL groundwork maps
directly onto the real product's Arabic phase (P3-T08); the `t()` keys become Odoo `.po`
message ids and the logical-property CSS ports as-is.

## Architecture — why this is reusable, not throwaway

Two design choices make the port into Odoo mechanical rather than a rewrite:

### 1. Design tokens = the brand system
`src/theme/tokens.css` defines the palette as CSS custom properties, grounded in the
committed brand colors from `custom_addons/ncollection_branding/static/src/scss/theme_colors.scss`
(primary `#1F5F8F`, secondary `#2D7AB7`, etc.). These map 1:1 onto the Odoo `--nc-*`
variables that P1-T16 introduces. The KPI card (`.nc-kpi`) is a direct visual descendant of
the Odoo `.o_ncollection_kpi_card` (white surface, 4px primary left-accent).

### 2. All data flows through one mock service
`src/mock/data.ts` exposes `dataService` — the **single** place any screen reads business
data. No component hardcodes data. The types (`Company`, `SalesOrder`, `DashboardKpis`, …)
mirror the real Odoo models. **To go live, replace the bodies of `dataService` methods with
API/RPC calls — the component code and types stay unchanged.**

### Porting map (for the eventual Odoo work)

| Demo artifact | Ports to |
|---------------|----------|
| `src/theme/tokens.css` `--nc-*` vars | `theme_colors.scss` `--nc-*` custom properties (P1-T16) |
| `src/components/ui/KpiCard.tsx` | `.o_ncollection_kpi_card` QWeb markup |
| `src/pages/Dashboard.tsx` | The P1-T17 OWL client action |
| `src/pages/Login.tsx` | QWeb inheritance on `web.login` (P1-T14) |
| `src/pages/Settings.tsx` | Owner settings backend views (P1-T12) + res.company fields (P1-T16) |
| `src/mock/data.ts` `dataService` | Python dashboard service model `read_group`/`search_count` |
| `src/lib/roles.ts` | The 8 `res.groups` (P1-T08) |

## Tech

React 18 + TypeScript + Vite · Chart.js (same engine Odoo uses) · React Router.
No backend calls, no external fonts/CDNs. Structure is feature/surface-oriented under `src/`.
