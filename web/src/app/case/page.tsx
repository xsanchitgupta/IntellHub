"use client";

import Link from "next/link";
import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import {
  AlertCircle,
  FileCheck2,
  ArrowLeft,
  ArrowRight,
  Network,
  AlignLeft,
  BrainCircuit,
  ShieldAlert,
  Target,
  Clock,
  Sword,
  CarFront,
  Gavel,
  Scale,
  Map as MapIcon,
  ClipboardCheck,
  Printer,
  Layers,
} from "lucide-react";
import { getJSON } from "@/lib/api";
import { HypothesisMatrix, IntegrityList, QuestionList, StatuteTable } from "@/components/Investigation";
import { FirPhoto } from "@/components/PersonPhoto";
import AccessRequired from "@/components/AccessRequired";

function Panel({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`bezel ${className}`}>
      <div className="bezel-core h-full p-6">{children}</div>
    </div>
  );
}

function SectionTitle({ icon: Icon, tone, title, sub }: { icon: any; tone: string; title: string; sub?: string }) {
  const tones: Record<string, string> = {
    brand: "bg-brand/12 text-brand",
    azure: "bg-azure/12 text-azure",
    crimson: "bg-crimson/12 text-crimson",
    emerald: "bg-emerald/12 text-emerald",
  };
  return (
    <div className="mb-5 flex items-center gap-3">
      <span className={`grid h-9 w-9 place-items-center rounded-xl ${tones[tone]}`}>
        <Icon className="h-[18px] w-[18px]" />
      </span>
      <div>
        <h2 className="text-[15px] font-bold text-ink">{title}</h2>
        {sub && <p className="text-[12px] text-faint">{sub}</p>}
      </div>
    </div>
  );
}

