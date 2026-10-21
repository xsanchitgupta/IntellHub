/* The static client and the AppSail backend do NOT share a Catalyst origin.
 *
 * The client is served from <project>.development.catalystserverless.in; the API
 * only answers on ksp-intelhub-core-<id>.development.catalystappsail.in. Assuming
 * one origin and leaving this empty was verified wrong against the live site —
 * every `/serverless/api/...` path shape on the client domain returns 404, so every
 * panel rendered empty with nothing in the UI to say why.
 *
 * So the deployed bundle must carry the absolute API origin, set at build time via
 * NEXT_PUBLIC_API_URL, and the backend must allow that origin in ALLOWED_ORIGINS.
 * Leaving NEXT_PUBLIC_API_URL unset in a production build is a deployment error, not
 * a fallback worth having: an empty base is exactly the silent-404 case above.
 *
 * `??` rather than `||` so an explicitly-configured empty string is still honoured
 * for a genuinely same-origin deployment, instead of silently falling back to
 * localhost — which is what once baked `http://127.0.0.1:9000` into the bundle.
 */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "production" ? "" : "http://127.0.0.1:9000");

export const api = (path: string) => `${API_URL}/serverless/api${path}`;

export type AgentAction =
  | "open_case"
  | "open_suspect"
  | "show_heatmap"
  | "show_network"
  | "show_dispatch"
  | "show_anomalies"
  | "show_analytics"
  | "show_models"
  | "recent_cases"
  | "show_registry"
  | "show_suspects"
  | "open_copilot"
  | "guide"
  | "show_surveillance"
  | "answer";

export interface AgentSuggestion {
  label: string;
  query: string;
}

export interface AgentResult {
  action: AgentAction;
  params: {
    case_id?: string;
    suspect_id?: string;
    district?: string;
    crime_type?: string;
    time_of_day?: string;
    kind?: string;
    page?: string;
    q?: string;
    query?: string;
    target?: string;
    mode?: string;
    sort?: string;
    repeat_only?: boolean | string;
    rowdy_sheeter?: boolean | string;
    active_warrant?: boolean | string;
  };
  reply: string;
  suggestions?: AgentSuggestion[];
  transcript?: string;
  /** Short spoken form of `reply` — the first sentence or two, without markup. */
  speech?: string;
  /** Records the copilot read to produce this answer, in order. */
  trace?: AgentTraceStep[];
  /** The Copilot conversation this exchange was saved to; send it back to continue it. */
  conversation_id?: string;
  /** "llm:<provider>" when a language model answered, "deterministic" otherwise. */
  mode?: string;
  /** Identifier verification performed server-side. `grounded: false` means the
   *  answer named a case, offender or officer that is not in the corpus. */
  grounding?: {
    checked: number;
    verified: number;
    unknown: { kind: string; value: string }[];
    grounded: boolean;
  };
}

export interface AgentTraceStep {
  tool: string;
  label: string;
  detail: string;
  result: string;
}

export type ChatTurn = { role: "user" | "assistant"; content: string };

export interface AgentContext {
  case_id?: string;
  suspect_id?: string;
}

export async function askAgent(
  query: string,
  history: ChatTurn[] = [],
  lang = "en",
  page?: string,
  context?: AgentContext,
  conversationId?: string | null
): Promise<AgentResult> {
  // Every exchange is saved to the officer's Copilot history; the id keeps one chat in one thread.
  const res = await apiFetch("/agent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, history, lang, page, context, conversation_id: conversationId ?? undefined }),
  });
  return res.json();
}

/* ------------------------------------------------------------------ auth
 * Person-level endpoints are role-gated server-side. The token below only attaches
 * the header; every access decision is made by the backend, and nothing here can
 * grant access the API has not already granted.
 *
 * Two shapes of refusal, deliberately different:
 *  - Aggregate surfaces that merely *contain* a person-level block (/stats,
 *    /analytics) stay readable and return that block pseudonymised, so the control
 *    is visible without credentials.
 *  - Dossiers and registries return 403. There is no partial version of an
 *    individual's file worth rendering.
 */
export const TOKEN_KEY = "ksp.auth.token";

