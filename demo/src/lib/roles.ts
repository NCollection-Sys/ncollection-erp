/*
 * The 8 NCollection tenant roles (P1-T08). Mirrors the res.groups the real
 * product defines. Each role carries which app modules it can see and which
 * dashboard widget groups are relevant — this drives the role-aware UI.
 */

export type RoleKey =
  | "owner"
  | "ceo"
  | "manager"
  | "sales"
  | "warehouse"
  | "hr"
  | "accountant"
  | "employee";

export type ModuleKey =
  | "dashboard"
  | "crm"
  | "sales"
  | "purchase"
  | "inventory"
  | "invoicing"
  | "hr"
  | "projects"
  | "settings";

export type WidgetGroup = "financial" | "pipeline" | "operations" | "personal";

export type RoleDef = {
  key: RoleKey;
  name: string;
  description: string;
  modules: ModuleKey[];
  widgets: WidgetGroup[];
};

const ALL_MODULES: ModuleKey[] = [
  "dashboard",
  "crm",
  "sales",
  "purchase",
  "inventory",
  "invoicing",
  "hr",
  "projects",
];

export const ROLES: Record<RoleKey, RoleDef> = {
  owner: {
    key: "owner",
    name: "Owner",
    description: "Full control, including billing & workspace settings",
    modules: [...ALL_MODULES, "settings"],
    widgets: ["financial", "pipeline", "operations", "personal"],
  },
  ceo: {
    key: "ceo",
    name: "CEO",
    description: "All modules, read-only financials",
    modules: ALL_MODULES,
    widgets: ["financial", "pipeline", "operations", "personal"],
  },
  manager: {
    key: "manager",
    name: "Manager",
    description: "Department-level access across operations",
    modules: ["dashboard", "crm", "sales", "purchase", "inventory", "projects"],
    widgets: ["pipeline", "operations", "personal"],
  },
  sales: {
    key: "sales",
    name: "Sales",
    description: "CRM, Sales & Invoicing",
    modules: ["dashboard", "crm", "sales", "invoicing"],
    widgets: ["pipeline", "personal"],
  },
  warehouse: {
    key: "warehouse",
    name: "Warehouse",
    description: "Inventory & Purchase",
    modules: ["dashboard", "inventory", "purchase"],
    widgets: ["operations", "personal"],
  },
  hr: {
    key: "hr",
    name: "HR",
    description: "Human Resources",
    modules: ["dashboard", "hr", "projects"],
    widgets: ["personal"],
  },
  accountant: {
    key: "accountant",
    name: "Accountant",
    description: "Accounting & Reports",
    modules: ["dashboard", "invoicing", "purchase"],
    widgets: ["financial", "personal"],
  },
  employee: {
    key: "employee",
    name: "Employee",
    description: "Limited self-service",
    modules: ["dashboard", "projects"],
    widgets: ["personal"],
  },
};

export const ROLE_LIST = Object.values(ROLES);
