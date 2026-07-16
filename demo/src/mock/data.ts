/*
 * ============================================================================
 * MOCK DATA — the single reuse seam.
 * ============================================================================
 * Every screen reads business data from `dataService` (below). NOTHING is
 * hardcoded inside components. When the real backend is ready, replace the
 * bodies of dataService methods with fetch()/RPC calls — the component code
 * and the types stay unchanged.
 *
 * Types intentionally mirror the real Odoo models (ncollection.tenant,
 * res.company branding fields, the 8 roles) so shapes carry into production.
 * ============================================================================
 */

import type { RoleKey } from "../lib/roles";

/* ---------------- Company / tenant (mirrors res.company + ncollection.tenant) */
export type Company = {
  name: string;
  legalName: string;
  trn: string; // UAE Tax Registration Number
  address: string;
  city: string;
  email: string;
  phone: string;
  website: string;
  plan: "Starter" | "Business" | "Enterprise";
  seatsUsed: number;
  seatsLimit: number;
  branding: {
    primary: string;
    secondary: string;
    sidebar: string;
  };
};

/* ---------------- User (mirrors res.users + role group) */
export type WorkspaceUser = {
  id: number;
  name: string;
  email: string;
  role: RoleKey;
  status: "active" | "invited" | "inactive";
  lastActive: string;
};

/* ---------------- Dashboard KPIs */
export type DashboardKpis = {
  salesThisMonth: number;
  salesTrend: number; // % vs last month
  receivables: number;
  payables: number;
  cashBalance: number;
  openActivities: number;
  pendingApprovals: number;
  inventoryValue: number;
};

/* ---------------- Sales orders */
export type SalesOrder = {
  ref: string;
  customer: string;
  date: string;
  amount: number;
  status: "quotation" | "confirmed" | "done" | "cancelled";
  salesperson: string;
};

/* ---------------- Invoices */
export type Invoice = {
  ref: string;
  customer: string;
  date: string;
  dueDate: string;
  amount: number;
  status: "paid" | "open" | "overdue" | "draft";
};

/* ---------------- CRM leads (kanban) */
export type Lead = {
  id: number;
  title: string;
  company: string;
  contact: string;
  value: number;
  stage: "new" | "qualified" | "proposition" | "won";
  owner: string;
};

/* ---------------- Inventory */
export type StockItem = {
  sku: string;
  name: string;
  category: string;
  onHand: number;
  forecast: number;
  unitCost: number;
  status: "in_stock" | "low" | "out";
};

/* ---------------- Activities */
export type Activity = {
  id: number;
  type: "call" | "meeting" | "email" | "todo";
  summary: string;
  due: string;
  who: string;
};

/* ============================== THE DATA ================================== */

const company: Company = {
  name: "Al Barari Trading",
  legalName: "Al Barari Trading LLC",
  trn: "100234567800003",
  address: "Office 1204, Burlington Tower, Business Bay",
  city: "Dubai, United Arab Emirates",
  email: "hello@albarari.ae",
  phone: "+971 4 555 0182",
  website: "albarari.ae",
  plan: "Business",
  seatsUsed: 18,
  seatsLimit: 25,
  branding: {
    primary: "#1F5F8F",
    secondary: "#2D7AB7",
    sidebar: "#12233A",
  },
};

const users: WorkspaceUser[] = [
  { id: 1, name: "Layla Al Nuaimi", email: "layla@albarari.ae", role: "owner", status: "active", lastActive: "2026-07-17" },
  { id: 2, name: "Omar Haddad", email: "omar@albarari.ae", role: "ceo", status: "active", lastActive: "2026-07-17" },
  { id: 3, name: "Fatima Rahmani", email: "fatima@albarari.ae", role: "accountant", status: "active", lastActive: "2026-07-16" },
  { id: 4, name: "Yousef Karim", email: "yousef@albarari.ae", role: "sales", status: "active", lastActive: "2026-07-17" },
  { id: 5, name: "Sara Mansour", email: "sara@albarari.ae", role: "manager", status: "active", lastActive: "2026-07-15" },
  { id: 6, name: "Bilal Ahmed", email: "bilal@albarari.ae", role: "warehouse", status: "active", lastActive: "2026-07-16" },
  { id: 7, name: "Noura Saleh", email: "noura@albarari.ae", role: "hr", status: "active", lastActive: "2026-07-14" },
  { id: 8, name: "Khalid Rashid", email: "khalid@albarari.ae", role: "sales", status: "invited", lastActive: "—" },
  { id: 9, name: "Aisha Darwish", email: "aisha@albarari.ae", role: "employee", status: "inactive", lastActive: "2026-06-28" },
];

