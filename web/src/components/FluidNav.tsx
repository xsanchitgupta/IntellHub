"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import AlertCenter from "./AlertCenter";
import { useI18n, LANGUAGES } from "@/lib/i18n";
import { demoLogin, logout, ApiError, type DemoPersona, type ScopeLevel } from "@/lib/api";
import { refreshAlerts } from "@/lib/alerts";
import {
  initials,
  isCurrentPersona,
  jurisdictionLabel,
  roleLabel,
  scopeLevel,
  useDemoInfo,
  useIdentity,
} from "@/lib/session";
import { AnimatePresence, motion } from "framer-motion";
import {
  LayoutDashboard,
  Folders,
  Map as MapIcon,
  Network,
  Siren,
  Boxes,
  ShieldAlert,
  Brain,
  Users,
  Timer,
  GitBranch,
  UserCog,
  Cctv,
  Sparkles,
  ChevronDown,
  Newspaper,
  LogIn,
  LogOut,
  Landmark,
  Building2,
  MapPin,
  Check,
  LoaderCircle,
  ArrowRight,
  ScanSearch,
  type LucideIcon,
} from "lucide-react";

type T = (k: string, d: string) => string;

// `key` drives translation; `label` is the English source and the fallback.
const NAV: { href: string; label: string; key: string; icon: LucideIcon }[] = [
  { href: "/overview", label: "Overview", key: "nav.overview", icon: LayoutDashboard },
  { href: "/cases", label: "Cases", key: "nav.cases", icon: Folders },
  { href: "/investigate", label: "Investigate", key: "nav.investigate", icon: ScanSearch },
  { href: "/map", label: "Map", key: "nav.map", icon: MapIcon },
  { href: "/surveillance", label: "CCTV", key: "nav.cctv", icon: Cctv },
  { href: "/news", label: "Live News", key: "nav.news", icon: Newspaper },
  { href: "/network", label: "Network", key: "nav.network", icon: Network },
  { href: "/analytics", label: "Analytics", key: "nav.analytics", icon: Brain },
  { href: "/copilot", label: "Copilot", key: "nav.copilot", icon: Sparkles },
];

// Less-frequent destinations, kept one click away so the pill fits a laptop screen.
const MORE: { href: string; label: string; key: string; icon: LucideIcon }[] = [
  { href: "/intel", label: "Registry", key: "nav.registry", icon: Boxes },
  { href: "/officers", label: "Officers", key: "nav.officers", icon: Users },
  { href: "/lifecycle", label: "Lifecycle", key: "nav.lifecycle", icon: GitBranch },
  { href: "/pendency", label: "Pendency", key: "nav.pendency", icon: Timer },
  { href: "/dispatch", label: "Dispatch", key: "nav.dispatch", icon: Siren },
  { href: "/access", label: "Account & roles", key: "nav.access", icon: UserCog },
];

const SCOPE_ICON: Record<ScopeLevel, LucideIcon> = { state: Landmark, district: Building2, station: MapPin };

/**
 * Where the text labels appear. Nine destinations, the crest, the bell, the
 * profile and the language switch do not fit a 1280px laptop with labels: the
 * centred pill overflowed both edges and the crest and first links were clipped.
 * So below xl the pill is icon-only (every link keeps an accessible name and a
 * tooltip). Kannada labels run roughly half again as wide, so they wait for 2xl.
 */
function labelClasses(lang: string) {
  return lang === "kn"
    ? { show: "hidden 2xl:inline", sr: "sr-only 2xl:hidden", mark: "hidden lg:flex" }
    : { show: "hidden xl:inline", sr: "sr-only xl:hidden", mark: "hidden lg:flex xl:hidden 2xl:flex" };
}

/** Close on a click outside `ref` or on Escape; Escape hands focus back to the
 *  control that opened the panel, so a keyboard user is not dropped at the top. */
function useDismiss(
  open: boolean,
  close: () => void,
  ref: React.RefObject<HTMLElement | null>,
  returnFocus?: React.RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      close();
      returnFocus?.current?.focus();
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close, ref, returnFocus]);
}

