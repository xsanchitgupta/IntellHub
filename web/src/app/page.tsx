"use client";

/**
 * The front door: sign in, with the register's live state beside the form.
 *
 * The dashboard used to be the root, which meant the first thing anyone saw was a
 * state-wide command overview with no indication of who they were or what they
 * were allowed to see. Person-level surfaces then refused them one page at a time.
 * Asking first — and offering a clearly labelled aggregates-only way in — states
 * the access model before it is enforced.
 *
 * It is laid out like the records office it fronts: a ruled register of what is on
 * file, the districts whose recorded crime has jumped this month, and a plain form.
 * Everything numeric is read from GET /stats when the page loads; a figure the API
 * did not return is hidden, not estimated, and the map marks only the districts the
 * trend alerts name. Nothing on the page moves.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ArrowRight, Building2, Eye, EyeOff, Landmark, LoaderCircle, MapPin } from "lucide-react";
import { ApiError, LoginError, demoLogin, getJSON, login, type DemoInfo, type DemoPersona } from "@/lib/api";
import { LANGUAGES, useI18n } from "@/lib/i18n";
import { safeNext, useDemoInfo, useHasToken, useIdentity } from "@/lib/session";

const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";
const NUMBER = new Intl.NumberFormat("en-IN");

type T = (key: string, fallback: string) => string;

/* ------------------------------------------------------------------ small hooks */

const WIDE = "(min-width: 1280px)";

function subscribeWide(cb: () => void) {
  const m = window.matchMedia(WIDE);
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
}

/** Whether the district map has room beside the register (and its GeoJSON is worth
 *  fetching): xl and up. Below that the register and the alerts say the same thing. */
function useWide() {
  return useSyncExternalStore(subscribeWide, () => window.matchMedia(WIDE).matches, () => false);
}

function subscribePopstate(cb: () => void) {
  window.addEventListener("popstate", cb);
  return () => window.removeEventListener("popstate", cb);
}

/** `location.search`, read without useSearchParams — which would force a Suspense
 *  boundary around the whole page in a static export for one query parameter. */
function useSearch() {
  return useSyncExternalStore(subscribePopstate, () => window.location.search, () => "");
}

/* ------------------------------------------------------------------ the register */

interface Alert {
  district?: string;
  crime_type?: string;
  recent_count?: number;
  expected_count?: number;
  spike_ratio?: number;
}

interface LandingStats {
  total_cases?: number;
  tracked_suspects?: number;
  districts?: string[];
  graph_edges?: number;
  trend_alerts?: Alert[];
}

const FIGURES: { key: string; en: string; pick: (s: LandingStats) => unknown }[] = [
  { key: "landing.stat.firs", en: "FIRs on record", pick: (s) => s.total_cases },
  { key: "landing.stat.offenders", en: "Offenders tracked", pick: (s) => s.tracked_suspects },
  { key: "landing.stat.districts", en: "Police districts", pick: (s) => s.districts?.length },
  { key: "landing.stat.links", en: "Graph relationships", pick: (s) => s.graph_edges },
];

function Rule({ children, aside }: { children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-3 border-b border-ink/80 pb-1.5">
      <h2 className="text-[10.5px] font-bold uppercase tracking-[0.2em] text-ink">{children}</h2>
      {aside && <span className="ml-auto text-[11px] text-muted">{aside}</span>}
    </div>
  );
}

