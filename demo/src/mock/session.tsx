/*
 * Session state — REAL authentication, demo role switching (Refs issue #103).
 *
 * login/logout/restore go through the actual Odoo backend (src/api/odoo.ts):
 * credentials are checked against PostgreSQL and a real session cookie is
 * kept. The ROLE the UI renders for (sidebar/widget visibility) remains a
 * demo-only switcher until the real role mapping ships with P1-T08/P1-T09.
 */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ROLES, type RoleKey, type RoleDef } from "../lib/roles";
import { odooApi } from "../api/odoo";

interface SessionValue {
  /** True while we check for an existing server session on first load. */
  booting: boolean;
  authed: boolean;
  role: RoleKey;
  userName: string;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  setRole: (r: RoleKey) => void;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [booting, setBooting] = useState(true);
  const [authed, setAuthed] = useState(false);
  const [userName, setUserName] = useState("");
  const [role, setRoleState] = useState<RoleKey>("owner");

  // Session restore: if a valid session cookie exists (page reload), pick it
  // up. The endpoint rejects when there is no session — that's simply
  // "not logged in", never an error worth surfacing.
  useEffect(() => {
    let cancelled = false;
    odooApi
      .getSessionInfo()
      .then((session) => {
        if (!cancelled && session.uid) {
          setUserName(session.name ?? "User");
          setAuthed(true);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setBooting(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo<SessionValue>(
    () => ({
      booting,
      authed,
      role,
      userName,
      login: async (email: string, password: string) => {
        const session = await odooApi.authenticate(email, password);
        setUserName(session.name ?? "User");
        setAuthed(true);
      },
      logout: () => {
        odooApi.logout().catch(() => undefined);
        setAuthed(false);
        setUserName("");
      },
      setRole: (r: RoleKey) => setRoleState(r),
    }),
    [booting, authed, role, userName],
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

export function useRoleDef(): RoleDef {
  const { role } = useSession();
  return ROLES[role];
}