function MoreMenu({ isActive, t, lang }: { isActive: (h: string) => boolean; t: T; lang: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLLIElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  const current = MORE.find((m) => isActive(m.href));
  const menuId = useId();
  useDismiss(open, close, ref, buttonRef);
  const Icon = current?.icon || ChevronDown;
  const cls = labelClasses(lang);
  const name = current ? t(current.key, current.label) : t("nav.more", "More");
  return (
    <li ref={ref} className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={menuId}
        title={name}
        className={`group relative flex min-h-[44px] items-center justify-center gap-1.5 rounded-full px-3 py-2 text-[13px] font-medium transition-colors md:min-h-0 xl:px-3.5 ${current ? "text-black" : "text-muted hover:text-ink"}`}
      >
        {current && (
          <motion.span layoutId="nav-pill" transition={{ type: "spring", stiffness: 480, damping: 30 }}
                       className="absolute inset-0 -z-10 rounded-full bg-gradient-to-b from-brand-soft to-brand shadow-[0_6px_20px_-5px_rgba(176,125,22,0.7)]" />
        )}
        {current && <Icon className="h-[15px] w-[15px]" aria-hidden="true" />}
        <span className={cls.show}>{name}</span>
        <span className={cls.sr}>{name}</span>
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden="true" />
      </button>
      <AnimatePresence>
        {open && (
          <motion.ul
            id={menuId}
            initial={{ opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.97 }}
            transition={{ duration: 0.16 }}
            className="fixed left-4 top-[84px] z-50 min-w-[200px] rounded-2xl border border-black/10 bg-white p-1.5 shadow-[0_24px_60px_-20px_rgba(17,20,32,0.3)] md:absolute md:left-auto md:right-0 md:top-12"
          >
            {MORE.map(({ href, label, key, icon: I }) => (
              <li key={href}>
                <Link prefetch={false} href={href} onClick={() => setOpen(false)}
                      aria-current={isActive(href) ? "page" : undefined}
                      className={`flex items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] font-medium ${isActive(href) ? "bg-brand/10 text-brand-deep" : "text-ink hover:bg-black/[0.04]"}`}>
                  <I className="h-4 w-4" aria-hidden="true" /> {t(key, label)}
                </Link>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </li>
  );
}

/** The tier is the coarse privilege level; the tint tells roles apart at a glance. */
function tierTone(tier: number | undefined, role: string) {
  const tr = tier ?? ({ analyst: 0, investigator: 1, supervisor: 2 } as Record<string, number>)[role] ?? 0;
  return tr >= 3
    ? "from-[#2a2a31] to-[#0f0f12] text-white"
    : tr === 2
      ? "from-[#f0605a] to-crimson text-white"
      : tr === 1
        ? "from-brand-soft to-brand-deep text-black/85"
        : "from-[#6aa2e0] to-azure text-white";
}

function personaScope(p: DemoPersona, district: string | null | undefined, t: T) {
  if (p.scope_level === "state") return t("profile.allDistricts", "All 31 districts");
  if (p.scope_level === "station") return [p.station, p.district].filter(Boolean).join(", ");
  return district || p.district || "";
}

let alertsEpoch = 0;

function ProfileMenu({ t }: { t: T }) {
  const { me, identity, authenticated, loading, epoch } = useIdentity();
  const { info: demo } = useDemoInfo();
  const pathname = usePathname() || "/";
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => {
    setOpen(false);
    setExpanded(null);
  }, []);
  const panelId = useId();
  useDismiss(open, close, wrapRef, buttonRef);

  // The alert feed is fetched with whatever token was current when it last
  // polled. After a sign-in, sign-out or switch it must start over, or the bell
  // keeps the previous role's alerts — or stays hidden for a role that may now
  // see them. `epoch` counts token changes in this tab; the last one acted on is
  // kept at module level because the nav unmounts on the sign-in page, which is
  // exactly where the change usually happens.
  useEffect(() => {
    if (epoch !== alertsEpoch) {
      alertsEpoch = epoch;
      refreshAlerts();
    }
  }, [epoch]);

  if (loading && !me) {
    return <span aria-hidden="true" className="shimmer ml-1 h-9 w-9 shrink-0 rounded-full" />;
  }

  if (!authenticated || !identity) {
    const next = pathname && pathname !== "/" ? `/?next=${encodeURIComponent(pathname)}` : "/";
    return (
      <Link
        prefetch={false}
        href={next}
        className="ml-1 flex h-11 shrink-0 items-center gap-1.5 rounded-full border border-brand/30 bg-brand/[0.07] px-3 text-[12.5px] font-semibold text-brand-deep transition-colors hover:border-brand/50 hover:bg-brand/[0.12] md:h-9"
      >
        <LogIn className="h-4 w-4" aria-hidden="true" />
        <span className="hidden sm:inline">{t("common.signIn", "Sign in")}</span>
        <span className="sr-only sm:hidden">{t("common.signIn", "Sign in")}</span>
      </Link>
    );
  }

  const label = roleLabel(identity);
  const where = jurisdictionLabel(identity, t);
  const ScopeIcon = SCOPE_ICON[scopeLevel(identity)];

  const switchTo = async (p: DemoPersona, district?: string) => {
    if (busy) return;
    setBusy(p.id);
    setFailure(null);
    try {
      await demoLogin(p.id, p.switchable_district ? district : undefined);
      // Every page here loads its data in effects on mount, using the token held
      // in storage. router.refresh() only re-renders server components, and a
      // static export has none carrying per-user data, so it cannot re-run those
      // effects; a full reload is the one way every panel re-fetches as the new
      // role. Navigation state survives because the URL does.
      router.refresh();
      window.location.reload();
    } catch (e) {
      setFailure(
        e instanceof ApiError
          ? `${t("profile.switchFailed", "Could not switch role")} (HTTP ${e.status}).`
          : t("landing.err.network", "Could not reach the intelligence core. Check that the backend is running, then retry."),
      );
      setBusy(null);
    }
  };

  const signOut = () => {
    logout();
    close();
    router.push("/");
  };

  return (
    <div ref={wrapRef} className="relative ml-1 shrink-0">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`${t("profile.menu", "Account menu")}: ${label}, ${where}`}
        title={`${label} · ${where}`}
        className="grid h-11 w-11 place-items-center rounded-full md:h-9 md:w-9"
      >
        <span
          className={`grid h-9 w-9 place-items-center rounded-full bg-gradient-to-br text-[11.5px] font-bold tracking-wide shadow-[0_4px_14px_-4px_rgba(17,20,32,0.35)] ring-2 ring-white transition-transform duration-300 hover:scale-105 ${tierTone(identity.tier, identity.role)}`}
        >
          {initials(identity)}
        </span>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            id={panelId}
            initial={{ opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.97 }}
            transition={{ duration: 0.16 }}
            className="fixed left-4 right-4 top-[84px] z-50 max-h-[calc(100dvh-100px)] overflow-y-auto rounded-2xl border border-black/10 bg-white p-1.5 shadow-[0_30px_80px_-24px_rgba(17,20,32,0.35)] md:absolute md:left-auto md:right-0 md:top-12 md:w-[330px]"
          >
            <div className="rounded-xl bg-black/[0.025] px-3.5 py-3">
              <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-brand">
                {me?.demo_mode ? t("profile.demoAccess", "Demo access") : t("profile.signedIn", "Signed in")}
              </p>
              <p className="mt-1.5 text-[15px] font-bold leading-tight text-ink">{label}</p>
              {identity.user && <p className="mt-0.5 truncate font-mono text-[11.5px] text-muted">{identity.user}</p>}
              <p className="mt-2 flex items-center gap-1.5 text-[12px] font-medium text-ink/80">
                <ScopeIcon className="h-3.5 w-3.5 text-brand" aria-hidden="true" /> {where}
              </p>
            </div>

            {demo && demo.personas.length > 0 && (
              <div className="mt-1.5">
                <p className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-faint">
                  {t("profile.switchRole", "Switch role (demo)")}
                </p>
                <ul>
                  {demo.personas.map((p) => {
                    const Icon = SCOPE_ICON[p.scope_level] ?? Landmark;
                    const current = isCurrentPersona(identity, p);
                    const canPick = p.switchable_district && demo.districts.length > 0;
                    const district = picked[p.id] ?? (current ? identity.scope?.district : null) ?? p.district ?? demo.districts[0];
                    const working = busy === p.id;
                    const isOpen = expanded === p.id;
                    return (
                      <li key={p.id}>
                        <button
                          type="button"
                          disabled={!!busy || (current && !canPick)}
                          aria-current={current ? "true" : undefined}
                          aria-expanded={canPick ? isOpen : undefined}
                          onClick={() => (canPick ? setExpanded(isOpen ? null : p.id) : switchTo(p))}
                          className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left transition-colors disabled:cursor-default ${current ? "bg-brand/[0.08]" : "hover:bg-black/[0.04]"}`}
                        >
                          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-black/[0.04] text-ink/70">
                            {working ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Icon className="h-3.5 w-3.5" aria-hidden="true" />}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[12.5px] font-semibold text-ink">{p.label}</span>
                            <span className="block truncate text-[11px] text-muted">{personaScope(p, district, t)}</span>
                          </span>
                          {current && <Check className="h-4 w-4 shrink-0 text-brand" aria-label={t("profile.current", "Current")} />}
                          {canPick && !current && (
                            <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-faint transition-transform ${isOpen ? "rotate-180" : ""}`} aria-hidden="true" />
                          )}
                        </button>
                        {canPick && isOpen && (
                          <div className="flex items-center gap-2 px-3 pb-2 pt-1">
                            <label className="sr-only" htmlFor={`${panelId}-${p.id}`}>
                              {t("profile.chooseDistrict", "District")}
                            </label>
                            <select
                              id={`${panelId}-${p.id}`}
                              value={district ?? ""}
                              disabled={!!busy}
                              onChange={(e) => setPicked((m) => ({ ...m, [p.id]: e.target.value }))}
                              className="h-9 min-w-0 flex-1 rounded-lg border border-black/10 bg-white px-2 text-[12.5px] text-ink outline-none focus:border-brand/50"
                            >
                              {demo.districts.map((d) => (
                                <option key={d} value={d}>{d}</option>
                              ))}
                            </select>
                            <button
                              type="button"
                              disabled={!!busy || (current && district === identity.scope?.district)}
                              onClick={() => switchTo(p, district ?? undefined)}
                              className="flex h-9 shrink-0 items-center gap-1 rounded-lg bg-ink px-3 text-[12px] font-semibold text-white transition-colors hover:bg-brand disabled:opacity-40"
                            >
                              {working ? t("profile.switching", "Switching…") : t("profile.switch", "Switch")}
                              <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                            </button>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
                {failure && <p role="alert" className="px-3 pb-1 pt-1.5 text-[11.5px] text-crimson">{failure}</p>}
              </div>
            )}

            <div className="mt-1.5 border-t border-black/[0.06] pt-1.5">
              <Link
                prefetch={false}
                href="/access"
                onClick={close}
                className="flex items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] font-medium text-ink hover:bg-black/[0.04]"
              >
                <UserCog className="h-4 w-4 text-muted" aria-hidden="true" /> {t("nav.access", "Account & roles")}
              </Link>
              <button
                type="button"
                onClick={signOut}
                className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-[13px] font-medium text-ink hover:bg-crimson/[0.06] hover:text-crimson"
              >
                <LogOut className="h-4 w-4" aria-hidden="true" /> {t("common.signOut", "Sign out")}
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

const spring = { type: "spring" as const, stiffness: 420, damping: 26 };

export default function FluidNav() {
  const { lang, setLang, t } = useI18n();
  const pathname = usePathname() || "/";
  const isActive = (href: string) => pathname === href || pathname.startsWith(href + "/");
  const cls = labelClasses(lang);

  return (
    // The pill is wider than a phone: the destinations plus the crest. Centring it
    // made it overflow BOTH edges, which pushed the crest and the first two links off
    // the left of the screen with no way to reach them — verified at 375px, where the
    // nav's right edge sat at 562px.
    //
    // So on narrow screens it aligns left and scrolls inside its own container,
    // keeping the crest visible and every destination reachable; from md up, where it
    // fits, it centres as before. `overscroll-x-contain` stops a swipe along the nav
    // from turning into a page-level back gesture.
    <header
      id="fluid-nav"
      className="fixed inset-x-0 top-0 z-40 flex justify-start overflow-x-auto overflow-y-hidden overscroll-x-contain px-4 pb-3 pt-5 no-scrollbar md:justify-center md:overflow-visible"
    >
      <motion.nav
        aria-label={t("a11y.primaryNav", "Primary")}
        initial={{ y: -28, opacity: 0, scale: 0.96 }}
        animate={{ y: 0, opacity: 1, scale: 1 }}
        transition={{ type: "spring", stiffness: 260, damping: 22, delay: 0.05 }}
        className="relative isolate flex shrink-0 items-center gap-1 rounded-full border border-black/[0.07] p-1.5 pl-2.5 pr-1.5 shadow-[0_18px_50px_-15px_rgba(17,20,32,0.16)]"
      >
        {/* The frosted surface is a layer behind the pill, not the pill itself.
            `backdrop-filter` on the nav made it the containing block for every
            `position: fixed` descendant, so the phone-width dropdowns (More, the
            alert bell, the profile) were positioned against the pill and clipped by
            the header's scroll container instead of sitting under it on screen. */}
        <span aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 rounded-full bg-white/85 backdrop-blur-xl" />

        {/* Brand crest */}
        <Link prefetch={false} href="/overview" aria-label="KSP IntelHub — Overview" className="group flex items-center gap-2.5 pr-2.5">
          <motion.span
            whileHover={{ scale: 1.1, rotate: -6 }}
            whileTap={{ scale: 0.92 }}
            transition={spring}
            className="relative grid h-9 w-9 place-items-center rounded-full bg-gradient-to-br from-brand-soft to-brand-deep shadow-[0_4px_16px_-3px_rgba(176,125,22,0.6)]"
          >
            <ShieldAlert className="h-[18px] w-[18px] text-black/85" strokeWidth={2.2} aria-hidden="true" />
            <span className="pointer-events-none absolute inset-0 rounded-full ring-1 ring-inset ring-white/30" />
          </motion.span>
          <span className={`${cls.mark} flex-col leading-none`}>
            <span className="text-display text-[13px] font-bold tracking-wide text-ink">
              KSP IntelHub
            </span>
            <span className="text-[9px] font-medium uppercase tracking-[0.22em] text-brand/90">
              SCRB · Karnataka
            </span>
          </span>
        </Link>

        <div className="mx-1 h-6 w-px bg-black/10" />

        <ul className="flex items-center gap-0.5">
          {NAV.map(({ href, label, key, icon: Icon }, i) => {
            const active = isActive(href);
            const name = t(key, label);
            return (
              <motion.li
                key={href}
                className="relative"
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ ...spring, delay: 0.12 + i * 0.035 }}
              >
                <motion.div whileHover={{ scale: 1.06, y: -1 }} whileTap={{ scale: 0.94 }} transition={spring}>
                  <Link prefetch={false}
                    href={href}
                    aria-current={active ? "page" : undefined}
                    title={name}
                    // 43x31 on a phone: comfortably past WCAG AA's 24x24 floor, but
                    // under the 44x44 target size the AAA guideline asks for, and
                    // these are icon-only at that width so there is no text to aim
                    // at either. Raised on touch widths only; the desktop pill keeps
                    // its proportions.
                    className={`group relative flex min-h-[44px] items-center justify-center gap-2 rounded-full px-3 py-2 text-[13px] font-medium transition-colors duration-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand md:min-h-0 xl:px-3 2xl:px-3.5 ${
                      active ? "text-black" : "text-muted hover:text-ink"
                    }`}
                  >
                    {active && (
                      <motion.span
                        layoutId="nav-pill"
                        transition={{ type: "spring", stiffness: 480, damping: 30 }}
                        className="absolute inset-0 -z-10 rounded-full bg-gradient-to-b from-brand-soft to-brand shadow-[0_6px_20px_-5px_rgba(176,125,22,0.7)]"
                      />
                    )}
                    <motion.span
                      className="grid place-items-center"
                      whileHover={{ rotate: active ? 0 : 8 }}
                      transition={spring}
                    >
                      <Icon className="h-[15px] w-[15px]" strokeWidth={2} aria-hidden="true" />
                    </motion.span>
                    {/* The visible label is hidden on narrower screens, so carry an
                        accessible name that survives at every width. */}
                    <span className={cls.show}>{name}</span>
                    <span className={cls.sr}>{name}</span>
                  </Link>
                </motion.div>
              </motion.li>
            );
          })}
          <MoreMenu isActive={isActive} t={t} lang={lang} />
        </ul>

        <AlertCenter />

        <ProfileMenu t={t} />

        {/* Language switch. Karnataka Police work in Kannada; an English-only tool
            excludes the people expected to live in it. */}
        <div
          role="group"
          aria-label="Language"
          className="ml-1 flex items-center gap-0.5 rounded-full border border-black/[0.07] bg-black/[0.02] p-0.5"
        >
          {LANGUAGES.map((l) => (
            <button
              key={l.code}
              type="button"
              onClick={() => setLang(l.code)}
              aria-pressed={lang === l.code}
              aria-label={`Switch interface language to ${l.native}`}
              title={l.native}
              // 28px square on a phone, which is a real control at roughly the size
              // of a fingertip's error margin. Given a 44px target on touch widths
              // like the nav links; the desktop pill keeps the compact form.
              className={`h-11 min-w-11 rounded-full px-2 text-[11.5px] font-semibold transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand md:h-7 md:min-w-7 ${
                lang === l.code
                  ? "bg-brand text-black"
                  : "text-muted hover:text-ink"
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>
      </motion.nav>
    </header>
  );
}