function CaseDossierContent() {
  const searchParams = useSearchParams();
  const id = searchParams.get("id");
  const [dossier, setDossier] = useState<any>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [loading, setLoading] = useState(true);
  const [inv, setInv] = useState<any>(null);

  useEffect(() => {
    if (!id) return;
    // The investigation check is a second read: the dossier stands without it.
    setInv(null);
    getJSON(`/investigate/case/${id}`).then(setInv).catch(() => setInv(null));
  }, [id]);

  useEffect(() => {
    if (!id) {
      setLoading(false);
      return;
    }
    setLoadError(null);
    getJSON(`/case/${id}`)
      .then(setDossier)
      .catch((e) => {
        setForbidden(Boolean(e?.isForbidden));
        setLoadError(String(e?.message || e));
      })
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return <CaseSkeleton />;
  }

  if (!id) {
    return (
      <div className="flex h-[60vh] flex-col items-center justify-center text-center">
        <AlertCircle className="mb-4 h-12 w-12 text-crimson" />
        <h1 className="text-display text-2xl font-bold text-ink">No Case ID Specified</h1>
        <p className="mt-2 text-muted">Please select a case from the directory.</p>
        <Link prefetch={false}
          href="/cases"
          className="mt-6 rounded-full border border-black/10 bg-black/[0.03] px-5 py-2.5 text-sm font-medium text-ink transition-all hover:border-brand/40"
        >
          Browse case registry
        </Link>
      </div>
    );
  }


  // An FIR dossier is a legal record. When the record cannot be read, this page
  // says so — it never substitutes generated content. A previous version returned
  // an invented dossier here, complete with a named "Prime Accused" and a
  // conviction probability, which is indistinguishable from a real one on screen.
  if (forbidden) {
    return <AccessRequired what="A case dossier" />;
  }

  if (!dossier) {
    return (
      <div className="flex h-[60vh] flex-col items-center justify-center px-6 text-center">
        <AlertCircle className="mb-4 h-12 w-12 text-crimson" />
        <h1 className="text-display text-2xl font-bold text-ink">Case dossier unavailable</h1>
        <p className="mt-2 max-w-md text-muted">
          {loadError
            ? `The record for ${id} could not be retrieved from the intelligence core.`
            : `No record was found for ${id}.`}
        </p>
        <p className="mt-3 max-w-md text-[12.5px] text-faint">
          This page renders only what the case record contains. Nothing is generated
          to fill the gap.
        </p>
        {loadError && (
          <code className="mt-4 rounded bg-black/[0.04] px-2.5 py-1 font-mono text-[11px] text-faint">
            {loadError}
          </code>
        )}
        <Link prefetch={false}
          href="/cases"
          className="mt-6 rounded-full border border-black/10 bg-black/[0.03] px-5 py-2.5 text-sm font-medium text-ink transition-all hover:border-brand/40"
        >
          Browse case registry
        </Link>
      </div>
    );
  }

  const {
    case_details,
    narrative_report,
    nlp_similar_cases,
    graph_linked_suspects,
    court,
    vehicles,
    cctv,
    officer,
    cyber,
    classifier_read,
  } = dossier;
  const isOpen = case_details.status !== "Closed";
  const sev = case_details.severity_score;
  const sevTone = sev > 70 ? "text-crimson" : sev > 40 ? "text-brand" : "text-emerald";
  const sevBar = sev > 70 ? "bg-crimson" : sev > 40 ? "bg-brand" : "bg-emerald";

  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-8">
      {/* Header */}
      <div className="flex flex-col gap-4 border-b border-black/[0.06] pb-6">
        <Link prefetch={false} href="/cases" className="flex w-fit items-center gap-2 text-sm text-faint transition-colors hover:text-ink">
          <ArrowLeft className="h-4 w-4" /> Back to registry
        </Link>
        <div className="flex flex-wrap items-center gap-4">
          {/* CCTNS case numbers are 18 unbroken digits. At text-4xl that is 360px of
              one unbreakable word, wider than a 360px phone, and it pushed the whole
              document sideways. `break-all` lets it wrap; the smaller size on narrow
              screens means it usually does not have to. */}
          <h1 className="text-display break-all text-2xl font-bold tracking-tight text-ink sm:text-4xl">{case_details.case_id}</h1>
          <span
            className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-bold ${
              !isOpen
                ? "border-emerald/25 bg-emerald/10 text-emerald"
                : "border-brand/25 bg-brand/10 text-brand"
            }`}
          >
            {!isOpen ? <FileCheck2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
            {case_details.status}
          </span>
          <p className="text-lg text-muted">
            {case_details.crime_type} · {case_details.district}
          </p>
          {case_details.CrimeNo && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-faint">
              <span>Crime No: <strong className="font-mono text-ink">{case_details.CrimeNo}</strong></span>
              <span>Case No: <strong className="font-mono text-ink">{case_details.CaseNo}</strong></span>
            </div>
          )}
          <Link prefetch={false} href={`/case/brief?id=${encodeURIComponent(case_details.case_id)}`}
                className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-black/10 bg-white px-3.5 py-2 text-[12.5px] font-semibold text-ink shadow-sm hover:border-brand/40">
            <Printer className="h-4 w-4 text-brand" /> Court-ready brief
          </Link>
        </div>
      </div>

      {/* Investigation check: record integrity, the IO's questions, competing hypotheses */}
      {inv && (
        <Panel>
          <SectionTitle icon={ClipboardCheck} tone="crimson" title="Investigation check"
                        sub="Computed from the records; every item cites the record it rests on" />
          <div className="grid gap-6 lg:grid-cols-2">
            <div>
              <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wider text-faint">
                Record issues and contradictions ({inv.integrity.length})
              </h3>
              <IntegrityList flags={inv.integrity} empty="The records for this FIR agree with each other." />
            </div>
            <div>
              <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wider text-faint">Questions for the IO</h3>
              <QuestionList questions={inv.questions} />
              {inv.series?.length > 0 && (
                <div className="mt-4 rounded-xl border border-azure/20 bg-azure/[0.05] p-3">
                  <p className="flex items-center gap-1.5 text-[12px] font-semibold text-azure"><Layers className="h-3.5 w-3.5" /> Part of a cross-station series</p>
                  <div className="mt-1.5 flex flex-wrap gap-2">
                    {inv.series.map((sr: any) => (
                      <Link key={sr.id} prefetch={false} href={`/investigate?series=${sr.id}`}
                            className="rounded-lg border border-azure/25 bg-white px-2 py-1 text-[12px] text-ink hover:border-azure/50">
                        <b className="font-mono">{sr.id}</b> · {sr.size} FIRs · {sr.stations} stations · {sr.kind === "mo" ? "same MO" : "same accused"}
                      </Link>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
          <div className="mt-6">
            <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wider text-faint">Competing hypotheses — who could have done this</h3>
            <HypothesisMatrix hyp={inv.hypotheses} />
          </div>
        </Panel>
      )}

      {/* Narrative + Location */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <Panel className="md:col-span-2">
          <div className="flex h-full flex-col justify-between">
            <div>
              <SectionTitle icon={AlignLeft} tone="azure" title="Investigator's Narrative" />
              <div className="relative mb-6 pl-6">
                <div className="absolute bottom-2 left-0 top-0 w-1 rounded-full bg-gradient-to-b from-azure to-transparent" />
                <p className="font-serif text-[16px] italic leading-relaxed text-ink/85">“{narrative_report}”</p>
              </div>

              {classifier_read && (
                <div
                  className={`mb-6 flex items-start gap-3 rounded-xl border p-3.5 ${
                    classifier_read.agrees_with_filing
                      ? "border-emerald/25 bg-emerald/[0.05]"
                      : "border-brand/30 bg-brand/[0.05]"
                  }`}
                >
                  <span
                    className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg ${
                      classifier_read.agrees_with_filing ? "bg-emerald/15 text-emerald" : "bg-brand/15 text-brand"
                    }`}
                  >
                    <BrainCircuit className="h-[18px] w-[18px]" />
                  </span>
                  <div className="min-w-0">
                    <p className="text-[11px] font-bold uppercase tracking-wider text-faint">
                      AI Narrative Check · TF-IDF + LogReg
                    </p>
                    <p className="mt-0.5 text-[13.5px] text-ink/85">
                      The classifier independently reads this report as{" "}
                      <span className="font-bold text-ink">{classifier_read.top}</span>{" "}
                      <span className="text-faint">({Math.round(classifier_read.confidence * 100)}% confidence)</span>{" "}
                      {classifier_read.agrees_with_filing ? (
                        <span className="text-emerald">— consistent with the filed classification.</span>
                      ) : (
                        <span className="text-brand">— differs from the filed “{case_details.crime_type}”; worth a review.</span>
                      )}
                    </p>
                  </div>
                </div>
              )}
            </div>

            <div className="rounded-xl border border-black/[0.06] bg-black/[0.02] p-5">
              <h3 className="mb-4 text-[11px] font-bold uppercase tracking-wider text-faint">Extracted Modus Operandi</h3>
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {[
                  { icon: Target, l: "Target", v: case_details.target_type },
                  { icon: Sword, l: "Weapon", v: case_details.weapon_used },
                  { icon: CarFront, l: "Vehicle", v: case_details.vehicle_used },
                  { icon: Clock, l: "Time", v: case_details.time_of_day },
                ].map((m) => (
                  <div key={m.l}>
                    <p className="mb-1 flex items-center gap-1 text-[11px] text-faint">
                      <m.icon className="h-3 w-3" /> {m.l}
                    </p>
                    <p className="text-[13px] font-medium text-ink">{m.v || "Unknown"}</p>
                  </div>
                ))}
              </div>
              {(case_details.ipc_bns_sections || case_details.police_station || officer) && (
                <div className="mt-5 grid grid-cols-2 gap-4 border-t border-black/[0.06] pt-5 sm:grid-cols-4">
                  <div className="col-span-2">
                    <p className="mb-1 text-[11px] text-faint">IPC / BNS Sections</p>
                    <p className="text-[13px] font-medium text-brand">{case_details.ipc_bns_sections || "—"}</p>
                  </div>
                  <div>
                    <p className="mb-1 text-[11px] text-faint">Police Station</p>
                    <p className="text-[13px] font-medium text-ink">{case_details.police_station || "—"}</p>
                  </div>
                  <div>
                    <p className="mb-1 text-[11px] text-faint">Investigating Officer</p>
                    <p className="truncate text-[13px] font-medium text-ink">
                      {officer ? `${officer.rank} ${officer.name}` : case_details.officer_id || "—"}
                    </p>
                    {officer && (
                      <span className="block text-[10px] text-muted truncate">
                        ID: {officer.officer_id} · DOB: {officer.dob} · Appt: {officer.appointment_date}
                      </span>
                    )}
                  </div>
                  <div>
                    <p className="mb-1 text-[11px] text-faint">Victim</p>
                    <p className="truncate text-[13px] font-medium text-ink">
                      {case_details.victim_name || "—"}
                      {case_details.victim_age ? `, ${case_details.victim_age}` : ""}
                      {case_details.victim_gender ? ` (${case_details.victim_gender})` : ""}
                    </p>
                    {case_details.victim_police === "1" && (
                      <span className="inline-block mt-0.5 rounded bg-brand/12 px-1.5 py-0.5 text-[9px] font-bold text-brand uppercase">
                        KSP Officer
                      </span>
                    )}
                  </div>
                  <div>
                    <p className="mb-1 text-[11px] text-faint">Complainant</p>
                    <p className="truncate text-[13px] font-medium text-ink">
                      {dossier.complainant ? dossier.complainant.ComplainantName : "—"}
                      {dossier.complainant && dossier.complainant.AgeYear ? `, ${dossier.complainant.AgeYear}` : ""}
                    </p>
                    {dossier.complainant && (
                      <span className="block text-[10px] text-muted truncate leading-normal">
                        {dossier.complainant.GenderID === 1 ? "Male" : dossier.complainant.GenderID === 2 ? "Female" : "Other"} · {dossier.complainant.religion_name} · {dossier.complainant.caste_name}
                        <span className="block text-[9.5px] text-faint">{dossier.complainant.occupation_name}</span>
                      </span>
                    )}
                  </div>
                  <div>
                    <p className="mb-1 text-[11px] text-faint">Accused / Witnesses</p>
                    <p className="text-[13px] font-medium text-ink">
                      {case_details.accused_count ?? 0} / {case_details.witnesses ?? 0}
                    </p>
                  </div>
                  <div>
                    <p className="mb-1 text-[11px] text-faint">Casualties</p>
                    <p className={`text-[13px] font-medium ${case_details.fatalities || case_details.injured ? "text-crimson" : "text-emerald"}`}>
                      {case_details.fatalities ?? 0} dead · {case_details.injured ?? 0} injured
                    </p>
                  </div>
                </div>
              )}
              {Array.isArray(dossier.photos) && dossier.photos.length > 0 && (
                <div className="mt-4 border-t border-black/[0.06] pt-3">
                  <p className="mb-2 text-[11px] text-faint">Photographed at registration</p>
                  <ul className="flex flex-wrap gap-3">
                    {dossier.photos.map((ph: { photo_id: string; role: string; name: string; person_id?: string | null }) => (
                      <li key={ph.photo_id + ph.role + ph.name} className="w-[88px]">
                        <FirPhoto photoId={ph.photo_id} alt={`${ph.role}: ${ph.name}`} className="h-[88px] w-[88px] rounded-xl border border-black/10" />
                        <p className="mt-1 truncate text-[11.5px] font-medium text-ink" title={ph.name}>{ph.name || "Unnamed"}</p>
                        <p className="text-[10px] uppercase tracking-wider text-faint">{ph.role}{ph.person_id ? ` · ${ph.person_id}` : ""}</p>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        </Panel>

        <Panel>
          <div className="flex h-full flex-col justify-between">
            <div>
              <SectionTitle icon={MapIcon} tone="brand" title="Crime Scene" />
              <div className="relative mb-4 h-40 overflow-hidden rounded-xl border border-black/[0.08]">
                <iframe
                  width="100%"
                  height="100%"
                  frameBorder="0"
                  scrolling="no"
                  src={`https://maps.google.com/maps?q=${case_details.latitude},${case_details.longitude}&t=k&z=16&ie=UTF8&iwloc=&output=embed`}
                  className="absolute inset-0 z-0 saturate-50 contrast-125"
                />
                <div className="pointer-events-none absolute inset-0 z-10 ring-1 ring-inset ring-black/10" />
              </div>
              <div className="space-y-2.5">
                <div className="flex items-center justify-between rounded-lg border border-black/[0.06] bg-black/[0.02] p-2.5">
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-faint">GPS</span>
                  <span className="tabular rounded bg-black/[0.04] px-2 py-1 font-mono text-[11px] text-ink/80">
                    {case_details.latitude.toFixed(5)}, {case_details.longitude.toFixed(5)}
                  </span>
                </div>
                <div className="flex items-center justify-between rounded-lg border border-black/[0.06] bg-black/[0.02] p-2.5">
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-faint">Date</span>
                  <span className="text-[13px] font-medium text-ink">
                    {new Date(case_details.timestamp).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
                  </span>
                </div>
              </div>
            </div>
            <div className="mt-6">
              <p className="mb-2 text-[11px] uppercase tracking-wider text-faint">Severity Assessment</p>
              <div className="flex items-center gap-3">
                <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-black/[0.05]">
                  <div className={`h-full rounded-full ${sevBar}`} style={{ width: `${sev}%` }} />
                </div>
                <span className={`text-lg font-bold ${sevTone}`}>{sev}/100</span>
              </div>
            </div>
          </div>
        </Panel>
      </div>

      {/* Similar cases + Criminal links */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel>
          <SectionTitle icon={BrainCircuit} tone="azure" title="AI Pattern Matching" sub="TF-IDF narrative + geospatial MO similarity" />
          {nlp_similar_cases?.length > 0 ? (
            <div className="space-y-2.5">
              {nlp_similar_cases.map((match: any, idx: number) => (
                <Link prefetch={false}
                  key={idx}
                  href={`/case?id=${match.case_id}`}
                  className="group flex items-center gap-4 rounded-xl border border-black/[0.06] bg-black/[0.02] p-3.5 transition-all hover:border-azure/35"
                >
                  <div className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-azure/30 bg-azure/10 text-[13px] font-bold text-azure">
                    {Math.round(match.similarity_score)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[13px] font-bold text-ink">{match.case_id}</span>
                      <span className="rounded bg-black/[0.05] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted">
                        {match.status}
                      </span>
                    </div>
                    {match.match_factors?.length > 0 && (
                      <p className="mt-0.5 truncate text-[11.5px] text-faint">{match.match_factors.join(" · ")}</p>
                    )}
                  </div>
                  <ArrowRight className="h-4 w-4 shrink-0 text-faint transition-all group-hover:translate-x-1 group-hover:text-azure" />
                </Link>
              ))}
            </div>
          ) : (
            <Empty>No strong MO matches in the database.</Empty>
          )}
        </Panel>

        <Panel>
          <SectionTitle icon={Network} tone="crimson" title="Criminal Links" sub="Suspects connected in the knowledge graph" />
          {graph_linked_suspects?.length > 0 ? (
            <div className="space-y-2.5">
              {graph_linked_suspects.map((s: any, idx: number) => (
                <Link prefetch={false}
                  key={idx}
                  href={`/suspect?id=${s.suspect_id}`}
                  className="group flex items-center justify-between gap-4 rounded-xl border border-black/[0.06] bg-black/[0.02] p-3.5 transition-all hover:border-brand/40"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <div className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-black/10 bg-gradient-to-br from-crimson/25 to-brand-deep/20">
                      <ShieldAlert className="h-5 w-5 text-crimson" />
                    </div>
                    <div className="min-w-0">
                      <p className="truncate font-bold text-ink">{s.name || s.suspect_id}</p>
                      <p className="truncate text-[11.5px] text-faint">
                        {s.gang || "Unaffiliated"} · <span className="font-mono">{s.suspect_id}</span>
                      </p>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <span className="rounded-full border border-brand/20 bg-brand/10 px-2.5 py-1 text-[11px] font-semibold text-brand">
                      {s.relationship}
                    </span>
                    <ArrowRight className="h-4 w-4 text-faint transition-all group-hover:translate-x-1 group-hover:text-brand" />
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <Empty>No suspects linked to this case yet.</Empty>
          )}
        </Panel>
      </div>

      {/* Court + Evidence */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel>
          <SectionTitle icon={Gavel} tone="azure" title="Court & Prosecution" />
          {court ? (
            <div className="space-y-2.5">
              <Row label="Court Case No." value={<span className="font-mono">{court.court_case_no}</span>} />
              <Row label="Court" value={court.court_name} />
              <div className="grid grid-cols-2 gap-2.5">
                <div className="rounded-lg border border-black/[0.06] bg-black/[0.02] p-3">
                  <span className="mb-1 block text-[11px] uppercase tracking-wider text-faint">Chargesheet</span>
                  <span className="text-[13px] font-semibold text-ink">{court.chargesheet_status}</span>
                </div>
                <div className="rounded-lg border border-black/[0.06] bg-black/[0.02] p-3">
                  <span className="mb-1 block text-[11px] uppercase tracking-wider text-faint">Verdict</span>
                  <span
                    className={`text-[13px] font-semibold ${
                      court.conviction_status === "Convicted"
                        ? "text-emerald"
                        : court.conviction_status === "Acquitted"
                        ? "text-azure"
                        : "text-brand"
                    }`}
                  >
                    {court.conviction_status}
                  </span>
                </div>
              </div>
              <Row label="Next Hearing" value={court.next_hearing} />
              {court.conviction_probability !== undefined && (
                <div className="rounded-xl border border-black/[0.06] bg-black/[0.02] p-4 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] uppercase tracking-wider text-faint">Conviction Probability</span>
                    <span className={`text-sm font-bold ${
                      court.conviction_probability > 75 
                        ? "text-crimson" 
                        : court.conviction_probability > 45 
                        ? "text-brand" 
                        : "text-emerald"
                    }`}>
                      {court.conviction_probability}%
                    </span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-black/[0.05]">
                    <div 
                      className={`h-full rounded-full ${
                        court.conviction_probability > 75 
                          ? "bg-crimson" 
                          : court.conviction_probability > 45 
                          ? "bg-brand" 
                          : "bg-emerald"
                      }`} 
                      style={{ width: `${court.conviction_probability}%` }} 
                    />
                  </div>
                  {court.probability_justification && (
                    <p className="text-[10px] text-muted leading-relaxed">
                      Factors: {court.probability_justification}
                    </p>
                  )}
                </div>
              )}
            </div>
          ) : (
            <Empty>No chargesheet filed — case under investigation.</Empty>
          )}

          {dossier.chargesheet && (
            <div className="mt-4 rounded-xl border border-black/[0.06] bg-black/[0.02] p-4 space-y-2">
              <p className="text-[11.5px] uppercase tracking-wider text-faint font-bold flex items-center gap-1.5">
                <BrainCircuit className="h-3.5 w-3.5 text-brand" /> SCRB Chargesheet Submission
              </p>
              <div className="text-[12.5px] space-y-1 text-ink">
                <p><span className="text-muted font-medium">CS ID:</span> #{dossier.chargesheet.cs_id}</p>
                <p><span className="text-muted font-medium">Filing Date:</span> {dossier.chargesheet.date}</p>
                <p><span className="text-muted font-medium">Report Type:</span> <span className="font-semibold text-brand">{dossier.chargesheet.report_type}</span></p>
                <p><span className="text-muted font-medium">Investigator:</span> {dossier.chargesheet.submitting_officer}</p>
              </div>
            </div>
          )}
        </Panel>

        <Panel>
          <SectionTitle icon={CarFront} tone="brand" title="Evidence Trail" />
          <div className="space-y-2.5">
            {cyber && (
              <div className="rounded-xl border border-crimson/20 bg-crimson/[0.05] p-3.5">
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-crimson">
                  Cyber Trail · ₹{Number(cyber.fraud_amount).toLocaleString("en-IN")} fraud
                </p>
                <p className="break-all font-mono text-[11.5px] text-muted">
                  UPI: {cyber.upi_ids} · Phone: {cyber.phone_numbers}
                </p>
              </div>
            )}
            {vehicles?.map((v: any, i: number) => (
              <div key={i} className="flex items-center justify-between rounded-lg border border-black/[0.06] bg-black/[0.02] p-3">
                <div>
                  <p className="font-mono text-[13px] text-ink">{v.vehicle_number}</p>
                  <p className="text-[11.5px] text-faint">{v.make_model}</p>
                </div>
                <span
                  className={`rounded-full border px-2 py-0.5 text-[11px] ${
                    v.recovery_status === "Recovered"
                      ? "border-emerald/20 bg-emerald/10 text-emerald"
                      : "border-crimson/20 bg-crimson/10 text-crimson"
                  }`}
                >
                  {v.recovery_status}
                </span>
              </div>
            ))}
            {cctv?.map((cam: any, i: number) => (
              <div key={i} className="flex items-center justify-between rounded-lg border border-black/[0.06] bg-black/[0.02] p-3">
                <div>
                  <p className="font-mono text-[13px] text-ink">{cam.camera_id}</p>
                  <p className="text-[11.5px] text-faint">{cam.detected_vehicle ? `Vehicle: ${cam.detected_vehicle}` : "Face capture"}</p>
                </div>
                <span className="text-[11px] text-azure">CCTV</span>
              </div>
            ))}
            {!cyber && !vehicles?.length && !cctv?.length && <Empty>No physical or digital evidence linked yet.</Empty>}
          </div>
        </Panel>
      </div>

      {/* Arrests, AI Suggestions & Statutory Laws */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3 mt-6">
        {/* Arrest & Custody Trail */}
        <Panel>
          <SectionTitle icon={ShieldAlert} tone="crimson" title="Arrest & Custody Events" />
          <div className="space-y-3">
            {dossier.arrests && dossier.arrests.length > 0 ? (
              dossier.arrests.map((arr: any) => (
                <div key={arr.arrest_id} className="rounded-xl border border-black/[0.06] bg-black/[0.02] p-4">
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-bold text-ink">{arr.accused_name}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                      arr.type === "Arrest" ? "bg-crimson/10 text-crimson" : "bg-emerald/10 text-emerald"
                    }`}>
                      {arr.type}
                    </span>
                  </div>
                  <div className="text-[12px] space-y-1 text-muted">
                    <p>Date of Event: <span className="font-medium text-ink">{arr.date}</span></p>
                    <p>Arresting Officer: <span className="font-medium text-ink">{arr.io_name}</span></p>
                    <p>Produced Before: <span className="font-medium text-ink">{arr.court_name}</span></p>
                    <p className="text-[10px] text-faint pt-1">
                      Primary Accused: {arr.is_primary ? "Yes" : "No"} · Complainant is Accused: {arr.is_complainant_accused ? "Yes" : "No"}
                    </p>
                  </div>
                </div>
              ))
            ) : (
              <Empty>No arrests or voluntary surrenders registered for this case.</Empty>
            )}
          </div>
        </Panel>

        {/* AI Suggested Suspects */}
        <Panel>
          <SectionTitle icon={BrainCircuit} tone="brand" title="AI Suggested Suspects" />
          <div className="space-y-3">
            {dossier.ai_suggested_suspects && dossier.ai_suggested_suspects.length > 0 ? (
              dossier.ai_suggested_suspects.map((s: any) => (
                <div key={s.suspect_id} className="rounded-xl border border-brand/15 bg-brand/[0.02] p-4 transition-all hover:border-brand/40">
                  <div className="flex items-center justify-between mb-2">
                    <Link prefetch={false} href={`/suspect?id=${s.suspect_id}`} className="font-bold text-ink hover:text-brand transition-colors">
                      {s.name}
                    </Link>
                    <span className="rounded bg-brand/12 px-2 py-0.5 text-[11px] font-bold text-brand">
                      {s.match_score}% Match
                    </span>
                  </div>
                  
                  <div className="text-[12px] space-y-1.5 text-muted">
                    <p className="flex items-center gap-1.5">
                      <span className="h-1.5 w-1.5 rounded-full bg-crimson" />
                      Risk Level: <span className="font-semibold text-crimson">{s.risk_level}</span>
                    </p>
                    
                    {s.justifications && s.justifications.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {s.justifications.map((j: string, idx: number) => {
                          let label = j;
                          if (j.includes("weapon")) label = "Weapon Match";
                          else if (j.includes("vehicle")) label = "Vehicle Match";
                          else if (j.includes("type")) label = "MO Match";
                          else if (j.includes("district") || j.includes("scene") || j.includes("km")) label = "Proximity";
                          else if (j.includes("Rowdy") || j.includes("warrant") || j.includes("arrests")) label = "Risk Profile";
                          
                          return (
                            <span key={idx} className="rounded-full bg-brand/8 border border-brand/12 text-brand px-2 py-0.5 text-[9.5px] font-medium" title={j}>
                              {label}
                            </span>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              ))
            ) : (
              <Empty>No high-probability suspects flagged by MO or proximity.</Empty>
            )}
          </div>
        </Panel>

        {/* Statutory Provisions */}
        <Panel>
          <SectionTitle icon={Scale} tone="azure" title="Statutory Provisions" sub="The sections this FIR is booked under, against the code in force" />
          {inv?.statute ? (
            <StatuteTable statute={inv.statute} />
          ) : (
            <div className="rounded-xl border border-black/[0.06] bg-black/[0.02] p-4">
              <p className="mb-2 text-[11.5px] font-semibold uppercase tracking-wider text-faint">Filed Acts & Sections</p>
              <p className="text-[14px] font-bold leading-relaxed text-brand">{case_details.ipc_bns_sections || "None filed"}</p>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-black/[0.06] bg-black/[0.02] p-3">
      <span className="text-[11px] uppercase tracking-wider text-faint">{label}</span>
      <span className="text-right text-[13px] text-ink">{value}</span>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-28 items-center justify-center rounded-xl border border-dashed border-black/[0.08] bg-black/[0.01]">
      <p className="text-[13px] text-faint">{children}</p>
    </div>
  );
}

function CaseSkeleton() {
  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-8 animate-pulse">
      {/* Header */}
      <div className="flex flex-col gap-4 border-b border-black/[0.06] pb-6">
        <div className="h-4 w-28 bg-black/[0.05] rounded-full" />
        <div className="flex flex-wrap items-center gap-4">
          <div className="h-10 w-64 bg-black/[0.08] rounded-lg" />
          <div className="h-6 w-24 bg-black/[0.05] rounded-full" />
          <div className="h-6 w-32 bg-black/[0.05] rounded-full" />
        </div>
      </div>

      {/* Narrative + Map */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        {/* Narrative Panel */}
        <div className="bezel md:col-span-2">
          <div className="bezel-core p-6 space-y-6">
            <div className="flex items-center gap-2">
              <div className="h-9 w-9 rounded-xl bg-black/[0.06]" />
              <div className="h-5 w-48 bg-black/[0.08] rounded-lg" />
            </div>
            <div className="space-y-3">
              <div className="h-4 w-full bg-black/[0.05] rounded-full" />
              <div className="h-4 w-full bg-black/[0.05] rounded-full" />
              <div className="h-4 w-5/6 bg-black/[0.05] rounded-full" />
            </div>
            <div className="h-16 w-full bg-black/[0.03] rounded-xl" />
            <div className="h-28 w-full bg-black/[0.03] rounded-xl" />
          </div>
        </div>

        {/* Map Panel */}
        <div className="bezel">
          <div className="bezel-core p-6 space-y-6">
            <div className="flex items-center gap-2">
              <div className="h-9 w-9 rounded-xl bg-black/[0.06]" />
              <div className="h-5 w-32 bg-black/[0.08] rounded-lg" />
            </div>
            <div className="h-44 w-full bg-black/[0.05] rounded-xl" />
            <div className="space-y-2">
              <div className="h-3 w-full bg-black/[0.04] rounded-full" />
              <div className="h-3 w-2/3 bg-black/[0.04] rounded-full" />
            </div>
          </div>
        </div>
      </div>

      {/* Middle cards grid */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        {[1, 2, 3].map((idx) => (
          <div key={idx} className="bezel">
            <div className="bezel-core p-6 space-y-4">
              <div className="flex items-center gap-2">
                <div className="h-9 w-9 rounded-xl bg-black/[0.06]" />
                <div className="h-5 w-36 bg-black/[0.08] rounded-lg" />
              </div>
              <div className="space-y-2.5">
                {[1, 2, 3].map((row) => (
                  <div key={row} className="flex justify-between items-center">
                    <div className="h-3.5 w-24 bg-black/[0.05] rounded-full" />
                    <div className="h-3.5 w-16 bg-black/[0.05] rounded-full" />
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function CaseDossier() {
  return (
    <Suspense fallback={<CaseSkeleton />}>
      <CaseDossierContent />
    </Suspense>
  );
}
