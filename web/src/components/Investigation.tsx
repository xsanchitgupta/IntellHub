"use client";

/* Investigation workbench pieces, shared by the case file, the person file and /investigate.
 *
 * Every finding the backend returns carries the record identifiers it rests on; these
 * components render them as links wherever the record has a page, so an officer can
 * always open the evidence behind a flag, a question or a hypothesis cell. Nothing
 * here computes a finding — it only draws what the records said. */

import Link from "next/link";
import { AlertTriangle, CheckCircle2, CircleDashed, HelpCircle, XCircle } from "lucide-react";

export type Cite = { kind: string; id: string; label?: string };
export type Flag = { code: string; severity: "high" | "medium" | "low"; title: string; detail: string; cites: Cite[]; question?: string };
export type Question = { question: string; why: string; cites: Cite[]; source: string };
export type Cell = { verdict: "C" | "I" | "N"; note: string; cites: Cite[] };
export type Hypotheses = {
  method: string;
  excluded_factors: string[];
  evidence: { key: string; label: string; diagnostic: boolean }[];
  hypotheses: { id: string; name?: string | null; named: boolean; inconsistent: number; consistent: number; cells: Record<string, Cell> }[];
  note: string;
};
export type GeoProfile = {
  applicable: boolean;
  reason?: string;
  method?: string;
  buffer_km?: number;
  cluster_km?: number;
  peak?: { lat: number; lng: number } | null;
  anchors?: { lat: number; lng: number; share: number }[];
  sites?: { lat: number; lng: number; case_id?: string }[];
  sites_used?: string[];
  excluded?: string[];
  search_area_km2?: { "50": number; "80": number; profiled: number };
  heat?: [number, number, number][];
  note?: string;
};

const SEV: Record<string, string> = {
  high: "bg-crimson/10 text-crimson border-crimson/25",
  medium: "bg-brand/10 text-brand border-brand/25",
  low: "bg-black/[0.04] text-muted border-black/10",
};