/**
 * Fired on `window` whenever this tab's token changes (sign-in, sign-out, a demo
 * persona switch). The nav, the landing page and every page that reads the
 * identity must agree on who is signed in; before this each surface fetched
 * /auth/me on its own and they drifted apart until a reload. Other tabs learn of
 * the change through the browser's own `storage` event on TOKEN_KEY.
 */
export const AUTH_EVENT = "ksp:auth-changed";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;   // storage blocked (private mode) — behave as signed out
  }
}

/** `notify: false` is for a caller that is itself reacting to an identity change
 *  (clearing a token the server no longer honours) and must not trigger another. */
export function setToken(token: string | null, notify = true) {
  if (typeof window === "undefined") return;
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage blocked: the session simply does not persist */
  }
  if (notify) window.dispatchEvent(new Event(AUTH_EVENT));
}

/** An API failure that remembers its status, so callers can distinguish
 *  "you may not see this" from "the backend is down". Without the status the UI
 *  can only ever show one generic error for two very different situations. */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message?: string) {
    super(message || `Request failed ${status}`);
    this.name = "ApiError";
    this.status = status;
  }
  /** Refused for lack of a role, rather than unavailable. */
  get isForbidden() {
    return this.status === 403 || this.status === 401;
  }
}

export function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

/**
 * The only way to call the API. Attaches the bearer token and surfaces the status
 * as an ApiError, so a 403 from a role gate is distinguishable from a dead backend.
 *
 * Every raw `fetch` in the app used to bypass authHeaders(), which meant the six
 * routes gated in the access-control work returned 403 to signed-in users too.
 */
export async function apiFetch(path: string, init: RequestInit = {}) {
  const res = await apiFetchRaw(path, init);
  if (!res.ok) throw new ApiError(res.status);
  return res;
}

/**
 * Same, but returns the Response whatever the status.
 *
 * For endpoints whose *failure body is the point*: /health answers 503 when the
 * platform is serving but degraded, and that response carries `degraded_reasons`.
 * Throwing it away leaves the operator with a blank panel at precisely the moment
 * the panel has something to say.
 */
export async function apiFetchRaw(path: string, init: RequestInit = {}) {
  return fetch(api(path), {
    ...init,
    headers: { ...(init.headers || {}), ...authHeaders() },
  });
}

/* ------------------------------------------------------------ roles & scope
 * The role set grew from three tiers to a KSP rank structure (SI, SHO, DSP, SP,
 * DGP, admin) with a jurisdiction carried in the signed token. Every field added
 * here is optional on purpose: the UI must keep working against a backend that
 * only knows the three legacy roles, and must never fill a gap with a guess about
 * what a role may do. Permissions are rendered from what the API returns, never
 * from a matrix hardcoded in the client.
 */
export type ScopeLevel = "state" | "district" | "station";

export interface Jurisdiction {
  level: ScopeLevel;
  district?: string | null;
  station?: string | null;
}

export interface IdentityCore {
  user: string | null;
  role: string;
  authenticated: boolean;
  role_label?: string;
  tier?: number;
  scope?: Jurisdiction;
  permissions?: string[];
}

export interface Identity {
  identity: IdentityCore;
  roles: string[];
  demo_mode: boolean;
  // Usernames and roles only — the API deliberately does not serve passwords.
  demo_accounts: { username: string; role: string }[];
  insecure_cleartext_accounts?: string[];
  policy: Record<string, string>;
  warning?: string | null;
}

export interface RoleInfo {
  role: string;
  label: string;
  tier: number;
  scope_level: ScopeLevel;
  description: string;
  permissions: string[];
}

export interface DemoPersona {
  id: string;
  label: string;
  role: string;
  role_label: string;
  scope_level: ScopeLevel;
  district: string | null;
  station: string | null;
  description: string;
  /** The persona's district can be swapped for any of `DemoInfo.districts`. */
  switchable_district: boolean;
}

export interface DemoInfo {
  demo_mode: true;
  districts: string[];
  personas: DemoPersona[];
}

export interface LoginResult {
  token: string;
  role: string;
  username: string;
  expires_in?: number;
  identity?: IdentityCore;
}

/**
 * A refused sign-in, with the reason kept apart from the message.
 *
 * "Wrong password" and "this account is locked for four more minutes" need
 * different screens: the first invites another try, the second must tell the
 * officer to stop, or they burn the remaining attempts against a lock they cannot
 * see. A bare `Error("Invalid credentials")` collapsed both into the first.
 */
