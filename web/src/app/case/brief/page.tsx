"use client";

/* Court-ready case brief.
 *
 * One printable sheet for the IO and the Public Prosecutor, assembled from the case
 * dossier (GET /case/<id>) and the investigation check (GET /investigate/case/<id>):
 * particulars, the sections against the code in force on the offence date, the facts,
 * the parties, the proceedings so far, the electronic evidence with a draft
 * certificate for it, and the record issues to resolve before anything is filed.
 *
 * Caste and religion are not on it: they are not facts of the offence. The sheet is a
 * draft from the records, and says so on every printed page.
 */

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Loader2, Printer } from "lucide-react";
import { ApiError, getJSON } from "@/lib/api";
import AccessRequired from "@/components/AccessRequired";

const GENDER: Record<number, string> = { 1: "Male", 2: "Female", 3: "Other" };

function H({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <h2 className="mb-2 mt-7 flex items-baseline gap-2 border-b border-black/15 pb-1 text-[13px] font-bold uppercase tracking-wider text-ink break-after-avoid">
      <span className="font-mono text-faint">{n}.</span> {children}
    </h2>
  );
}

function Grid({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <table className="w-full text-[12.5px]">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k} className="border-b border-black/[0.06] align-top">
            <td className="w-48 py-1.5 pr-3 text-muted">{k}</td>
            <td className="py-1.5 text-ink">{v ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Blank({ label }: { label: string }) {
  return (
    <div className="flex items-end gap-2 py-1 text-[12px]">
      <span className="shrink-0 text-muted">{label}</span>
      <span className="flex-1 border-b border-dotted border-black/30">&nbsp;</span>
    </div>
  );
}

function Brief() {
  const id = useSearchParams().get("id");
  const [d, setD] = useState<any>(null);
  const [inv, setInv] = useState<any>(null);
  const [state, setState] = useState<"loading" | "ok" | "forbidden" | "missing">("loading");

  useEffect(() => {
    if (!id) {
      setState("missing");
      return;
    }
    Promise.all([getJSON(`/case/${encodeURIComponent(id)}`), getJSON(`/investigate/case/${encodeURIComponent(id)}`).catch(() => null)])
      .then(([dossier, check]) => {
        if (!dossier?.case_details) return setState("missing");
        setD(dossier);
        setInv(check);
        setState("ok");
      })
      .catch((e) => setState(e instanceof ApiError && e.isForbidden ? "forbidden" : "missing"));
  }, [id]);

  if (state === "loading") return <p className="flex items-center gap-2 py-20 text-[13px] text-faint"><Loader2 className="h-4 w-4 animate-spin" /> Assembling the brief…</p>;
  if (state === "forbidden") return <AccessRequired what="This case brief" />;
  if (state === "missing" || !d) return <p className="py-20 text-[13px] text-muted">No FIR with that number. <Link href="/cases" className="text-brand">Browse cases</Link></p>;

  const c = d.case_details;
  const st = inv?.statute;
  const today = new Date().toISOString().slice(0, 10);
  const issues = (inv?.integrity || []) as any[];
  const comp = d.complainant;
  const cctv = (d.cctv || []) as any[];
  const vehicles = (d.vehicles || []) as any[];
  const findingFor = (act: string, section: string) =>
    (st?.findings || []).find((f: any) => f.act === act && String(f.section) === String(section));

  return (
    <div className="mx-auto max-w-[820px] pb-10 print:max-w-none print:pb-0">
      <div className="mb-5 flex items-center gap-3 print:hidden">
        <Link prefetch={false} href={`/case?id=${encodeURIComponent(c.case_id)}`} className="inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-ink">
          <ArrowLeft className="h-4 w-4" /> Back to the dossier
        </Link>
        <button onClick={() => window.print()} className="ml-auto inline-flex items-center gap-1.5 rounded-xl bg-ink px-4 py-2 text-[13px] font-semibold text-white">
          <Printer className="h-4 w-4" /> Print or save as PDF
        </button>
      </div>

      <article className="rounded-2xl border border-black/10 bg-white px-10 py-9 shadow-sm print:rounded-none print:border-0 print:p-0 print:shadow-none">
        <header className="border-b-2 border-ink pb-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted">Karnataka State Police · {c.district} district · {c.police_station}</p>
          <h1 className="mt-1 text-[22px] font-bold leading-tight text-ink">Case brief — FIR {c.case_id}</h1>
          <p className="mt-1 text-[12px] text-muted">
            Prepared {today} from the records in KSP IntelHub. A working draft for the IO and the prosecutor: verify every entry
            against the case diary before it is relied on.
          </p>
        </header>

        <H n={1}>Particulars</H>
        <Grid rows={[
          ["Crime No. / Case No.", c.CrimeNo ? `${c.CrimeNo} / ${c.CaseNo}` : "—"],
          ["Police station", `${c.police_station}, ${c.district}`],
          ["Date and time of offence", c.timestamp ? String(c.timestamp).replace("T", " ").slice(0, 16) : "—"],
          ["Offence", `${c.crime_type}${c.time_of_day ? ` · ${c.time_of_day}` : ""}`],
          ["Modus operandi", [c.weapon_used !== "None" && `weapon: ${c.weapon_used}`, c.vehicle_used !== "None" && `vehicle: ${c.vehicle_used}`, c.target_type && `target: ${c.target_type}`].filter(Boolean).join(" · ") || "—"],
          ["Casualties", `${c.fatalities ?? 0} killed · ${c.injured ?? 0} injured · ${c.witnesses ?? 0} witnesses`],
          ["Status", c.status],
          ["Investigating officer", d.officer?.name ? `${d.officer.name}${d.officer.rank ? `, ${d.officer.rank}` : ""}` : c.officer_id || "—"],
        ]} />

        <H n={2}>Sections charged</H>
        {st ? (
          <>
            <p className="mb-2 text-[12px] text-muted">
              Code in force on the date of the offence: <b className="text-ink">{st.code_in_force === "BNS" ? "Bharatiya Nyaya Sanhita, 2023" : "Indian Penal Code, 1860"}</b>{" "}
              (the BNS commenced on {st.commencement}).
            </p>
            <table className="w-full text-[12.5px]">
              <thead className="text-[10.5px] uppercase tracking-wider text-faint">
                <tr className="border-b border-black/15"><th className="w-24 whitespace-nowrap py-1 text-left">As filed</th><th className="py-1 text-left">Subject</th><th className="py-1 text-left">Corresponding section</th></tr>
              </thead>
              <tbody>
                {st.sections.map((s: any) => {
                  const f = findingFor(s.act, s.section);
                  const cw = s.crosswalk;
                  return (
                    <tr key={`${s.act}${s.section}`} className="border-b border-black/[0.06] align-top">
                      <td className="whitespace-nowrap py-1.5 pr-3 font-mono font-semibold text-ink">{s.act} {s.section}</td>
                      <td className="py-1.5 text-ink">{cw?.subject || "—"}</td>
                      <td className="py-1.5 text-muted">
                        {cw ? (s.act === "IPC" ? `BNS ${cw.bns}` : cw.ipc ? `IPC ${cw.ipc}` : "New in the BNS") : "Not affected by the change of code"}
                        {f && <p className="mt-0.5 font-semibold text-crimson">{f.message}</p>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="mt-1 text-[10.5px] text-faint">Cross-walk source: {st.source}.</p>
          </>
        ) : (
          <p className="text-[12.5px] text-ink">{c.ipc_bns_sections || "None filed"}</p>
        )}

        <H n={3}>Brief facts</H>
        <p className="whitespace-pre-line text-[12.5px] leading-relaxed text-ink">{d.narrative_report && d.narrative_report !== "No report." ? d.narrative_report : "No narrative is recorded for this FIR."}</p>

        <H n={4}>Parties</H>
        <Grid rows={[
          ["Complainant", comp ? `${comp.ComplainantName}${comp.AgeYear ? `, ${comp.AgeYear}` : ""}${GENDER[comp.GenderID] ? `, ${GENDER[comp.GenderID]}` : ""}${comp.occupation_name ? ` · ${comp.occupation_name}` : ""}` : "—"],
          ["Victim", c.victim_name ? `${c.victim_name}${c.victim_age ? `, ${c.victim_age}` : ""}${c.victim_gender ? `, ${c.victim_gender}` : ""}${c.victim_police ? " · police officer" : ""}` : "—"],
          ["Accused", `${c.accused_count ?? (d.arrests || []).length} named${(d.arrests || []).length ? ` · ${(d.arrests || []).map((a: any) => a.accused_name).filter((x: string, i: number, xs: string[]) => xs.indexOf(x) === i).join(", ")}` : ""}`],
        ]} />

        <H n={5}>Proceedings so far</H>
        {(d.arrests || []).length > 0 ? (
          <table className="mb-3 w-full text-[12.5px]">
            <thead className="text-[10.5px] uppercase tracking-wider text-faint">
              <tr className="border-b border-black/15"><th className="py-1 text-left">Date</th><th className="py-1 text-left">Accused</th><th className="py-1 text-left">Arrest / surrender</th><th className="py-1 text-left">Produced before</th></tr>
            </thead>
            <tbody>
              {d.arrests.map((a: any) => (
                <tr key={a.arrest_id} className="border-b border-black/[0.06]">
                  <td className="py-1.5 font-mono">{String(a.date).slice(0, 10)}</td>
                  <td className="py-1.5">{a.accused_name}</td>
                  <td className="py-1.5">{a.type} · by {a.io_name}</td>
                  <td className="py-1.5 text-muted">{a.court_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="mb-3 text-[12.5px] text-muted">No arrest or surrender is recorded.</p>}
        <Grid rows={[
          ["Final report", d.chargesheet ? `${d.chargesheet.report_type} · ${String(d.chargesheet.date).slice(0, 10)} · by ${d.chargesheet.submitting_officer}` : "Not yet filed"],
          ["Court", d.court?.court_name ? `${d.court.court_name}${d.court.court_case_no ? ` · ${d.court.court_case_no}` : ""}` : "Not yet assigned"],
          ["Trial status", d.court?.conviction_status || "—"],
          ["Next hearing", d.court?.next_hearing || "—"],
        ]} />

        <H n={6}>Electronic evidence</H>
        {cctv.length || vehicles.length ? (
          <ul className="list-disc space-y-0.5 pl-5 text-[12.5px] text-ink">
            {cctv.map((x, i) => (
              <li key={`c${i}`}>CCTV camera {x.camera_id} · {String(x.timestamp).replace("T", " ").slice(0, 16)}{x.detected_vehicle ? ` · vehicle ${x.detected_vehicle} detected` : ""}</li>
            ))}
            {vehicles.map((v, i) => (
              <li key={`v${i}`}>Stolen-vehicle record {v.vehicle_number}{v.make_model ? ` (${v.make_model})` : ""} · {v.recovery_status}</li>
            ))}
          </ul>
        ) : <p className="text-[12.5px] text-muted">No camera footage or vehicle record is linked to this FIR.</p>}

        {cctv.length > 0 && (
          <section className="mt-4 rounded-lg border-2 border-dashed border-black/25 p-4 break-inside-avoid">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-crimson">Draft — to be completed and signed; not a certificate until then</p>
            <h3 className="mt-1 text-[13px] font-bold text-ink">Certificate for electronic records — Section 63(4), Bharatiya Sakshya Adhiniyam, 2023</h3>
            <p className="mt-1 text-[11.5px] leading-relaxed text-muted">
              For proceedings pending before 1 July 2024 the equivalent is Section 65B(4) of the Indian Evidence Act, 1872. The
              records it covers are the {cctv.length} CCTV clip{cctv.length > 1 ? "s" : ""} listed above. Part A is made by the person in
              charge of the device; Part B by an expert. Hash values are computed on the exported copy, not taken from this page.
            </p>
            <div className="mt-3 grid gap-5 sm:grid-cols-2 print:grid-cols-2">
              <div>
                <p className="text-[11.5px] font-bold text-ink">Part A — by the party</p>
                <Blank label="Name, designation" />
                <Blank label="Device / system (camera IDs)" />
                <Blank label="Regularly used for, by" />
                <Blank label="Operating properly during" />
                <Blank label="Hash (SHA-256) of exported copy" />
                <Blank label="Place, date, signature" />
              </div>
              <div>
                <p className="text-[11.5px] font-bold text-ink">Part B — by the expert</p>
                <Blank label="Name, designation" />
                <Blank label="Examined (media, files)" />
                <Blank label="Hash verified (SHA-256)" />
                <Blank label="Opinion" />
                <Blank label="Place, date, signature" />
              </div>
            </div>
          </section>
        )}

        <H n={7}>Items to resolve before filing</H>
        {issues.length ? (
          <ol className="list-decimal space-y-1.5 pl-5 text-[12.5px] text-ink">
            {issues.map((f: any, i: number) => (
              <li key={i}>
                <b>{f.title}.</b> {f.detail}
                {f.question && !f.question.includes(f.detail) && <span className="text-muted"> — {f.question}</span>}
              </li>
            ))}
          </ol>
        ) : inv ? (
          <p className="text-[12.5px] text-muted">The records for this FIR agree with each other: no date, status or statute conflict was found.</p>
        ) : (
          <p className="text-[12.5px] text-muted">The record-integrity check could not be run for this FIR.</p>
        )}
        {inv?.series?.length > 0 && (
          <p className="mt-2 text-[12.5px] text-ink">
            This FIR is part of {inv.series.length === 1 ? "a cross-station series" : `${inv.series.length} cross-station series`}:{" "}
            {inv.series.map((s: any) => `${s.id} (${s.size} ${s.crime_type} FIRs, ${s.stations} stations)`).join("; ")}. Consult those IOs before the final report.
          </p>
        )}

        <footer className="mt-10 grid grid-cols-2 gap-10 border-t border-black/15 pt-6 text-[12px] text-muted">
          <div>
            <div className="h-10 border-b border-black/30" />
            <p className="mt-1">Investigating officer — name, rank, signature</p>
          </div>
          <div>
            <div className="h-10 border-b border-black/30" />
            <p className="mt-1">Station House Officer — name, signature, seal</p>
          </div>
          <p className="col-span-2 text-[10.5px] text-faint">FIR {c.case_id} · prepared {today} · a draft from the records, not a court document until verified and signed.</p>
        </footer>
      </article>
    </div>
  );
}

export default function CaseBriefPage() {
  return (
    <Suspense fallback={null}>
      <Brief />
    </Suspense>
  );
}