const kpis: DashboardKpis = {
  salesThisMonth: 842500,
  salesTrend: 12.4,
  receivables: 318200,
  payables: 146750,
  cashBalance: 1265400,
  openActivities: 23,
  pendingApprovals: 6,
  inventoryValue: 487300,
};

const revenue = {
  labels: ["Feb", "Mar", "Apr", "May", "Jun", "Jul"],
  data: [612000, 689000, 640000, 731000, 795000, 842500],
};

const topCustomers = {
  labels: [
    "Emaar Properties",
    "Majid Al Futtaim",
    "Al-Futtaim Group",
    "DAMAC",
    "Nakheel",
  ],
  data: [186000, 154000, 132500, 98000, 74500],
};

const salesOrders: SalesOrder[] = [
  { ref: "SO-2026-0412", customer: "Emaar Properties", date: "2026-07-16", amount: 84500, status: "confirmed", salesperson: "Yousef Karim" },
  { ref: "SO-2026-0411", customer: "Majid Al Futtaim", date: "2026-07-15", amount: 52300, status: "confirmed", salesperson: "Khalid Rashid" },
  { ref: "SO-2026-0410", customer: "DAMAC", date: "2026-07-15", amount: 31900, status: "quotation", salesperson: "Yousef Karim" },
  { ref: "SO-2026-0409", customer: "Nakheel", date: "2026-07-14", amount: 127400, status: "done", salesperson: "Yousef Karim" },
  { ref: "SO-2026-0408", customer: "Al-Futtaim Group", date: "2026-07-13", amount: 46200, status: "confirmed", salesperson: "Khalid Rashid" },
  { ref: "SO-2026-0407", customer: "Aldar Properties", date: "2026-07-12", amount: 18750, status: "quotation", salesperson: "Yousef Karim" },
  { ref: "SO-2026-0406", customer: "Dubai Holding", date: "2026-07-11", amount: 63000, status: "done", salesperson: "Khalid Rashid" },
  { ref: "SO-2026-0405", customer: "Meraas", date: "2026-07-10", amount: 24800, status: "cancelled", salesperson: "Yousef Karim" },
];

const invoices: Invoice[] = [
  { ref: "INV/2026/0288", customer: "Nakheel", date: "2026-07-14", dueDate: "2026-08-13", amount: 127400, status: "open" },
  { ref: "INV/2026/0287", customer: "Dubai Holding", date: "2026-07-11", dueDate: "2026-08-10", amount: 63000, status: "open" },
  { ref: "INV/2026/0286", customer: "Emaar Properties", date: "2026-06-30", dueDate: "2026-07-15", amount: 91200, status: "overdue" },
  { ref: "INV/2026/0285", customer: "Majid Al Futtaim", date: "2026-06-28", dueDate: "2026-07-28", amount: 44500, status: "paid" },
  { ref: "INV/2026/0284", customer: "Al-Futtaim Group", date: "2026-06-25", dueDate: "2026-07-25", amount: 38900, status: "paid" },
  { ref: "INV/2026/0283", customer: "DAMAC", date: "2026-07-16", dueDate: "2026-08-15", amount: 31900, status: "draft" },
  { ref: "INV/2026/0282", customer: "Aldar Properties", date: "2026-06-20", dueDate: "2026-07-05", amount: 15600, status: "overdue" },
];