function Register({ stats, failed, readAt, t }: { stats: LandingStats | null; failed: boolean; readAt: string | null; t: T }) {
  const rows = FIGURES.map((f) => ({ ...f, value: stats ? f.pick(stats) : undefined })).filter(
    (f) => !stats || (typeof f.value === "number" && Number.isFinite(f.value)),
  );
  return (
    <section aria-labelledby="register-title">
      <Rule aside={readAt ? `${t("landing.readAt", "Read")} ${readAt}` : undefined}>
        <span id="register-title">{t("landing.register", "On file")}</span>
      </Rule>
      {failed ? (
        <p className="py-3 text-[13px] text-muted">
          {t("landing.statsDown", "Live figures unavailable — the intelligence core did not answer.")}
        </p>
      ) : (
        <dl aria-busy={!stats}>
          {rows.map((f) => (
            <div key={f.key} className="flex items-baseline justify-between gap-4 border-b border-black/[0.09] py-2 short:py-1.5 tight:py-1">
              <dt className="text-[13px] text-muted">{t(f.key, f.en)}</dt>
              <dd className="text-display tabular text-[22px] font-semibold leading-none text-ink short:text-[19px]">
                {stats ? NUMBER.format(f.value as number) : <span className="shimmer inline-block h-5 w-20 rounded-sm align-middle" />}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

function Alerts({ alerts, t }: { alerts: Alert[] | undefined; t: T }) {
  if (!alerts) return null;
  return (
    <section aria-labelledby="alerts-title">
      <Rule aside={t("landing.alertsWindow", "against each district's own history")}>
        <span id="alerts-title">{t("landing.alerts", "Spikes, last 30 days")}</span>
      </Rule>
      {alerts.length === 0 ? (
        <p className="py-2.5 text-[13px] text-muted">{t("landing.noAlerts", "No district's recorded crime has jumped this month.")}</p>
      ) : (
        <table className="w-full text-[13px]">
          <tbody>
            {alerts.map((a, i) => (
              <tr key={i} className="border-b border-black/[0.09]">
                <td className="py-1.5 pr-3 font-semibold text-ink short:py-1">{a.district}</td>
                <td className="py-1.5 pr-3 text-muted short:py-1">{a.crime_type}</td>
                <td className="py-1.5 text-right tabular text-muted short:py-1">
                  <span className="font-semibold text-crimson">{a.recent_count}</span>
                  {a.expected_count != null && <> {t("landing.against", "against")} {a.expected_count} {t("landing.usual", "usual")}</>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ Karnataka map */

type Ring = number[][];
interface GeoFeature {
  properties: { district: string; label_lng: number; label_lat: number };
  geometry: { type: "Polygon"; coordinates: Ring[] } | { type: "MultiPolygon"; coordinates: Ring[][] };
}

const MAP_W = 300;
// Equirectangular, scaled by cos(15°N) — the state's middle latitude — so the
// outline is not stretched east-west. At this extent (11.6°–18.5°N) the error at
// the edges is a few percent, invisible at this size.
const COS_LAT0 = Math.cos((15 * Math.PI) / 180);

function rings(g: GeoFeature["geometry"]): Ring[] {
  return g.type === "Polygon" ? g.coordinates : g.coordinates.flat();
}

function projectMap(features: GeoFeature[]) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const f of features) {
    for (const ring of rings(f.geometry)) {
      for (const [lng, lat] of ring) {
        const x = lng * COS_LAT0, y = -lat;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  const s = MAP_W / (maxX - minX);
  const pt = (lng: number, lat: number): [number, number] => [
    Math.round((lng * COS_LAT0 - minX) * s * 10) / 10,
    Math.round((-lat - minY) * s * 10) / 10,
  ];
  const districts = features.map((f) => ({
    name: f.properties.district,
    d: rings(f.geometry)
      .map((ring) => "M" + ring.map(([lng, lat]) => pt(lng, lat).join(",")).join("L") + "Z")
      .join(""),
    hq: pt(f.properties.label_lng, f.properties.label_lat),
  }));
  return { width: MAP_W, height: Math.ceil((maxY - minY) * s), districts };
}

function KarnatakaMap({ marked, t }: { marked: Set<string>; t: T }) {
  const [features, setFeatures] = useState<GeoFeature[] | null>(null);
  const titleId = useId();

  useEffect(() => {
    let alive = true;
    fetch(`${BASE}/geo/karnataka-districts.geojson`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => {
        if (alive && Array.isArray(d?.features) && d.features.length) setFeatures(d.features);
      })
      // No outline is better than an invented one: on failure there is simply no map.
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const map = useMemo(() => (features ? projectMap(features) : null), [features]);
  if (!map) return null;

  return (
    <figure className="m-0">
      <svg viewBox={`-4 -4 ${map.width + 8} ${map.height + 8}`} role="img" aria-labelledby={titleId} className="h-auto max-h-[58vh] w-full">
        <title id={titleId}>
          {t("landing.mapTitle", "Karnataka's police districts")}
          {marked.size ? ` — ${[...marked].join(", ")}` : ""}
        </title>
        {map.districts.map((d) => (
          <path
            key={d.name}
            d={d.d}
            fill={marked.has(d.name) ? "rgba(220,43,37,0.16)" : "#fff"}
            stroke={marked.has(d.name) ? "#b3231e" : "rgba(29,29,31,0.32)"}
            strokeWidth={marked.has(d.name) ? 0.9 : 0.5}
            strokeLinejoin="round"
          >
            <title>{d.name}</title>
          </path>
        ))}
        {map.districts
          .filter((d) => marked.has(d.name))
          .map((d) => (
            <text key={d.name} x={d.hq[0]} y={d.hq[1]} textAnchor="middle" dominantBaseline="middle"
                  className="fill-[#8f1c18] text-[9px] font-semibold" style={{ paintOrder: "stroke", stroke: "#fff", strokeWidth: 2.4 }}>
              {d.name}
            </text>
          ))}
      </svg>
      <figcaption className="mt-1 text-[11px] text-muted">
        {marked.size
          ? t("landing.mapMarked", "Shaded: the districts listed alongside.")
          : t("landing.mapTitle", "Karnataka's police districts")}
      </figcaption>
    </figure>
  );
}

/* ------------------------------------------------------------------ sign-in */

type FormError =
  | { kind: "invalid" }
  | { kind: "locked"; seconds: number | null }
  | { kind: "network" }
  | { kind: "server"; status: number }
  | { kind: "demo"; status: number };

function errorText(e: FormError, t: T) {
  switch (e.kind) {
    case "invalid":
      return t("landing.err.invalid", "Those credentials were not recognised. Check the address and password and try again.");
    case "locked":
      return t("landing.err.locked", "Too many failed attempts — sign-in for this account is paused.");
    case "network":
      return t("landing.err.network", "Could not reach the intelligence core. Check that the backend is running, then retry.");
    case "demo":
      return e.status === 404
        ? t("landing.err.demoOff", "Demo access is switched off on this deployment.")
        : `${t("landing.err.demo", "That demo persona could not be opened")} (HTTP ${e.status}).`;
    default:
      return `${t("landing.err.server", "Sign-in failed on the server")} (HTTP ${e.status}).`;
  }
}

const TILES: { id: string; key: string; en: string; icon: typeof Landmark }[] = [
  { id: "dgp", key: "landing.tile.state", en: "State", icon: Landmark },
  { id: "sp", key: "landing.tile.district", en: "District", icon: Building2 },
  { id: "sho", key: "landing.tile.station", en: "Station", icon: MapPin },
];

function personaScope(p: DemoPersona, district: string | null, t: T) {
  if (p.scope_level === "state") return t("profile.allDistricts", "All 31 districts");
  if (p.scope_level === "station") return [p.station, p.district].filter(Boolean).join(", ");
  return district || p.district || "";
}

function DemoRows({
  info,
  busy,
  onEnter,
  t,
}: {
  info: DemoInfo;
  busy: string | null;
  onEnter: (p: DemoPersona, district?: string) => void;
  t: T;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const baseId = useId();
  const tiles = TILES.map((tile) => ({ ...tile, persona: info.personas.find((p) => p.id === tile.id) })).filter(
    (x): x is typeof x & { persona: DemoPersona } => !!x.persona,
  );
  if (!tiles.length) return null;

  return (
    <ul className="divide-y divide-black/[0.08] rounded-[10px] border border-black/[0.12] bg-white">
      {tiles.map(({ id, key, en, icon: Icon, persona }) => {
        const switchable = persona.switchable_district && info.districts.length > 0;
        const district = switchable ? picked ?? persona.district ?? info.districts[0] : persona.district;
        const working = busy === id;
        const selectId = `${baseId}-${id}`;
        return (
          <li key={id} className="group flex items-center gap-2 pr-2 transition-colors hover:bg-black/[0.025]">
            <button
              type="button"
              disabled={!!busy}
              onClick={() => onEnter(persona, switchable ? district ?? undefined : undefined)}
              className="flex min-h-[46px] min-w-0 flex-1 items-center gap-3 py-2 pl-3 text-left disabled:cursor-wait short:min-h-[40px] short:py-1.5 tight:min-h-[36px] tight:py-1"
            >
              {working ? (
                <LoaderCircle className="h-4 w-4 shrink-0 animate-spin text-muted" aria-hidden="true" />
              ) : (
                <Icon className="h-4 w-4 shrink-0 text-muted" strokeWidth={1.7} aria-hidden="true" />
              )}
              <span className="min-w-0">
                <span className="block text-[13px] font-semibold leading-tight text-ink">{t(key, en)}</span>
                <span className="block truncate text-[12px] leading-snug text-muted">
                  {persona.role_label}
                  {!switchable && personaScope(persona, district ?? null, t) ? ` · ${personaScope(persona, district ?? null, t)}` : ""}
                </span>
              </span>
              {!switchable && (
                <ArrowRight className="ml-auto h-4 w-4 shrink-0 text-faint transition-colors group-hover:text-ink" aria-hidden="true" />
              )}
            </button>
            {switchable && (
              <>
                <label htmlFor={selectId} className="sr-only">
                  {t("landing.pickDistrict", "District for the")} {persona.role_label}
                </label>
                <select
                  id={selectId}
                  value={district ?? ""}
                  disabled={!!busy}
                  onChange={(e) => setPicked(e.target.value)}
                  className="h-8 w-[124px] shrink-0 cursor-pointer rounded-md border border-black/[0.14] bg-white px-1.5 text-[12px] text-ink outline-none hover:border-black/30 focus:border-ink"
                >
                  {info.districts.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  disabled={!!busy}
                  onClick={() => onEnter(persona, district ?? undefined)}
                  aria-label={`${t("landing.enterAs", "Enter as")} ${persona.role_label}, ${district}`}
                  className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-faint transition-colors hover:bg-ink hover:text-white disabled:cursor-wait"
                >
                  <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </button>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}

type Core = "checking" | "online" | "offline";

/** Whether the intelligence core answered the landing page's own request. Said before
 *  anyone types a password, so a dead backend is not discovered as a failed sign-in. */
function CoreStatus({ core, t }: { core: Core; t: T }) {
  const tone = core === "online" ? "bg-emerald" : core === "offline" ? "bg-crimson" : "bg-faint";
  const text =
    core === "online" ? t("landing.coreOnline", "Core online")
    : core === "offline" ? t("landing.coreOffline", "Core unreachable — sign-in will fail")
    : t("landing.coreChecking", "Connecting…");
  return (
    <span role="status" className={`inline-flex items-center gap-1.5 text-[11.5px] ${core === "offline" ? "font-semibold text-crimson" : "text-muted"}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${tone}`} aria-hidden="true" />
      {text}
    </span>
  );
}

const field =
  "h-11 w-full rounded-lg border border-black/[0.16] bg-white px-3.5 text-[14px] text-ink outline-none transition-colors placeholder:text-faint hover:border-black/30 focus:border-ink focus:ring-2 focus:ring-ink/10 short:h-10 tight:h-9";

function SignIn({ nextPath, onSignedIn, core, t }: { nextPath: string; onSignedIn: () => void; core: Core; t: T }) {
  const { authenticated, loading, error: identityDown } = useIdentity();
  const hasToken = useHasToken();
  const { info: demo } = useDemoInfo();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<FormError | null>(null);
  const [lockedFor, setLockedFor] = useState(0);
  const emailId = useId();
  const passwordId = useId();
  const errorId = useId();

  // Count the lock down in place, so the officer can see when to try again
  // instead of spending attempts against a lock they cannot see.
  useEffect(() => {
    if (lockedFor <= 0) return;
    const id = setTimeout(() => {
      if (lockedFor <= 1) setError(null);
      setLockedFor(lockedFor - 1);
    }, 1000);
    return () => clearTimeout(id);
  }, [lockedFor]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || lockedFor > 0) return;
    setBusy("password");
    setError(null);
    try {
      await login(email.trim(), password);
      setPassword("");
      onSignedIn();
    } catch (err) {
      if (err instanceof LoginError && err.isLockedOut) {
        setError({ kind: "locked", seconds: err.retryAfter });
        setLockedFor(err.retryAfter ?? 0);
      } else if (err instanceof ApiError && err.status === 401) {
        setError({ kind: "invalid" });
      } else if (err instanceof ApiError) {
        setError({ kind: "server", status: err.status });
      } else {
        setError({ kind: "network" });
      }
    } finally {
      setBusy(null);
    }
  };

  const enterAs = async (p: DemoPersona, district?: string) => {
    if (busy) return;
    setBusy(p.id);
    setError(null);
    try {
      await demoLogin(p.id, district);
      onSignedIn();
    } catch (err) {
      setError(err instanceof ApiError ? { kind: "demo", status: err.status } : { kind: "network" });
    } finally {
      setBusy(null);
    }
  };

  // A held token is checked before the form is offered, so a returning officer is
  // not asked for credentials they do not need to type.
  const restoring = hasToken && (loading || authenticated);

  return (
    <section aria-labelledby="signin-title" className="w-full max-w-[400px]">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="signin-title" className="text-display text-[28px] font-bold tracking-tight text-ink short:text-[24px]">
          {t("common.signIn", "Sign in")}
        </h2>
        <CoreStatus core={core} t={t} />
      </div>
      <p className="mt-1 text-[13.5px] text-muted">{t("landing.useOfficial", "Use your official @ksp.gov.in address")}</p>
      {nextPath && (
        <p className="mt-1.5 text-[12px] text-muted tight:hidden">
          {t("landing.returnTo", "You will return to")} <code className="rounded bg-black/[0.05] px-1.5 py-0.5 text-ink">{nextPath}</code>
        </p>
      )}

      {restoring ? (
        <div role="status" className="mt-7 flex min-h-[200px] flex-col items-center justify-center gap-3 rounded-[10px] border border-black/[0.1] text-center">
          <LoaderCircle className="h-5 w-5 animate-spin text-muted" aria-hidden="true" />
          <p className="text-[13.5px] text-ink">
            {authenticated ? t("landing.opening", "Signed in — opening your workspace…") : t("landing.checking", "Restoring your session…")}
          </p>
        </div>
      ) : (
        <form onSubmit={submit} noValidate className="mt-6 space-y-4 short:mt-4 short:space-y-3 tight:mt-3 tight:space-y-2.5" aria-describedby={error ? errorId : undefined}>
          <div>
            <label htmlFor={emailId} className="mb-1.5 block text-[12.5px] font-semibold text-ink">
              {t("landing.email", "Official email")}
            </label>
            {/* type="text", not "email": the legacy demo accounts (analyst, io, sp)
                are bare usernames, and the browser would refuse them. */}
            <input
              id={emailId}
              type="text"
              inputMode="email"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@ksp.gov.in"
              aria-invalid={error?.kind === "invalid" || undefined}
              className={field}
            />
          </div>
          <div>
            <label htmlFor={passwordId} className="mb-1.5 block text-[12.5px] font-semibold text-ink">
              {t("landing.password", "Password")}
            </label>
            <div className="relative">
              <input
                id={passwordId}
                type={reveal ? "text" : "password"}
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => setCapsLock(e.getModifierState?.("CapsLock") ?? false)}
                onKeyUp={(e) => setCapsLock(e.getModifierState?.("CapsLock") ?? false)}
                onBlur={() => setCapsLock(false)}
                aria-invalid={error?.kind === "invalid" || undefined}
                className={`${field} pr-11`}
              />
              <button
                type="button"
                onClick={() => setReveal((r) => !r)}
                aria-pressed={reveal}
                aria-controls={passwordId}
                aria-label={reveal ? t("landing.hide", "Hide password") : t("landing.show", "Show password")}
                className="absolute right-1 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-md text-muted transition-colors hover:text-ink short:h-8 short:w-8"
              >
                {reveal ? <EyeOff className="h-4 w-4" aria-hidden="true" /> : <Eye className="h-4 w-4" aria-hidden="true" />}
              </button>
            </div>
            {capsLock && (
              <p role="status" className="mt-1.5 text-[12px] font-semibold text-brand-deep">
                {t("landing.capsLock", "Caps Lock is on")}
              </p>
            )}
          </div>

          {error && (
            <div id={errorId} role="alert" className="border-l-2 border-crimson bg-crimson/[0.06] px-3.5 py-2.5 text-[12.5px] leading-relaxed text-[#9c1d18]">
              {errorText(error, t)}
              {/* An alert re-announces whenever its content changes, so a ticking
                  number inside it would be read out every second. The wait is
                  announced once; the visible countdown is not announced at all. */}
              {error.kind === "locked" && error.seconds ? (
                <span className="sr-only">
                  {" "}
                  {t("landing.retryIn", "Try again in")} {error.seconds}s
                </span>
              ) : null}
              {error.kind === "locked" && lockedFor > 0 && (
                <span aria-hidden="true" className="mt-0.5 block font-semibold tabular">
                  {t("landing.retryIn", "Try again in")} {lockedFor}s
                </span>
              )}
            </div>
          )}

          <button
            type="submit"
            disabled={!!busy || lockedFor > 0 || !email.trim() || !password}
            className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-ink text-[14px] font-semibold text-white transition-colors hover:bg-black active:bg-black disabled:cursor-not-allowed disabled:bg-ink/35 short:h-10 tight:h-9"
          >
            {busy === "password" && <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {busy === "password" ? t("landing.signingIn", "Signing in…") : t("common.signIn", "Sign in")}
          </button>
        </form>
      )}

      {identityDown && hasToken && (
        <p className="mt-3 text-[12px] text-muted">
          {t("landing.restoreFailed", "Your saved session could not be checked because the intelligence core did not answer.")}
        </p>
      )}

      {!restoring && demo && demo.personas.length > 0 && (
        <div className="mt-8 short:mt-5 tight:mt-3.5">
          <p className="mb-2 text-[12.5px] font-semibold text-ink">{t("landing.explore", "Or explore without an account")}</p>
          <DemoRows info={demo} busy={busy} onEnter={enterAs} t={t} />
          <p className="mt-2 text-[11.5px] leading-relaxed text-muted tight:hidden">
            {t("landing.demoNote", "Demo mode only: each opens a ready-made persona with that jurisdiction. Every entry is audited.")}
          </p>
        </div>
      )}

      {!restoring && (
        <Link
          prefetch={false}
          href="/overview"
          className="mt-5 inline-flex items-center gap-1.5 text-[13px] font-semibold text-ink underline decoration-black/25 underline-offset-4 transition-colors hover:decoration-ink short:mt-3.5 tight:mt-2.5"
        >
          {t("landing.analyst", "Continue as analyst (aggregates only)")}
          <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
        </Link>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ page */

export default function Landing() {
  const { t, lang, setLang } = useI18n();
  const router = useRouter();
  const { authenticated } = useIdentity();
  const hasToken = useHasToken();
  const wide = useWide();
  const search = useSearch();
  const nextPath = useMemo(() => safeNext(new URLSearchParams(search).get("next"), ""), [search]);

  const [stats, setStats] = useState<LandingStats | null>(null);
  const [statsFailed, setStatsFailed] = useState(false);
  const [readAt, setReadAt] = useState<string | null>(null);

  useEffect(() => {
    getJSON<LandingStats>("/stats")
      .then((s) => {
        setStats(s);
        setReadAt(
          new Date().toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }) + " IST",
        );
      })
      .catch(() => setStatsFailed(true));
  }, []);

  // `replace`, so Back from the workspace does not land on a sign-in form for a
  // session that is still open. One navigation per visit, whichever path fires.
  const leaving = useRef(false);
  const go = useCallback(() => {
    if (leaving.current) return;
    leaving.current = true;
    router.replace(nextPath || "/overview");
  }, [router, nextPath]);

  useEffect(() => {
    if (authenticated && hasToken) go();
  }, [authenticated, hasToken, go]);

  const marked = useMemo(
    () => new Set((stats?.trend_alerts ?? []).map((a) => a.district).filter((d): d is string => !!d)),
    [stats],
  );

  return (
    // Its own opaque paper, over the layout's backdrop. On desktop the front door is
    // exactly one screen and nothing scrolls; phones put the form first and scroll.
    <div className="relative flex min-h-[100dvh] flex-col bg-[#f7f6f2] lg:h-[100dvh] lg:min-h-0 lg:overflow-hidden">
      <div aria-hidden="true" className="h-[3px] shrink-0 bg-brand" />
      <header className="flex shrink-0 items-center justify-between gap-4 border-b border-black/[0.1] px-4 py-3 sm:px-8 lg:px-12 short:py-2">
        <div className="flex items-center gap-3">
          {/* eslint-disable-next-line @next/next/no-img-element -- static export; fixed-size brand asset */}
          <img
            src={`${BASE}/brand/karnataka-emblem.png`}
            srcSet={`${BASE}/brand/karnataka-emblem.png 1x, ${BASE}/brand/karnataka-emblem@2x.png 2x`}
            alt={t("landing.emblemAlt", "Emblem of the Government of Karnataka")}
            width={62}
            height={56}
            className="h-10 w-auto short:h-9"
          />
          <div className="leading-tight">
            <p className="text-[11.5px] font-bold uppercase tracking-[0.18em] text-ink sm:text-[12.5px]">{t("landing.ksp", "Karnataka State Police")}</p>
            <p className="mt-0.5 text-[11.5px] text-muted">{t("landing.scrb", "State Crime Records Bureau")}</p>
          </div>
        </div>
        <div role="group" aria-label="Language" className="flex shrink-0 overflow-hidden rounded-md border border-black/[0.14] bg-white">
          {LANGUAGES.map((l) => (
            <button
              key={l.code}
              type="button"
              onClick={() => setLang(l.code)}
              aria-pressed={lang === l.code}
              aria-label={`Switch interface language to ${l.native}`}
              title={l.native}
              className={`h-8 min-w-10 px-2.5 text-[12px] font-semibold transition-colors ${lang === l.code ? "bg-ink text-white" : "text-muted hover:text-ink"}`}
            >
              {l.label}
            </button>
          ))}
        </div>
      </header>

      <div className="mx-auto grid w-full max-w-[1500px] flex-1 grid-cols-1 lg:min-h-0 lg:grid-cols-[minmax(0,1.25fr)_minmax(420px,0.75fr)]">
        {/* ------------------------------------------------------------ the register */}
        <div className="order-2 flex flex-col px-4 py-8 sm:px-8 lg:order-1 lg:min-h-0 lg:px-12 lg:py-10 short:py-6 tight:py-4">
          <div className="max-w-[640px]">
            <h1 className="text-display text-[40px] font-bold leading-none tracking-[-0.03em] text-ink sm:text-[52px] short:text-[44px] tight:text-[38px]">
              KSP IntelHub
            </h1>
            <p className="mt-3 max-w-[34rem] text-[15px] leading-relaxed text-muted short:mt-2 short:text-[14px]">
              {t(
                "landing.lede",
                "Case files, offender histories, cross-station series and camera hits for Karnataka's investigating officers — every record cited, every access logged.",
              )}
            </p>
          </div>

          <div className="mt-8 grid gap-x-12 gap-y-8 lg:min-h-0 lg:flex-1 xl:grid-cols-[minmax(0,1fr)_minmax(0,0.8fr)] short:mt-5 short:gap-y-5 tight:mt-4">
            <div className="max-w-[640px] space-y-7 short:space-y-4">
              <Register stats={stats} failed={statsFailed} readAt={readAt} t={t} />
              <Alerts alerts={stats?.trend_alerts} t={t} />
            </div>
            {wide && (
              <div className="hidden min-h-0 xl:block">
                <KarnatakaMap marked={marked} t={t} />
              </div>
            )}
          </div>
        </div>

        {/* ------------------------------------------------------------ sign-in */}
        <div className="no-scrollbar order-1 flex justify-center border-black/[0.1] bg-white px-4 py-8 sm:px-8 lg:order-2 lg:min-h-0 lg:items-center lg:overflow-y-auto lg:border-l lg:px-12 short:py-5 tight:py-3">
          <SignIn nextPath={nextPath} onSignedIn={go} core={stats ? "online" : statsFailed ? "offline" : "checking"} t={t} />
        </div>
      </div>

      <footer className="flex shrink-0 flex-col gap-1 border-t border-black/[0.1] px-4 py-2.5 text-[11px] leading-relaxed text-muted sm:flex-row sm:items-center sm:justify-between sm:px-8 lg:px-12">
        <span>
          {t(
            "landing.simulated",
            "Figures are computed live from a simulated, CCTNS-shaped corpus over Karnataka's real districts. No production KSP records are used.",
          )}
        </span>
        <span className="shrink-0">{t("landing.audited", "Every sign-in attempt, successful or not, is written to the audit log.")}</span>
      </footer>
    </div>
  );
}
