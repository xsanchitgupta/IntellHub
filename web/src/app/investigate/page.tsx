"use client";

/* Investigation workbench.
 *
 * Four views over the whole corpus, each computed by GET /investigate/* from the records
 * and each finding citing the records it rests on:
 *   Series        FIRs that look like one offender's work across police-station lines
 *   Integrity     records that contradict each other, statewide and by district
 *   Under-reporting  districts recording far less than their peers; Dial-112 calls that
 *                 never became an FIR
 *   Statutes      the IPC -> BNS cross-walk the FIR checks use, with a lookup
 */

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, BookOpenCheck, ClipboardCheck, Layers, Loader2, Radar, Search } from "lucide-react";
import { ApiError, getJSON } from "@/lib/api";
import AccessRequired from "@/components/AccessRequired";
import { Cites, GeoProfileView, SeverityTag } from "@/components/Investigation";

const TABS = [
  { id: "series", label: "Crime series", icon: Layers },
  { id: "integrity", label: "Record integrity", icon: ClipboardCheck },
  { id: "underreporting", label: "Under-reporting", icon: Radar },
  { id: "statutes", label: "IPC → BNS", icon: BookOpenCheck },
];

const RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };
const FEATURE: Record<string, string> = {
  literacy: "literacy", unemployment: "unemployment", per_capita_income_k: "income per head",
  urbanization: "urbanisation", police_per_100k: "police per lakh", youth_pct: "share of youth", slum_pct: "slum population",
};
const select = "rounded-xl border border-black/10 bg-white px-3 py-2 text-[13px] text-ink focus:border-brand/50 focus:outline-none";

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`bezel ${className}`}>
      <div className="bezel-core h-full p-5">{children}</div>
    </div>
  );
}

function useLoad<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "forbidden" | "error">("idle");
  useEffect(() => {
    if (!path) return;
    let live = true;
    setState("loading");
    getJSON<T>(path)
      .then((d) => { if (live) { setData(d); setState("idle"); } })
      .catch((e) => live && setState(e instanceof ApiError && e.isForbidden ? "forbidden" : "error"));
    return () => { live = false; };
  }, [path]);
  return { data, state };
}

function Status({ state, what }: { state: string; what: string }) {
  if (state === "loading") return <p className="flex items-center gap-2 py-10 text-[13px] text-faint"><Loader2 className="h-4 w-4 animate-spin" /> Computing from the records…</p>;
  if (state === "forbidden") return <AccessRequired what={what} />;
  if (state === "error") return <p className="py-10 text-[13px] text-crimson">The intelligence core did not answer. Try again in a moment.</p>;
  return null;
}

