/*
 * Real Odoo API layer (Refs GitHub issue #103).
 *
 * The ONLY authenticated backend calls in the demo: login, logout, session
 * restore, and signup. Everything else still reads mock data from
 * src/mock/data.ts. Requests go through the Vite dev proxy (vite.config.ts),
 * so the browser sees one origin and Odoo's session_id cookie just works.
 *
 * Verified against Odoo 19 (see the PR for the curl transcripts):
 *   - routes are JSON-RPC (`type='jsonrpc'`)
 *   - multi-database servers select the DB via the `X-Odoo-Database` header
 *   - a wrong password raises `odoo.exceptions.AccessDenied`
 */

const ODOO_DB: string =
  (import.meta.env.VITE_ODOO_DB as string | undefined) ?? "ncollection";

interface JsonRpcErrorPayload {
  code: number;
  message: string;
  data?: { name?: string; message?: string };
}

interface JsonRpcResponse<T> {
  jsonrpc: "2.0";
  id: number | null;
  result?: T;
  error?: JsonRpcErrorPayload;
}

/** Error carrying the Odoo exception name (e.g. "odoo.exceptions.AccessDenied"). */
export class OdooApiError extends Error {
  readonly exception: string;

  constructor(exception: string, message: string) {
    super(message);
    this.name = "OdooApiError";
    this.exception = exception;
  }
}

export function isAccessDenied(error: unknown): boolean {
  return (
    error instanceof OdooApiError &&
    error.exception === "odoo.exceptions.AccessDenied"
  );
}

interface RpcOptions {
  /**
   * Send the X-Odoo-Database header. ONLY for session-less public routes
   * (signup): on a multi-DB server it selects the database — but Odoo also
   * treats such requests as stateless and will NOT issue a session cookie,
   * so it must never be sent on authenticate/session calls (verified: with
   * the header, /web/session/authenticate returns zero Set-Cookie headers).
   */
  dbHeader?: boolean;
}

async function rpc<T>(
  path: string,
  params: Record<string, unknown>,
  options: RpcOptions = {},
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (options.dbHeader) headers["X-Odoo-Database"] = ODOO_DB;
  const response = await fetch(path, {
    method: "POST",
    headers,
    credentials: "same-origin",
    body: JSON.stringify({ jsonrpc: "2.0", method: "call", params }),
  });
  if (!response.ok) {
    throw new OdooApiError("http_error", `HTTP ${response.status} on ${path}`);
  }
  const payload = (await response.json()) as JsonRpcResponse<T>;
  if (payload.error) {
    throw new OdooApiError(
      payload.error.data?.name ?? "server_error",
      payload.error.data?.message ?? payload.error.message,
    );
  }
  return payload.result as T;
}

export interface OdooSession {
  uid: number | false;
  name?: string;
  username?: string;
  db?: string;
}

export interface SignupResult {
  success: boolean;
  error?: "missing_fields" | "invalid_email" | "weak_password" | "email_exists";
}

export const odooApi = {
  /** Log in against the real database; sets the session cookie on success. */
  authenticate(login: string, password: string): Promise<OdooSession> {
    return rpc<OdooSession>("/web/session/authenticate", {
      db: ODOO_DB,
      login,
      password,
    });
  },

  /**
   * Restore an existing session (page reload). The route is auth='user', so
   * with no valid session it REJECTS — callers treat that as "not logged in".
   */
  getSessionInfo(): Promise<OdooSession> {
    return rpc<OdooSession>("/web/session/get_session_info", {});
  },

  /** Invalidate the server-side session. */
  logout(): Promise<void> {
    return rpc<void>("/web/session/destroy", {});
  },

  /** Create a workspace user via our ncollection_core controller. */
  signup(name: string, email: string, password: string): Promise<SignupResult> {
    return rpc<SignupResult>(
      "/ncollection/api/signup",
      { name, email, password },
      { dbHeader: true },
    );
  },
};