const leads: Lead[] = [
  { id: 1, title: "Fit-out supply — Marina tower", company: "Select Group", contact: "R. Fernandes", value: 210000, stage: "new", owner: "Yousef Karim" },
  { id: 2, title: "Annual maintenance contract", company: "Sobha Realty", contact: "M. Iqbal", value: 96000, stage: "new", owner: "Khalid Rashid" },
  { id: 3, title: "Office furniture — HQ relocation", company: "Chalhoub Group", contact: "L. Aoun", value: 148000, stage: "qualified", owner: "Yousef Karim" },
  { id: 4, title: "Warehouse racking system", company: "Aramex", contact: "S. Botros", value: 78500, stage: "qualified", owner: "Khalid Rashid" },
  { id: 5, title: "Retail POS rollout — 12 stores", company: "Apparel Group", contact: "N. Kapoor", value: 305000, stage: "proposition", owner: "Yousef Karim" },
  { id: 6, title: "Signage & branding package", company: "GEMS Education", contact: "H. Suri", value: 64000, stage: "proposition", owner: "Khalid Rashid" },
  { id: 7, title: "Facilities supply — Q3", company: "Emaar Properties", contact: "A. Zaabi", value: 186000, stage: "won", owner: "Yousef Karim" },
  { id: 8, title: "IT hardware refresh", company: "Mashreq Bank", contact: "T. Wahab", value: 122000, stage: "won", owner: "Khalid Rashid" },
];

const stock: StockItem[] = [
  { sku: "FRN-DSK-014", name: "Executive Desk — Walnut", category: "Furniture", onHand: 42, forecast: 30, unitCost: 1450, status: "in_stock" },
  { sku: "FRN-CHR-208", name: "Ergonomic Task Chair", category: "Furniture", onHand: 8, forecast: -4, unitCost: 890, status: "low" },
  { sku: "ELC-MON-032", name: '27" 4K Monitor', category: "Electronics", onHand: 0, forecast: -12, unitCost: 1120, status: "out" },
  { sku: "ELC-LAP-101", name: "Business Laptop 14", category: "Electronics", onHand: 61, forecast: 45, unitCost: 3400, status: "in_stock" },
  { sku: "STA-PPR-500", name: "A4 Paper (box of 5)", category: "Stationery", onHand: 5, forecast: -20, unitCost: 42, status: "low" },
  { sku: "FRN-CAB-077", name: "Filing Cabinet — Steel", category: "Furniture", onHand: 27, forecast: 18, unitCost: 620, status: "in_stock" },
  { sku: "ELC-PRN-015", name: "Multifunction Printer", category: "Electronics", onHand: 14, forecast: 9, unitCost: 2250, status: "in_stock" },
];

const activities: Activity[] = [
  { id: 1, type: "call", summary: "Follow up on Nakheel PO terms", due: "Today, 14:00", who: "Yousef Karim" },
  { id: 2, type: "meeting", summary: "Q3 review with Emaar procurement", due: "Today, 16:30", who: "Omar Haddad" },
  { id: 3, type: "email", summary: "Send revised quote — Select Group", due: "Tomorrow", who: "Yousef Karim" },
  { id: 4, type: "todo", summary: "Approve July supplier payments", due: "Tomorrow", who: "Fatima Rahmani" },
  { id: 5, type: "call", summary: "Chase overdue invoice — Aldar", due: "18 Jul", who: "Fatima Rahmani" },
];

/* ============================ DATA SERVICE ================================
 * The one place screens read from. Swap these bodies for real API calls
 * later; the return types are the contract that keeps the UI unchanged.
 * ========================================================================= */

export const dataService = {
  getCompany: (): Company => company,
  getUsers: (): WorkspaceUser[] => users,
  getKpis: (): DashboardKpis => kpis,
  getRevenueSeries: () => revenue,
  getTopCustomers: () => topCustomers,
  getSalesOrders: (): SalesOrder[] => salesOrders,
  getInvoices: (): Invoice[] => invoices,
  getLeads: (): Lead[] => leads,
  getStock: (): StockItem[] => stock,
  getActivities: (): Activity[] => activities,
};