// ------------------------------------------------------------------------------- series
function SeriesTab({ initial }: { initial: string | null }) {
  const router = useRouter();
  const [kind, setKind] = useState("");
  const [district, setDistrict] = useState("");
  const [crime, setCrime] = useState("");
  const [limit, setLimit] = useState(30);
  const [picked, setPicked] = useState<string | null>(initial);
  const q = new URLSearchParams({ limit: String(limit), ...(kind && { kind }), ...(district && { district }), ...(crime && { crime_type: crime }) });
  const list = useLoad<any>(`/investigate/series?${q}`);
  const detail = useLoad<any>(picked ? `/investigate/series/${picked}` : null);
  const masters = useLoad<any>("/fir/masters");

  const pick = (id: string) => {
    setPicked(id);
    router.replace(`/investigate?series=${id}`);
  };

  if (list.state === "forbidden") return <AccessRequired what="Crime series" />;
  const d = list.data;
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
      <div>
        <div className="mb-3 flex flex-wrap gap-2">
          <select value={kind} onChange={(e) => setKind(e.target.value)} className={select}>
            <option value="">Both kinds</option>
            <option value="mo">Linked by MO</option>
            <option value="offender">Linked by a shared accused</option>
          </select>
          <select value={district} onChange={(e) => setDistrict(e.target.value)} className={select}>
            <option value="">All districts</option>
            {(masters.data?.districts || []).map((x: any) => <option key={x.id}>{x.name}</option>)}
          </select>
          <select value={crime} onChange={(e) => setCrime(e.target.value)} className={select}>
            <option value="">All crime types</option>
            {Array.from(new Set<string>((masters.data?.crime_types || []).map((x: any) => x.name))).sort().map((n) => <option key={n}>{n}</option>)}
          </select>
        </div>
        {d && (
          <p className="mb-3 text-[12px] text-faint">
            {d.total.toLocaleString()} candidate series ·{" "}
            {Object.entries(d.by_kind).map(([k, v]: any) => `${v.toLocaleString()} ${k === "mo" ? "by MO" : "by shared accused"}`).join(" · ")}.
            MO series: same crime type and at least {d.criteria.min_mo_match} of weapon, vehicle, target and time, within {d.criteria.window_days} days and {d.criteria.radius_km} km.
          </p>
        )}
        <Status state={list.state} what="Crime series" />
        <ul className="space-y-2">
          {(d?.series || []).map((s: any) => (
            <li key={s.id}>
              <button onClick={() => pick(s.id)}
                      className={`w-full rounded-xl border p-3 text-left transition-colors ${picked === s.id ? "border-brand/50 bg-brand/[0.06]" : "border-black/[0.07] bg-white hover:border-brand/30"}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[12.5px] font-bold text-ink">{s.id}</span>
                  <span className={`rounded px-1.5 py-px text-[10px] font-semibold ${s.kind === "mo" ? "bg-azure/12 text-azure" : "bg-brand/12 text-brand"}`}>
                    {s.kind === "mo" ? "same MO" : `same accused · ${s.person?.id}`}
                  </span>
                </div>
                <p className="mt-1 text-[13px] font-semibold text-ink">{s.size} {s.crime_type} FIRs across {s.stations} stations</p>
                <p className="text-[11.5px] text-muted">{s.districts.slice(0, 4).join(", ")}{s.districts.length > 4 ? ` +${s.districts.length - 4}` : ""} · {s.first} → {s.last} · {s.open} open</p>
              </button>
            </li>
          ))}
        </ul>
        {d && d.series.length < d.total && (
          <button onClick={() => setLimit((n) => n + 30)} className="mt-3 w-full rounded-xl border border-black/10 py-2 text-[12.5px] font-semibold text-ink hover:border-brand/40">
            Show more
          </button>
        )}
      </div>

      <div>
        {!picked && <Card><p className="py-10 text-center text-[13px] text-faint">Pick a series to see its FIRs, shared accused and geographic profile.</p></Card>}
        {picked && (
          <Card>
            <Status state={detail.state} what="This series" />
            {detail.data && detail.state === "idle" && (
              <div>
                <p className="font-mono text-[12px] text-faint">{detail.data.id}</p>
                <h2 className="text-[17px] font-bold text-ink">
                  {detail.data.size} {detail.data.crime_type} FIRs · {detail.data.stations} stations · {detail.data.districts.length} district(s)
                </h2>
                {detail.data.signature && (
                  <p className="mt-1 text-[12.5px] text-muted">
                    Shared MO: {Object.values(detail.data.signature).filter((v: any) => v && v !== "None").join(" · ")}
                  </p>
                )}
                {detail.data.person && (
                  <p className="mt-1 text-[12.5px] text-muted">
                    Named on every FIR: <Link prefetch={false} href={`/suspect?id=${detail.data.person.id}`} className="font-semibold text-brand">{detail.data.person.id}</Link>
                  </p>
                )}
                {detail.data.shared_accused?.length > 0 && (
                  <div className="mt-3">
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-faint">Named on two or more of these FIRs</p>
                    <div className="mt-1"><Cites cites={detail.data.shared_accused.map((p: any) => ({ kind: "person", id: p.id, label: `${p.id} · ${p.firs} FIRs` }))} max={8} /></div>
                  </div>
                )}
                <div className="mt-4 max-h-64 overflow-y-auto rounded-xl border border-black/[0.06]">
                  <table className="w-full text-[12px]">
                    <thead className="sticky top-0 bg-[#fafafa] text-[10.5px] uppercase tracking-wider text-faint">
                      <tr><th className="px-3 py-1.5 text-left">FIR</th><th className="px-3 py-1.5 text-left">Date</th><th className="px-3 py-1.5 text-left">Station</th><th className="px-3 py-1.5 text-left">Status</th></tr>
                    </thead>
                    <tbody>
                      {detail.data.members.map((m: any) => (
                        <tr key={m.case_id} className="border-t border-black/[0.05]">
                          <td className="px-3 py-1.5"><Link prefetch={false} href={`/case?id=${m.case_id}`} className="font-mono text-brand hover:underline">{m.case_id}</Link></td>
                          <td className="px-3 py-1.5 font-mono text-muted">{m.date}</td>
                          <td className="px-3 py-1.5 text-ink">{m.station}</td>
                          <td className="px-3 py-1.5 text-muted">{m.status}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <h3 className="mb-2 mt-5 text-[12px] font-semibold uppercase tracking-wider text-faint">Geographic profile</h3>
                <GeoProfileView g={detail.data.geoprofile} />
                <p className="mt-3 text-[11.5px] text-faint">{detail.data.note}</p>
              </div>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------- integrity
function IntegrityTab() {
  const [district, setDistrict] = useState("");
  const [code, setCode] = useState("");
  const q = new URLSearchParams({ limit: "60", ...(district && { district }), ...(code && { code }) });
  const { data, state } = useLoad<any>(`/investigate/integrity?${q}`);
  const masters = useLoad<any>("/fir/masters");
  if (state === "forbidden") return <AccessRequired what="The record-integrity audit" />;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <select value={district} onChange={(e) => setDistrict(e.target.value)} className={select}>
          <option value="">All districts</option>
          {(masters.data?.districts || []).map((x: any) => <option key={x.id}>{x.name}</option>)}
        </select>
        {code && <button onClick={() => setCode("")} className="rounded-xl border border-black/10 px-3 py-2 text-[12.5px] text-muted hover:text-ink">Clear check filter ×</button>}
        {data && <span className="text-[12.5px] text-faint">{data.firs_flagged.toLocaleString()} of {data.firs_checked.toLocaleString()} FIRs carry at least one issue · as of {data.as_of}</span>}
      </div>
      <Status state={state} what="The record-integrity audit" />
      {data && (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card>
            <h3 className="mb-3 text-[13px] font-bold text-ink">Checks</h3>
            <ul className="divide-y divide-black/[0.05]">
              {data.checks.filter((c: any) => c.count > 0 || code === c.code).sort((a: any, b: any) => (RANK[a.severity] ?? 3) - (RANK[b.severity] ?? 3) || b.count - a.count).map((c: any) => (
                <li key={c.code}>
                  <button onClick={() => setCode(code === c.code ? "" : c.code)} className={`flex w-full items-center gap-3 py-2 text-left ${code === c.code ? "text-brand" : ""}`}>
                    <SeverityTag s={c.severity} />
                    <span className="flex-1 text-[12.5px] text-ink">{c.title}</span>
                    <span className="font-mono text-[12.5px] font-semibold text-ink">{c.count.toLocaleString()}</span>
                  </button>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-[11px] text-faint">{data.checks.filter((c: any) => c.count === 0).length} further checks found nothing.</p>
          </Card>
          <Card>
            <h3 className="mb-3 text-[13px] font-bold text-ink">By district</h3>
            <div className="max-h-80 overflow-y-auto">
              <table className="w-full text-[12.5px]">
                <tbody>
                  {data.by_district.map((r: any) => (
                    <tr key={r.district} className="border-t border-black/[0.05]">
                      <td className="py-1.5"><button onClick={() => setDistrict(r.district)} className="text-left text-ink hover:text-brand">{r.district}</button></td>
                      <td className="py-1.5 text-right font-mono font-semibold">{r.total.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Card className="lg:col-span-2">
            <h3 className="mb-3 text-[13px] font-bold text-ink">FIRs with the most serious issues</h3>
            <ul className="divide-y divide-black/[0.05]">
              {data.cases.map((c: any) => (
                <li key={c.case_id} className="flex flex-wrap items-center gap-3 py-2">
                  <Link prefetch={false} href={`/case?id=${c.case_id}`} className="font-mono text-[12.5px] font-semibold text-brand hover:underline">{c.case_id}</Link>
                  <span className="text-[12px] text-muted">{c.crime_type} · {c.station}</span>
                  <span className="flex flex-wrap gap-1">
                    {c.flags.slice(0, 4).map((f: any, i: number) => (
                      <span key={i} className="rounded bg-black/[0.04] px-1.5 py-px text-[10.5px] text-ink">{f.title}</span>
                    ))}
                  </span>
                  <ArrowRight className="ml-auto h-4 w-4 text-faint" />
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------ under-reporting
function UnderreportingTab() {
  const { data, state } = useLoad<any>("/investigate/underreporting");
  return (
    <div>
      <Status state={state} what="Under-reporting analysis" />
      {data && (
        <Card>
          <p className="mb-3 text-[12.5px] leading-relaxed text-muted">
            {data.model?.method} ({data.model?.features.map((f: string) => FEATURE[f] ?? f).join(", ")}), over {data.model?.years} years. Dial-112 calls reporting{" "}
            {data.cognizable_call_types.join(", ").toLowerCase()} — offences an FIR is due for — became an FIR{" "}
            {data.state_cognizable_fir_rate != null ? `${Math.round(data.state_cognizable_fir_rate * 100)}%` : "—"} of the time statewide.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-[12.5px]">
              <thead className="text-[10.5px] uppercase tracking-wider text-faint">
                <tr>
                  <th className="py-2 text-left">District</th>
                  <th className="py-2 text-right">FIRs / lakh / yr</th>
                  <th className="py-2 text-right">Peers predict</th>
                  <th className="py-2 text-left pl-4">Gap (SD)</th>
                  <th className="py-2 text-right">Calls → FIR</th>
                  <th className="py-2 text-left pl-4">Signal</th>
                </tr>
              </thead>
              <tbody>
                {data.districts.map((d: any) => (
                  <tr key={d.district} className={`border-t border-black/[0.05] ${d.flags.length ? "bg-brand/[0.035]" : ""}`}>
                    <td className="py-2 font-semibold text-ink">{d.district}</td>
                    <td className="py-2 text-right font-mono">{d.rate_per_lakh_year ?? "—"}</td>
                    <td className="py-2 text-right font-mono text-muted">{d.expected ?? "—"}</td>
                    <td className="py-2 pl-4">
                      {d.z != null && (
                        <span className="flex items-center gap-2">
                          <span className="relative h-1.5 w-24 rounded-full bg-black/[0.06]">
                            <span className={`absolute top-0 h-1.5 rounded-full ${d.z < 0 ? "bg-crimson/70" : "bg-emerald/70"}`}
                                  style={{ left: d.z < 0 ? `${50 - Math.min(50, Math.abs(d.z) * 16)}%` : "50%", width: `${Math.min(50, Math.abs(d.z) * 16)}%` }} />
                          </span>
                          <span className="font-mono text-[11.5px] text-muted">{d.z > 0 ? "+" : ""}{d.z}</span>
                        </span>
                      )}
                    </td>
                    <td className="py-2 text-right font-mono text-muted">{d.calls ? `${d.calls.with_fir}/${d.calls.calls}` : "—"}</td>
                    <td className="py-2 pl-4 text-[12px] text-ink">{d.flags.join(" ") || <span className="text-faint">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[11.5px] text-faint">{data.note}</p>
        </Card>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------ statutes
function StatutesTab() {
  const { data } = useLoad<any>("/statutes/crosswalk");
  const [act, setAct] = useState("IPC");
  const [section, setSection] = useState("");
  const [date, setDate] = useState("");
  const [res, setRes] = useState<any>(null);
  const look = useCallback(() => {
    if (!section.trim()) return;
    const q = new URLSearchParams({ act, section: section.trim(), ...(date && { date }) });
    getJSON(`/statutes/crosswalk?${q}`).then(setRes).catch(() => setRes({ row: null }));
  }, [act, section, date]);
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.8fr)]">
      <Card>
        <h3 className="text-[13px] font-bold text-ink">Cross-walk used by every FIR check</h3>
        {data && (
          <>
            <p className="mt-1 text-[12px] text-faint">
              The BNS replaced the IPC on {data.commencement}; the date of the offence decides which applies. {data.unaffected_acts.join(", ")} are unchanged.
            </p>
            <table className="mt-3 w-full text-[12.5px]">
              <thead className="text-[10.5px] uppercase tracking-wider text-faint">
                <tr><th className="w-14 py-1.5 pr-4 text-left">IPC</th><th className="w-20 py-1.5 pr-4 text-left">BNS</th><th className="py-1.5 text-left">Subject · change</th></tr>
              </thead>
              <tbody>
                {data.rows.map((r: any) => (
                  <tr key={r.bns} className="border-t border-black/[0.05] align-top">
                    <td className="whitespace-nowrap py-2 pr-4 font-mono font-semibold text-ink">{r.ipc ?? "—"}</td>
                    <td className="whitespace-nowrap py-2 pr-4 font-mono font-semibold text-brand">{r.bns}</td>
                    <td className="py-2 text-muted"><span className="text-ink">{r.subject}.</span> {r.change}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-[11px] text-faint">Source: {data.source}.</p>
          </>
        )}
      </Card>
      <Card>
        <h3 className="text-[13px] font-bold text-ink">Look up a section</h3>
        <form onSubmit={(e) => { e.preventDefault(); look(); }} className="mt-3 space-y-2">
          <div className="flex gap-2">
            <select value={act} onChange={(e) => setAct(e.target.value)} className={select}><option>IPC</option><option>BNS</option></select>
            <input value={section} onChange={(e) => setSection(e.target.value)} placeholder="e.g. 457 or 331(4)" className={`${select} min-w-0 flex-1`} />
          </div>
          <label className="block text-[11.5px] text-faint">
            Date of the offence (optional)
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={`${select} mt-1 w-full`} />
          </label>
          <button className="inline-flex items-center gap-1.5 rounded-xl bg-ink px-4 py-2 text-[13px] font-semibold text-white"><Search className="h-4 w-4" /> Look up</button>
        </form>
        {res && (
          <div className="mt-4 rounded-xl border border-black/[0.06] bg-black/[0.02] p-3 text-[12.5px]">
            {res.row ? (
              <>
                <p className="font-semibold text-ink">
                  {act} {section} ⇄ {act === "IPC" ? `BNS ${res.row.bns}` : res.row.ipc ? `IPC ${res.row.ipc}` : "no IPC predecessor"}
                </p>
                <p className="mt-1 text-muted">{res.row.subject}. {res.row.change}</p>
              </>
            ) : <p className="text-muted">That section is not in the cross-walk.</p>}
            {res.code_in_force && <p className="mt-2 text-ink">Code in force on that date: <b>{res.code_in_force}</b></p>}
            {res.findings?.map((f: any, i: number) => <p key={i} className="mt-2 text-brand">{f.message}</p>)}
          </div>
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------------- page
function Inner() {
  const params = useSearchParams();
  const router = useRouter();
  const initialSeries = params.get("series");
  const [tab, setTab] = useState(params.get("tab") || "series");
  const go = (t: string) => {
    setTab(t);
    router.replace(`/investigate?tab=${t}`);
  };
  const active = useMemo(() => TABS.find((t) => t.id === tab) ?? TABS[0], [tab]);
  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-8">
      <header>
        <h1 className="text-display text-3xl font-bold tracking-tight text-ink md:text-4xl">Investigation workbench</h1>
        <p className="mt-1 max-w-3xl text-[14px] text-muted">
          Cross-station series, records that contradict each other, districts that record less than they should, and the
          statute each FIR belongs under — computed from the records, every finding citing the record it rests on.
        </p>
      </header>
      <div className="flex flex-wrap gap-1 rounded-2xl border border-black/[0.07] bg-white p-1 shadow-sm">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => go(t.id)}
                  className={`flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-[13px] font-semibold transition-colors ${active.id === t.id ? "bg-ink text-white" : "text-muted hover:text-ink"}`}>
            <t.icon className="h-4 w-4" /> {t.label}
          </button>
        ))}
      </div>
      {active.id === "series" && <SeriesTab initial={initialSeries} />}
      {active.id === "integrity" && <IntegrityTab />}
      {active.id === "underreporting" && <UnderreportingTab />}
      {active.id === "statutes" && <StatutesTab />}
    </div>
  );
}

export default function InvestigatePage() {
  return (
    <Suspense fallback={null}>
      <Inner />
    </Suspense>
  );
}