export class LoginError extends ApiError {
  code: string;
  /** Seconds until the account may try again, on a 429. */
  retryAfter: number | null;
  constructor(status: number, code: string, retryAfter: number | null = null) {
    super(status, code);
    this.name = "LoginError";
    this.code = code;
    this.retryAfter = retryAfter;
  }
  get isLockedOut() {
    return this.status === 429;
  }
}

async function readLoginResponse(res: Response): Promise<LoginResult> {
  let body: Record<string, unknown> = {};
  try {
    body = await res.json();
  } catch {
    /* an empty or HTML body — the status alone still says what happened */
  }
  if (!res.ok) {
    const retry = Number(body.retry_after_seconds);
    throw new LoginError(
      res.status,
      typeof body.error === "string" ? body.error : `http_${res.status}`,
      res.status === 429 && Number.isFinite(retry) ? Math.max(0, Math.round(retry)) : null,
    );
  }
  const data = body as unknown as LoginResult;
  if (!data.token) throw new LoginError(502, "no_token");
  setToken(data.token);
  return data;
}

export async function login(username: string, password: string): Promise<LoginResult> {
  const res = await fetch(api("/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return readLoginResponse(res);
}

/**
 * Discards the token in this browser.
 *
 * Tokens are stateless signed payloads, so there is no server session to end: a
 * copied token stays valid until it expires (TOKEN_TTL_SECONDS, eight hours) or
 * the signing secret is rotated. Signing out protects this device, not a token
 * that has already leaked.
 */
export function logout() {
  setToken(null);
}

export async function whoami(): Promise<Identity> {
  return getJSON<Identity>("/auth/me");
}

export interface RoleCatalog {
  roles: RoleInfo[];
  /** Every permission the server knows, in its own order, with what it unlocks. */
  permissions?: { permission: string; description: string }[];
  /** What each scope level restricts, in the server's words. */
  scope_levels?: Partial<Record<ScopeLevel, string>>;
}

/** GET /auth/roles in full: roles, the permission glossary and the scope levels. */
export async function getRoleCatalog(): Promise<RoleCatalog> {
  const data = await getJSON<RoleCatalog>("/auth/roles");
  return {
    ...data,
    roles: Array.isArray(data.roles) ? data.roles : [],
    permissions: Array.isArray(data.permissions) ? data.permissions : undefined,
  };
}

/** The role catalogue with each role's permissions, as the API enforces them. */
export async function getRoles(): Promise<RoleInfo[]> {
  return (await getRoleCatalog()).roles;
}

/**
 * Demo personas, or `null` when the deployment is not in demo mode.
 *
 * The endpoint answers 404 outside demo mode, which is the expected state of a
 * real deployment, not a fault — so it is returned as `null` for callers to hide
 * the persona UI, while any other failure still throws.
 */
export async function demoPersonas(): Promise<DemoInfo | null> {
  try {
    const data = await getJSON<DemoInfo>("/auth/demo");
    return data && Array.isArray(data.personas) ? data : null;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

/** Sign in as a demo persona; for a switchable one, `district` replaces its default. */
export async function demoLogin(persona: string, district?: string | null): Promise<LoginResult> {
  const res = await fetch(api("/auth/demo"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(district ? { persona, district } : { persona }),
  });
  return readLoginResponse(res);
}


export async function getJSON<T = any>(path: string): Promise<T> {
  const res = await fetch(api(path), { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status);

  // A 2xx with an empty body is a real thing here, not a theoretical one: the
  // Catalyst gateway answers 200 with no content while the AppSail container is
  // restarting. Calling res.json() on that throws the browser's own
  // "Unexpected end of JSON input", which surfaced to users as the reason a case
  // dossier failed to load — a message about JSON parsing when the actual cause
  // was a backend that was not up yet.
  const body = await res.text();
  if (!body.trim()) {
    throw new ApiError(
      503,
      "The intelligence core returned an empty response — it is most likely " +
        "restarting. Retry in a few seconds.");
  }
  try {
    return JSON.parse(body) as T;
  } catch {
    // Equally: an HTML error page from the gateway. Report what it was, not how
    // the parser failed.
    throw new ApiError(
      502,
      "The intelligence core returned a malformed response instead of JSON.");
  }
}
