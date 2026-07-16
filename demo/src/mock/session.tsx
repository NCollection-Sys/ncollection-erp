/*
 * Mock session — who is "logged in" and their role. The demo exposes a role
 * switcher (in the topbar) so a reviewer can see the role-aware UI change
 * without needing separate accounts. In production this is replaced by the
 * real Odoo session / res.users group membership.
 */
import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { RoleKey } from "../lib/roles";
import { ROLES } from "../lib/roles";

type SessionValue = {
  authed: boolean;
  role: RoleKey;
  userName: string;
  login: () => void;
  logout: () => void;
  setRole: (r: RoleKey) => void;
};

const SessionContext = createContext<SessionValue | null>(null);

const ROLE_USER: Record<RoleKey, string> = {
  owner: "Layla Al Nuaimi",
  ceo: "Omar Haddad",
  manager: "Sara Mansour",
  sales: "Yousef Karim",
  warehouse: "Bilal Ahmed",
  hr: "Noura Saleh",
  accountant: "Fatima Rahmani",
  employee: "Aisha Darwish",
};

export function SessionProvider({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(false);
  const [role, setRoleState] = useState<RoleKey>("owner");

  const value = useMemo<SessionValue>(
    () => ({
      authed,
      role,
      userName: ROLE_USER[role],
      login: () => setAuthed(true),
      logout: () => setAuthed(false),
      setRole: (r) => setRoleState(r),
    }),
    [authed, role],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used within SessionProvider");
  return ctx;
}

export function useRoleDef() {
  const { role } = useSession();
  return ROLES[role];
}