export function SeverityTag({ s }: { s: string }) {
  return <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${SEV[s] || SEV.low}`}>{s}</span>;
}

export function Cites({ cites, max = 4 }: { cites: Cite[]; max?: number }) {
  if (!cites?.length) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {cites.slice(0, max).map((c, i) => {
        const text = c.label || c.id;
        const cls = "rounded border border-black/[0.08] bg-black/[0.025] px-1.5 py-px font-mono text-[10.5px] text-muted";
        const href = c.kind === "fir" ? `/case?id=${encodeURIComponent(c.id)}` : c.kind === "person" ? `/suspect?id=${encodeURIComponent(c.id)}` : null;
        return href ? (
          <Link key={i} prefetch={false} href={href} className={`${cls} hover:border-brand/40 hover:text-brand`}>{text}</Link>
        ) : (
          <span key={i} className={cls}>{text}</span>
        );
      })}
      {cites.length > max && <span className="text-[10.5px] text-faint">+{cites.length - max}</span>}
    </span>
  );
}

export function IntegrityList({ flags, empty = "No record issues found." }: { flags: Flag[]; empty?: string }) {
  if (!flags?.length)
    return (
      <p className="flex items-center gap-2 rounded-xl border border-emerald/20 bg-emerald/[0.05] px-3 py-2.5 text-[12.5px] text-emerald">
        <CheckCircle2 className="h-4 w-4" /> {empty}
      </p>
    );
  return (
    <ul className="space-y-2">
      {flags.map((f, i) => (
        <li key={i} className="rounded-xl border border-black/[0.06] bg-white p-3">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityTag s={f.severity} />
            <span className="text-[13px] font-semibold text-ink">{f.title}</span>
          </div>
          <p className="mt-1 text-[12.5px] leading-relaxed text-muted">{f.detail}</p>
          <div className="mt-1.5"><Cites cites={f.cites} /></div>
        </li>
      ))}
    </ul>
  );
}

export function QuestionList({ questions }: { questions: Question[] }) {
  if (!questions?.length) return <p className="text-[12.5px] text-faint">Nothing in the records raises a question for the IO.</p>;
  return (
    <ol className="space-y-2.5">
      {questions.map((q, i) => (
        <li key={i} className="flex gap-3">
          <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-ink text-[10.5px] font-bold text-white">{i + 1}</span>
          <div className="min-w-0">
            <p className="text-[13px] leading-relaxed text-ink">{q.question}</p>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-faint">
              <span>{q.why}</span>
              <Cites cites={q.cites} max={3} />
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

const VERDICT: Record<string, { icon: any; cls: string; word: string }> = {
  C: { icon: CheckCircle2, cls: "bg-emerald/10 text-emerald", word: "Consistent" },
  I: { icon: XCircle, cls: "bg-crimson/10 text-crimson", word: "Inconsistent" },
  N: { icon: CircleDashed, cls: "bg-black/[0.03] text-faint", word: "No bearing" },
};

export function HypothesisMatrix({ hyp }: { hyp: Hypotheses }) {
  if (!hyp?.hypotheses?.length) return <p className="text-[12.5px] text-faint">No one is named, and no registry history fits this FIR.</p>;
  return (
    <div>
      <div className="overflow-x-auto rounded-xl border border-black/[0.06]">
        <table className="w-full min-w-[640px] border-collapse text-[12px]">
          <thead>
            <tr className="bg-black/[0.02]">
              <th className="w-[34%] px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-faint">Evidence</th>
              {hyp.hypotheses.map((h, i) => (
                <th key={h.id} className="px-2 py-2 text-left align-bottom">
                  <Link prefetch={false} href={`/suspect?id=${encodeURIComponent(h.id)}`} className="block font-mono text-[11.5px] font-bold text-ink hover:text-brand">
                    {i + 1}. {h.id}
                  </Link>
                  <span className="block truncate text-[10.5px] font-normal text-faint">{h.named ? "Named on FIR" : "From records"}</span>
                  <span className="mt-0.5 block text-[10.5px] font-semibold">
                    <span className="text-crimson">{h.inconsistent} against</span> · <span className="text-emerald">{h.consistent} for</span>
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {hyp.evidence.map((e) => (
              <tr key={e.key} className="border-t border-black/[0.05]">
                <td className="px-3 py-2 text-ink">
                  {e.label}
                  {!e.diagnostic && <span className="ml-1.5 text-[10px] text-faint">(does not discriminate)</span>}
                </td>
                {hyp.hypotheses.map((h) => {
                  const c = h.cells[e.key];
                  const v = VERDICT[c?.verdict || "N"];
                  const Icon = v.icon;
                  return (
                    <td key={h.id} className="px-2 py-1.5">
                      <span title={`${v.word}: ${c?.note || ""}`} className={`inline-flex max-w-[170px] items-center gap-1 rounded-md px-1.5 py-1 ${v.cls}`}>
                        <Icon className="h-3.5 w-3.5 shrink-0" />
                        <span className="truncate text-[11px]">{c?.note}</span>
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11.5px] leading-relaxed text-faint">
        {hyp.method} Never used: {hyp.excluded_factors.join(", ")}. {hyp.note}
      </p>
    </div>
  );
}

/** A Rossmo surface drawn as a plain SVG: no tiles to fetch, prints cleanly. */
export function GeoProfileView({ g, height = 260 }: { g: GeoProfile; height?: number }) {
  if (!g) return null;
  if (!g.applicable)
    return (
      <p className="flex items-start gap-2 rounded-xl border border-black/[0.06] bg-black/[0.02] px-3 py-2.5 text-[12.5px] text-muted">
        <HelpCircle className="mt-0.5 h-4 w-4 shrink-0 text-faint" /> {g.reason}
      </p>
    );
  const heat = g.heat || [];
  const sites = g.sites || [];
  const pts = [...heat.map(([la, ln]) => [la, ln]), ...sites.map((s) => [s.lat, s.lng])];
  const lats = pts.map((p) => p[0]);
  const lngs = pts.map((p) => p[1]);
  const [minLa, maxLa, minLn, maxLn] = [Math.min(...lats), Math.max(...lats), Math.min(...lngs), Math.max(...lngs)];
  const kx = Math.cos(((minLa + maxLa) / 2) * (Math.PI / 180));
  const spanX = Math.max((maxLn - minLn) * kx, 1e-6);
  const spanY = Math.max(maxLa - minLa, 1e-6);
  const W = 100;
  const H = Math.max(40, Math.min(100, (spanY / spanX) * W));
  const px = (ln: number) => ((ln - minLn) * kx * W) / spanX;
  const py = (la: number) => H - ((la - minLa) * H) / spanY;
  const uniq = (xs: number[]) => [...new Set(xs.map((x) => x.toFixed(5)))].map(Number).sort((a, b) => a - b);
  const step = (xs: number[]) => {
    const u = uniq(xs);
    let m = Infinity;
    for (let i = 1; i < u.length; i++) m = Math.min(m, u[i] - u[i - 1]);
    return Number.isFinite(m) ? m : 0.01;
  };
  const cw = ((step(heat.map((h) => h[1])) * kx * W) / spanX) * 1.05;
  const ch = ((step(heat.map((h) => h[0])) * H) / spanY) * 1.05;
  const kmPerUnit = (spanX * 111.32) / W;
  const bar = Math.max(1, Math.round(10 / kmPerUnit) * kmPerUnit);
  return (
    <div>
      <svg viewBox={`-2 -2 ${W + 4} ${H + 4}`} style={{ height }} className="w-full rounded-xl border border-black/[0.06] bg-[#fbfaf7]" role="img"
           aria-label="Geographic profile: priority surface with crime sites">
        {heat.map(([la, ln, v], i) => (
          <rect key={i} x={px(ln) - cw / 2} y={py(la) - ch / 2} width={cw} height={ch}
                fill={v > 0.75 ? "#dc2b25" : v > 0.5 ? "#e7b43c" : "#2f74c0"} opacity={0.18 + v * 0.6} />
        ))}
        {sites.map((s, i) => (
          <circle key={i} cx={px(s.lng)} cy={py(s.lat)} r={1.3} fill="#1d1d1f" stroke="#fff" strokeWidth={0.4}>
            <title>{s.case_id}</title>
          </circle>
        ))}
        {g.peak && (
          <g>
            <circle cx={px(g.peak.lng)} cy={py(g.peak.lat)} r={3.2} fill="none" stroke="#7d570f" strokeWidth={0.9} />
            <circle cx={px(g.peak.lng)} cy={py(g.peak.lat)} r={0.9} fill="#7d570f" />
          </g>
        )}
        <g transform={`translate(3 ${H - 4})`}>
          <line x1={0} x2={bar / kmPerUnit} y1={0} y2={0} stroke="#1d1d1f" strokeWidth={0.6} />
          <text x={0} y={-1.5} fontSize={2.6} fill="#6b6b73">{Math.round(bar)} km</text>
        </g>
      </svg>
      <div className="mt-2 grid grid-cols-3 gap-2 text-center">
        {[
          ["Highest priority", g.peak ? `${g.peak.lat.toFixed(4)}, ${g.peak.lng.toFixed(4)}` : "—"],
          ["50% of surface", `${g.search_area_km2?.["50"]} km²`],
          ["80% of surface", `${g.search_area_km2?.["80"]} km²`],
        ].map(([k, v]) => (
          <div key={k} className="rounded-lg bg-black/[0.03] px-2 py-1.5">
            <p className="text-[10px] uppercase tracking-wider text-faint">{k}</p>
            <p className="font-mono text-[12px] font-semibold text-ink">{v}</p>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11.5px] leading-relaxed text-faint">
        {g.method}: {g.sites_used?.length} sites, buffer {g.buffer_km} km
        {g.excluded?.length ? `; ${g.excluded.length} site(s) outside the ${g.cluster_km} km hunting area left out` : ""}. {g.note}
      </p>
    </div>
  );
}

export type TimelineEvent = { ts: string | null; source: string; kind: string; title: string; detail: string; cites: Cite[] };

const SOURCE_TONE: Record<string, string> = {
  FIR: "bg-ink text-white",
  "Arrest register": "bg-crimson/12 text-crimson",
  "Charge-sheet register": "bg-brand/12 text-brand",
  "Court register": "bg-azure/12 text-azure",
  "Stolen-vehicle register": "bg-black/[0.06] text-ink",
  CCTV: "bg-emerald/12 text-emerald",
};

export function SourceTimeline({ events, limit = 60 }: { events: TimelineEvent[]; limit?: number }) {
  if (!events?.length) return <p className="text-[12.5px] text-faint">No records name this person.</p>;
  const shown = events.slice(-limit).reverse();
  return (
    <ol className="relative space-y-2 border-l border-black/[0.08] pl-4">
      {shown.map((e, i) => (
        <li key={i} className="relative">
          <span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-black/25 ring-2 ring-white" />
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[11px] text-faint">{e.ts ? String(e.ts).slice(0, 10) : "undated"}</span>
            <span className={`rounded px-1.5 py-px text-[10px] font-semibold ${SOURCE_TONE[e.source] || "bg-black/[0.05] text-muted"}`}>{e.source}</span>
            <span className="text-[12.5px] font-semibold text-ink">{e.title}</span>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11.5px] text-muted">
            {e.detail && <span>{e.detail}</span>}
            <Cites cites={e.cites} max={2} />
          </div>
        </li>
      ))}
      {events.length > limit && <li className="text-[11px] text-faint">Showing the latest {limit} of {events.length} records.</li>}
    </ol>
  );
}

export type Statute = {
  code_in_force: string | null;
  commencement: string;
  sections: { act: string; section: string; crosswalk: { ipc: string | null; bns: string; subject: string; change: string } | null }[];
  findings: { code: string; message: string; suggestion?: { act: string; section: string } | null }[];
  source: string;
};

export function StatuteTable({ statute }: { statute: Statute }) {
  if (!statute) return null;
  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-muted">
        Code in force on the date of the offence: <b className="text-ink">{statute.code_in_force || "unknown"}</b>
        <span className="text-faint"> (the BNS replaced the IPC on {statute.commencement})</span>
      </p>
      <div className="overflow-hidden rounded-xl border border-black/[0.06]">
        <table className="w-full text-[12.5px]">
          <thead className="bg-black/[0.02] text-[11px] uppercase tracking-wider text-faint">
            <tr><th className="px-3 py-2 text-left">Booked</th><th className="px-3 py-2 text-left">Counterpart</th><th className="px-3 py-2 text-left">Subject</th></tr>
          </thead>
          <tbody>
            {statute.sections.map((s, i) => (
              <tr key={i} className="border-t border-black/[0.05]">
                <td className="px-3 py-2 font-mono font-semibold text-ink">{s.act} {s.section}</td>
                <td className="px-3 py-2 font-mono text-muted">
                  {s.crosswalk ? (s.act === "IPC" ? `BNS ${s.crosswalk.bns}` : s.crosswalk.ipc ? `IPC ${s.crosswalk.ipc}` : "new in BNS") : "unchanged act"}
                </td>
                <td className="px-3 py-2 text-muted">{s.crosswalk ? `${s.crosswalk.subject}. ${s.crosswalk.change}` : "Special law; not replaced by the BNS."}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {statute.findings.map((f, i) => (
        <p key={i} className="flex items-start gap-2 rounded-xl border border-brand/25 bg-brand/[0.06] px-3 py-2 text-[12.5px] text-ink">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-brand" /> {f.message}
        </p>
      ))}
      <p className="text-[11px] text-faint">Cross-walk: {statute.source}.</p>
    </div>
  );
}
