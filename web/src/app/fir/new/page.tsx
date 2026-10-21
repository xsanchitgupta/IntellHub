"use client";

/* Register a new FIR.
 *
 * Every dropdown is filled from GET /fir/masters (the CCTNS master tables), and the
 * server re-validates every code on POST /fir/register — the form is a convenience,
 * never the check. Field errors come back keyed by field ("victims.0.name") and are
 * shown on the step that owns them.
 *
 * Station choice is narrowed to the officer's jurisdiction from the signed token: a
 * station role sees its own station only, a district role its district's stations.
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FilePlus2,
  Loader2,
  Plus,
  Search,
  ShieldAlert,
  Trash2,
  UserRound,
} from "lucide-react";
import { apiFetchRaw, getJSON } from "@/lib/api";
import { useIdentity } from "@/lib/session";
import { PhotoField, type PhotoValue } from "@/components/PersonPhoto";

type Opt = { id: number; name: string };
type Masters = {
  districts: Opt[];
  stations: (Opt & { district_id: number })[];
  case_categories: Opt[];
  gravity: Opt[];
  crime_heads: Opt[];
  crime_types: (Opt & {
    head_id: number;
    suggested_section: { act: string; section: string } | null;
    suggested_section_bns: { act: string; section: string } | null;
    base_severity: number;
  })[];
  acts: { code: string; name: string }[];
  sections: { act: string; section: string; description: string }[];
  /** The day the BNS replaced the IPC; the date of the offence decides which applies. */
  bns_commencement: string;
  courts: Opt[];
  officers: (Opt & { kgid: string; rank: string; unit_id: number; district_id: number })[];
  occupations: Opt[];
  religions: Opt[];
  castes: Opt[];
  genders: Opt[];
  weapons: string[];
  vehicles: string[];
  targets: string[];
};
type Person = {
  name: string;
  age: string;
  gender_id: string;
  person_id?: string;
  is_police?: boolean;
  occupation_id?: string;
  religion_id?: string;
  caste_id?: string;
  /** Uploaded already; only photo_id is sent with the registration. */
  photo?: PhotoValue | null;
};
type IO = { officer_id: number; name: string; rank: string; kgid: string; open_cases: number; score: number; reasons: string[] };
type Hit = { person_id: string; name: string; alias?: string; risk_level?: string; rowdy_sheeter: boolean; active_warrant: boolean };

const STEPS = ["Incident & station", "Offence & sections", "People", "Facts & review"];
const input =
  "w-full rounded-xl border border-black/10 bg-white px-3.5 py-2.5 text-[13px] text-ink focus:border-brand/50 focus:outline-none";
const label = "mb-1 block text-[11px] font-bold uppercase tracking-wider text-faint";

function localNow() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

function Field({ k, errors, children, title, hint }: { k?: string; errors: Record<string, string>; children: React.ReactNode; title: string; hint?: string }) {
  const err = k ? errors[k] : undefined;
  return (
    <div>
      <label className={label}>{title}</label>
      {children}
      {err ? <p className="mt-1 text-[11.5px] text-crimson">{err}</p> : hint ? <p className="mt-1 text-[11px] text-faint">{hint}</p> : null}
    </div>
  );
}

export default function NewFIRPage() {
  const { identity, loading: idLoading } = useIdentity();
  const [m, setM] = useState<Masters | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState<any>(null);

  const [f, setF] = useState({
    district_id: "",
    station_id: "",
    case_category_id: "1",
    gravity_id: "",
    crime_type_id: "",
    officer_id: "",
    court_id: "",
    incident_from: "",
    incident_to: "",
    info_received: "",
    place_of_occurrence: "",
    latitude: "",
    longitude: "",
    weapon: "None",
    vehicle: "None",
    target: "",
    fatalities: "0",
    injured: "0",
    witnesses: "0",
    brief_facts: "",
  });
  const [sections, setSections] = useState<{ act: string; section: string }[]>([]);
  const [complainant, setComplainant] = useState<Person>({ name: "", age: "", gender_id: "", occupation_id: "", religion_id: "", caste_id: "" });
  const [victims, setVictims] = useState<Person[]>([{ name: "", age: "", gender_id: "" }]);
  const [accused, setAccused] = useState<Person[]>([]);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);

  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setF((s) => ({ ...s, [k]: e.target.value }));

  useEffect(() => {
    getJSON<Masters>("/fir/masters").then(setM).catch(() => setLoadErr("Could not load the master tables."));
  }, []);

  const scope = identity?.scope;
  const canRegister = !!identity?.permissions?.includes("register_fir");

  const districts = useMemo(() => {
    if (!m) return [];
    if (scope?.level === "district" || scope?.level === "station") return m.districts.filter((d) => d.name === scope.district);
    return m.districts;
  }, [m, scope]);
  const stations = useMemo(() => {
    if (!m || !f.district_id) return [];
    let s = m.stations.filter((x) => String(x.district_id) === f.district_id);
    if (scope?.level === "station") s = s.filter((x) => x.name === scope.station);
    return s;
  }, [m, f.district_id, scope]);
  const officers = useMemo(() => {
    if (!m) return [];
    const inStation = m.officers.filter((o) => String(o.unit_id) === f.station_id);
    const inDistrict = m.officers.filter((o) => String(o.district_id) === f.district_id && String(o.unit_id) !== f.station_id);
    const rest = m.officers.filter((o) => String(o.district_id) !== f.district_id);
    return [...inStation, ...inDistrict, ...rest];
  }, [m, f.station_id, f.district_id]);

  // Default the district/station to the officer's own.
  useEffect(() => {
    if (districts.length === 1 && !f.district_id) setF((s) => ({ ...s, district_id: String(districts[0].id) }));
  }, [districts, f.district_id]);
  useEffect(() => {
    if (stations.length === 1 && !f.station_id) setF((s) => ({ ...s, station_id: String(stations[0].id) }));
  }, [stations, f.station_id]);

  const crimeType = m?.crime_types.find((c) => String(c.id) === f.crime_type_id);
  // The penal code in force on the day of the offence: IPC before the BNS commenced, BNS after.
  const code = m && (f.incident_from || localNow()).slice(0, 10) < m.bns_commencement ? "IPC" : "BNS";
  const suggested = crimeType && (code === "BNS" ? crimeType.suggested_section_bns : crimeType.suggested_section);
  const other = crimeType && (code === "BNS" ? crimeType.suggested_section : crimeType.suggested_section_bns);
  const wrongCode = sections.filter((x) => (x.act === "IPC" || x.act === "BNS") && x.act !== code);
  // Suggest the section for the crime type under the code in force, and the gravity its severity implies.
  useEffect(() => {
    if (!crimeType) return;
    const onlyOther = sections.length === 1 && other && sections[0].act === other.act && sections[0].section === other.section;
    if (suggested && (!sections.length || onlyOther)) setSections([suggested]);
    if (!f.gravity_id && m) {
      const heinous = crimeType.base_severity > 75;
      const g = m.gravity.find((x) => x.name === (heinous ? "Heinous" : "Non-Heinous"));
      if (g) setF((s) => ({ ...s, gravity_id: String(g.id) }));
    }
  }, [crimeType, code]); // eslint-disable-line react-hooks/exhaustive-deps

  // A shortlist of investigating officers for this station and crime type, with the reasons.
  const [ios, setIos] = useState<IO[]>([]);
  useEffect(() => {
    if (!f.station_id) {
      setIos([]);
      return;
    }
    const q = new URLSearchParams({ station_id: f.station_id, ...(crimeType && { crime_type: crimeType.name }) });
    getJSON<{ officers: IO[] }>(`/investigate/recommend-io?${q}`)
      .then((r) => setIos(r.officers.slice(0, 3)))
      .catch(() => setIos([]));
  }, [f.station_id, crimeType]);
  const ioChips = ios.length > 0 && (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {ios.map((o) => (
        <button key={o.officer_id} type="button" title={o.reasons.join(" · ")}
                onClick={() => setF((s) => ({ ...s, officer_id: String(o.officer_id) }))}
                className={`rounded-lg border px-2 py-1 text-left text-[11px] transition-colors ${
                  f.officer_id === String(o.officer_id) ? "border-brand/50 bg-brand/10 text-ink" : "border-black/10 text-muted hover:border-brand/40 hover:text-ink"
                }`}>
          <b className="text-ink">{o.name}</b> · {o.rank} · {o.open_cases} open
        </button>
      ))}
    </div>
  );

  useEffect(() => {
    if (q.trim().length < 2) {
      setHits([]);
      return;
    }
    const t = setTimeout(() => {
      getJSON<{ results: Hit[] }>(`/fir/person-search?q=${encodeURIComponent(q.trim())}`)
        .then((r) => setHits(r.results))
        .catch(() => setHits([]));
    }, 220);
    return () => clearTimeout(t);
  }, [q]);

  const stepOf = (key: string) => {
    if (/^(district|station_id|case_category|incident|info_received|place|latitude|longitude|officer_id|court_id)/.test(key)) return 0;
    if (/^(crime_type|gravity|sections|weapon|vehicle|target|fatalities|injured|witnesses)/.test(key)) return 1;
    if (/^(complainant|victims|accused)/.test(key)) return 2;
    return 3;
  };

  async function submit() {
    setSubmitting(true);
    setErrors({});
    const num = (v: string) => (v === "" ? null : Number(v));
    const person = ({ photo, ...p }: Person) => ({ ...p, age: num(p.age), gender_id: num(p.gender_id), photo_id: photo?.photo_id ?? null });
    const body = {
      ...f,
      station_id: num(f.station_id),
      case_category_id: num(f.case_category_id),
      gravity_id: num(f.gravity_id),
      crime_type_id: num(f.crime_type_id),
      officer_id: num(f.officer_id),
      court_id: num(f.court_id),
      latitude: num(f.latitude),
      longitude: num(f.longitude),
      fatalities: num(f.fatalities),
      injured: num(f.injured),
      witnesses: num(f.witnesses),
      incident_to: f.incident_to || f.incident_from,
      sections,
      complainant: {
        ...person(complainant),
        occupation_id: num(complainant.occupation_id || ""),
        religion_id: num(complainant.religion_id || ""),
        caste_id: num(complainant.caste_id || ""),
      },
      victims: victims.map(person),
      accused: accused.map(person),
    };
    try {
      const res = await apiFetchRaw("/fir/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setDone(data);
        return;
      }
      if (res.status === 400 && data.fields) {
        setErrors(data.fields);
        const first = Object.keys(data.fields).map(stepOf).sort()[0];
        setStep(first ?? 0);
      } else if (res.status === 403) {
        setErrors({ _form: data.message && data.message !== "register_fir" ? data.message : "Your role cannot register FIRs." });
      } else {
        setErrors({ _form: `Registration failed (${res.status}).` });
      }
    } catch {
      setErrors({ _form: "The server could not be reached." });
    } finally {
      setSubmitting(false);
    }
  }

  if (done)
    return (
      <div className="mx-auto max-w-2xl px-4 py-16">
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="bezel">
          <div className="bezel-core p-8 text-center">
            <CheckCircle2 className="mx-auto h-12 w-12 text-emerald" />
            <h1 className="mt-3 text-[22px] font-bold text-ink">FIR registered</h1>
            <p className="mt-1 text-[13px] text-muted">
              {done.crime_type} · {done.station}, {done.district} · {done.status}
            </p>
            <div className="mx-auto mt-5 grid max-w-md grid-cols-2 gap-3 text-left">
              <div className="rounded-xl bg-black/[0.03] p-3">
                <p className={label}>Crime No.</p>
                <p className="tabular font-mono text-[14px] font-bold text-ink">{done.crime_no}</p>
              </div>
              <div className="rounded-xl bg-black/[0.03] p-3">
                <p className={label}>Case No.</p>
                <p className="tabular font-mono text-[14px] font-bold text-ink">{done.case_no}</p>
              </div>
            </div>
            <p className="mt-4 text-[12px] text-faint">
              Severity {done.severity} · {done.linked_accused} accused linked to the offender registry
              {done.photos ? ` · ${done.photos} photograph${done.photos === 1 ? "" : "s"} attached` : ""} · registered {done.registered_at}
            </p>
            <div className="mt-6 flex justify-center gap-3">
              <Link prefetch={false} href={`/case?id=${encodeURIComponent(done.crime_no)}`} className="rounded-xl bg-brand px-4 py-2.5 text-[13px] font-semibold text-white">
                Open case file
              </Link>
              <button
                onClick={() => window.location.reload()}
                className="rounded-xl border border-black/10 px-4 py-2.5 text-[13px] font-semibold text-ink"
              >
                Register another
              </button>
            </div>
          </div>
        </motion.div>
      </div>
    );

  if (!idLoading && !canRegister)
    return (
      <div className="mx-auto max-w-xl px-4 py-16 text-center">
        <ShieldAlert className="mx-auto h-10 w-10 text-crimson" />
        <h1 className="mt-3 text-[20px] font-bold text-ink">FIR registration needs a station or district role</h1>
        <p className="mt-2 text-[13px] text-muted">
          Your current role ({identity?.role_label || "signed out"}) does not hold the <code>register_fir</code> permission. Sign in as an
          SI, SHO, DSP, SP, DGP or admin.
        </p>
        <Link prefetch={false} href="/" className="mt-5 inline-block rounded-xl bg-brand px-4 py-2.5 text-[13px] font-semibold text-white">
          Sign in
        </Link>
      </div>
    );

  if (loadErr)
    return (
      <div className="mx-auto max-w-xl px-4 py-16 text-center text-crimson">
        <AlertCircle className="mx-auto h-8 w-8" /> {loadErr}
      </div>
    );
  if (!m) return <div className="mx-auto mt-16 h-96 max-w-4xl animate-pulse rounded-3xl bg-black/[0.03]" />;

  const sel = (k: keyof typeof f, opts: { v: string; l: string }[], placeholder = "Select…") => (
    <select value={f[k]} onChange={set(k)} className={input}>
      <option value="">{placeholder}</option>
      {opts.map((o) => (
        <option key={o.v} value={o.v}>
          {o.l}
        </option>
      ))}
    </select>
  );
  const opts = (xs: Opt[]) => xs.map((x) => ({ v: String(x.id), l: x.name }));

  const personRow = (p: Person, onChange: (p: Person) => void, key: string, opts2?: { registry?: boolean; police?: boolean }) => (
    <div className="grid gap-3 sm:grid-cols-[minmax(0,2fr)_90px_minmax(0,1fr)]">
      <Field k={`${key}.name`} errors={errors} title="Name">
        <input
          value={p.name}
          onChange={(e) => onChange({ ...p, name: e.target.value })}
          className={input}
          readOnly={!!opts2?.registry && !!p.person_id}
          placeholder="Full name"
        />
      </Field>
      <Field k={`${key}.age`} errors={errors} title="Age">
        <input inputMode="numeric" value={p.age} onChange={(e) => onChange({ ...p, age: e.target.value.replace(/\D/g, "") })} className={input} />
      </Field>
      <Field k={`${key}.gender_id`} errors={errors} title="Gender">
        <select value={p.gender_id} onChange={(e) => onChange({ ...p, gender_id: e.target.value })} className={input}>
          <option value="">Select…</option>
          {m.genders.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
      </Field>
      {opts2?.police && (
        <label className="flex items-center gap-2 text-[12.5px] text-muted sm:col-span-3">
          <input type="checkbox" checked={!!p.is_police} onChange={(e) => onChange({ ...p, is_police: e.target.checked })} />
          Victim is a police officer on duty
        </label>
      )}
      <div className="sm:col-span-3">
        <span className={label}>Photograph · optional</span>
        <PhotoField
          who={key.startsWith("victims") ? "victim" : key.startsWith("accused") ? "accused" : "complainant"}
          value={p.photo}
          onChange={(photo) => onChange({ ...p, photo })}
          error={errors[`${key}.photo_id`]}
        />
        {key.startsWith("victims") && (
          <p className="mt-1 text-[11px] text-faint">Never photograph the victim of a sexual offence — the law bars disclosing their identity.</p>
        )}
      </div>
    </div>
  );

  const station = m.stations.find((s) => String(s.id) === f.station_id);
  const district = m.districts.find((d) => String(d.id) === f.district_id);

  return (
    <div className="mx-auto max-w-4xl px-4 pb-24 pt-8">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-brand/12 text-brand">
            <FilePlus2 className="h-5 w-5" />
          </span>
          <div>
            <h1 className="text-[20px] font-bold text-ink">Register FIR</h1>
            <p className="text-[12px] text-faint">CCTNS schema · every code checked against the master tables · status on registration: Under Investigation</p>
          </div>
        </div>
        <Link prefetch={false} href="/cases" className="text-[12.5px] text-muted hover:text-ink">
          ← Back to cases
        </Link>
      </div>

      <div className="mb-5 grid grid-cols-4 gap-2">
        {STEPS.map((s, i) => {
          const bad = Object.keys(errors).some((k) => k !== "_form" && stepOf(k) === i);
          return (
            <button
              key={s}
              onClick={() => setStep(i)}
              className={`rounded-xl border px-2 py-2 text-left text-[11.5px] transition-colors ${
                i === step ? "border-brand/50 bg-brand/10 text-ink" : "border-black/[0.08] text-muted hover:text-ink"
              }`}
            >
              <span className={`mr-1 font-bold tabular ${bad ? "text-crimson" : ""}`}>{i + 1}</span>
              <span className="hidden sm:inline">{s}</span>
            </button>
          );
        })}
      </div>

      <div className="bezel">
        <div className="bezel-core space-y-5 p-6">
          {step === 0 && (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field k="district_id" errors={errors} title="District">
                  <select value={f.district_id} onChange={(e) => setF((s) => ({ ...s, district_id: e.target.value, station_id: "" }))} className={input}>
                    <option value="">Select…</option>
                    {districts.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field k="station_id" errors={errors} title="Police station" hint={scope?.level === "station" ? "Limited to your station" : undefined}>
                  {sel("station_id", stations.map((s) => ({ v: String(s.id), l: s.name })))}
                </Field>
                <Field k="case_category_id" errors={errors} title="Case category">
                  {sel("case_category_id", opts(m.case_categories))}
                </Field>
                <Field k="officer_id" errors={errors} title="Investigating officer"
                       hint={ios.length ? "Suggested by specialisation, posting, closure rate and open cases — hover for the reasons" : "Officers of this station are listed first"}>
                  {sel("officer_id", officers.map((o) => ({ v: String(o.id), l: `${o.name} · ${o.rank} · ${o.kgid}` })))}
                  {ioChips}
                </Field>
                <Field k="incident_from" errors={errors} title="Incident from">
                  <input type="datetime-local" max={localNow()} value={f.incident_from} onChange={set("incident_from")} className={input} />
                </Field>
                <Field k="incident_to" errors={errors} title="Incident to" hint="Leave blank if a single moment">
                  <input type="datetime-local" max={localNow()} value={f.incident_to} onChange={set("incident_to")} className={input} />
                </Field>
                <Field k="info_received" errors={errors} title="Information received at PS">
                  <input type="datetime-local" max={localNow()} value={f.info_received} onChange={set("info_received")} className={input} />
                </Field>
                <Field k="court_id" errors={errors} title="Jurisdictional court" hint="Optional at registration">
                  {sel("court_id", opts(m.courts), "Not yet assigned")}
                </Field>
              </div>
              <Field k="place_of_occurrence" errors={errors} title="Place of occurrence">
                <input value={f.place_of_occurrence} onChange={set("place_of_occurrence")} className={input} placeholder="Street, landmark, distance and direction from PS" />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field k="latitude" errors={errors} title="Latitude" hint="Blank: the district headquarters">
                  <input inputMode="decimal" value={f.latitude} onChange={set("latitude")} className={input} placeholder="12.9352" />
                </Field>
                <Field k="longitude" errors={errors} title="Longitude">
                  <input inputMode="decimal" value={f.longitude} onChange={set("longitude")} className={input} placeholder="77.6245" />
                </Field>
              </div>
            </>
          )}

          {step === 1 && (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field k="crime_type_id" errors={errors} title="Crime type (sub-head)">
                  <select value={f.crime_type_id} onChange={(e) => { setSections([]); setF((s) => ({ ...s, crime_type_id: e.target.value, gravity_id: "" })); }} className={input}>
                    <option value="">Select…</option>
                    {m.crime_heads.map((h) => (
                      <optgroup key={h.id} label={h.name}>
                        {m.crime_types.filter((c) => c.head_id === h.id).map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.name}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </Field>
                <Field k="gravity_id" errors={errors} title="Gravity of offence">
                  {sel("gravity_id", opts(m.gravity))}
                </Field>
              </div>
              <Field k="sections" errors={errors} title="Acts & sections"
                     hint={`Offence on ${(f.incident_from || localNow()).slice(0, 10)}: the ${code === "BNS" ? "Bharatiya Nyaya Sanhita" : "Indian Penal Code"} applies`}>
                <div className="space-y-2">
                  {wrongCode.length > 0 && (
                    <p className="rounded-lg border border-brand/25 bg-brand/[0.06] px-3 py-2 text-[12px] text-ink">
                      {wrongCode.map((x) => `${x.act} ${x.section}`).join(", ")} {wrongCode.length > 1 ? "are" : "is"} not the code in force on the date of the offence — the
                      registration will be refused. {code === "BNS" ? `The BNS commenced on ${m.bns_commencement}.` : `The BNS only applies from ${m.bns_commencement}.`}
                    </p>
                  )}
                  {sections.map((s, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <select
                        value={`${s.act}|${s.section}`}
                        onChange={(e) => {
                          const [act, section] = e.target.value.split("|");
                          setSections((xs) => xs.map((x, j) => (j === i ? { act, section } : x)));
                        }}
                        className={input}
                      >
                        {m.acts.map((a) => (
                          <optgroup key={a.code} label={a.name}>
                            {m.sections.filter((x) => x.act === a.code).map((x) => (
                              <option key={`${x.act}|${x.section}`} value={`${x.act}|${x.section}`}>
                                {x.act} {x.section} — {x.description}
                              </option>
                            ))}
                          </optgroup>
                        ))}
                      </select>
                      <button aria-label="Remove section" onClick={() => setSections((xs) => xs.filter((_, j) => j !== i))} className="rounded-lg p-2 text-faint hover:text-crimson">
                        <Trash2 className="h-4 w-4" />
                      </button>
                      {errors[`sections.${i}`] && <span className="text-[11px] text-crimson">{errors[`sections.${i}`]}</span>}
                    </div>
                  ))}
                  <button
                    onClick={() => setSections((xs) => [...xs, { act: m.sections[0].act, section: m.sections[0].section }])}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-dashed border-black/15 px-3 py-1.5 text-[12px] text-muted hover:text-ink"
                  >
                    <Plus className="h-3.5 w-3.5" /> Add section
                  </button>
                </div>
              </Field>
              <div className="grid gap-4 sm:grid-cols-3">
                <Field k="weapon" errors={errors} title="Weapon used">
                  {sel("weapon", m.weapons.map((w) => ({ v: w, l: w })))}
                </Field>
                <Field k="vehicle" errors={errors} title="Vehicle used">
                  {sel("vehicle", m.vehicles.map((w) => ({ v: w, l: w })))}
                </Field>
                <Field k="target" errors={errors} title="Target type">
                  {sel("target", m.targets.map((w) => ({ v: w, l: w })))}
                </Field>
                {(["fatalities", "injured", "witnesses"] as const).map((k) => (
                  <Field key={k} k={k} errors={errors} title={k === "injured" ? "Injured" : k[0].toUpperCase() + k.slice(1)}>
                    <input inputMode="numeric" value={f[k]} onChange={(e) => setF((s) => ({ ...s, [k]: e.target.value.replace(/\D/g, "") }))} className={input} />
                  </Field>
                ))}
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <p className="rounded-xl border border-black/[0.06] bg-black/[0.02] px-3.5 py-2.5 text-[11.5px] leading-relaxed text-muted">
                Photographs are optional. Attach one only with the person&apos;s consent, or for an arrested accused under the Criminal Procedure
                (Identification) Act, 2022. Location and camera data are removed before upload, and the photograph is shown only inside the case file.
              </p>
              <section>
                <h3 className="mb-2 text-[13px] font-bold text-ink">Complainant / informant</h3>
                {personRow(complainant, setComplainant, "complainant")}
                <div className="mt-3 grid gap-3 sm:grid-cols-3">
                  {(
                    [
                      ["occupation_id", "Occupation", m.occupations],
                      ["religion_id", "Religion", m.religions],
                      ["caste_id", "Caste", m.castes],
                    ] as const
                  ).map(([k, t, xs]) => (
                    <Field key={k} k={`complainant.${k}`} errors={errors} title={t}>
                      <select value={(complainant as any)[k]} onChange={(e) => setComplainant((c) => ({ ...c, [k]: e.target.value }))} className={input}>
                        <option value="">Select…</option>
                        {xs.map((x) => (
                          <option key={x.id} value={x.id}>
                            {x.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                  ))}
                </div>
              </section>

              <section>
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="text-[13px] font-bold text-ink">Victims</h3>
                  <button
                    onClick={() => setVictims((v) => [...v, { name: "", age: "", gender_id: "" }])}
                    className="inline-flex items-center gap-1 text-[12px] text-brand"
                  >
                    <Plus className="h-3.5 w-3.5" /> Add victim
                  </button>
                </div>
                {errors.victims && <p className="mb-2 text-[11.5px] text-crimson">{errors.victims}</p>}
                <div className="space-y-3">
                  {victims.map((v, i) => (
                    <div key={i} className="relative rounded-xl border border-black/[0.06] bg-black/[0.015] p-3">
                      {victims.length > 1 && (
                        <button aria-label="Remove victim" onClick={() => setVictims((xs) => xs.filter((_, j) => j !== i))} className="absolute right-2 top-2 p-1 text-faint hover:text-crimson">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                      {personRow(v, (p) => setVictims((xs) => xs.map((x, j) => (j === i ? p : x))), `victims.${i}`, { police: true })}
                    </div>
                  ))}
                  <button
                    onClick={() => setVictims((xs) => [...xs, { ...complainant, person_id: undefined }])}
                    className="text-[11.5px] text-muted underline-offset-2 hover:underline"
                  >
                    Complainant is also a victim — copy details
                  </button>
                </div>
              </section>

              <section>
                <h3 className="mb-2 text-[13px] font-bold text-ink">Accused</h3>
                <p className="mb-2 text-[11.5px] text-faint">
                  Pick a person from the offender registry to link this FIR to their record and the network graph. A name typed in without a
                  registry match is recorded on the FIR but not linked to anyone.
                </p>
                {errors.accused && <p className="mb-2 text-[11.5px] text-crimson">{errors.accused}</p>}
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-faint" />
                  <input value={q} onChange={(e) => setQ(e.target.value)} className={`${input} pl-9`} placeholder="Search registry by name or SUS-id" />
                  {hits.length > 0 && (
                    <div className="absolute z-20 mt-1 w-full overflow-hidden rounded-xl border border-black/10 bg-white shadow-lg">
                      {hits.map((h) => (
                        <button
                          key={h.person_id}
                          onClick={() => {
                            setAccused((xs) => [...xs, { name: h.name, age: "", gender_id: "", person_id: h.person_id }]);
                            setQ("");
                            setHits([]);
                          }}
                          className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-[12.5px] hover:bg-black/[0.03]"
                        >
                          <span>
                            <b>{h.name}</b> <span className="text-faint">{h.person_id}{h.alias ? ` · ${h.alias}` : ""}</span>
                          </span>
                          <span className="flex gap-1 text-[10.5px]">
                            {h.rowdy_sheeter && <span className="rounded bg-crimson/10 px-1.5 text-crimson">rowdy</span>}
                            {h.active_warrant && <span className="rounded bg-brand/15 px-1.5 text-brand">warrant</span>}
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <button
                  onClick={() => setAccused((xs) => [...xs, { name: "", age: "", gender_id: "" }])}
                  className="mt-2 inline-flex items-center gap-1 text-[12px] text-brand"
                >
                  <Plus className="h-3.5 w-3.5" /> Add unidentified / new accused
                </button>
                <div className="mt-3 space-y-3">
                  {accused.map((a, i) => (
                    <div key={i} className="relative rounded-xl border border-black/[0.06] bg-black/[0.015] p-3">
                      <div className="mb-2 flex items-center gap-2 text-[11.5px]">
                        <UserRound className="h-3.5 w-3.5 text-faint" />
                        {a.person_id ? (
                          <span className="rounded bg-emerald/10 px-1.5 py-0.5 font-semibold text-emerald">Registry {a.person_id}</span>
                        ) : (
                          <span className="text-faint">Not linked to the registry</span>
                        )}
                        {errors[`accused.${i}.person_id`] && <span className="text-crimson">{errors[`accused.${i}.person_id`]}</span>}
                        <button aria-label="Remove accused" onClick={() => setAccused((xs) => xs.filter((_, j) => j !== i))} className="ml-auto p-1 text-faint hover:text-crimson">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      {personRow(a, (p) => setAccused((xs) => xs.map((x, j) => (j === i ? p : x))), `accused.${i}`, { registry: true })}
                    </div>
                  ))}
                  {!accused.length && <p className="text-[12px] text-faint">No accused named — the FIR will be registered against unknown persons.</p>}
                </div>
              </section>
            </>
          )}

          {step === 3 && (
            <>
              <Field k="brief_facts" errors={errors} title="Brief facts of the case" hint={`${f.brief_facts.trim().length} characters · at least 30`}>
                <textarea
                  value={f.brief_facts}
                  onChange={set("brief_facts")}
                  rows={7}
                  className={input}
                  placeholder="What happened, in the complainant's account: sequence of events, property involved, how the accused left…"
                />
              </Field>
              <div className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4 text-[12.5px]">
                <p className={label}>Review</p>
                <dl className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
                  {[
                    ["Station", station ? `${station.name}, ${district?.name}` : "—"],
                    ["Category", m.case_categories.find((x) => String(x.id) === f.case_category_id)?.name],
                    ["Crime type", crimeType?.name],
                    ["Gravity", m.gravity.find((x) => String(x.id) === f.gravity_id)?.name],
                    ["Sections", sections.map((s) => `${s.act} ${s.section}`).join(", ")],
                    ["Incident", f.incident_from ? f.incident_from.replace("T", " ") : "—"],
                    ["Complainant", complainant.name || "—"],
                    ["Victims", String(victims.length)],
                    ["Accused", accused.length ? `${accused.length} (${accused.filter((a) => a.person_id).length} from registry)` : "Unknown persons"],
                    ["Photographs", String([complainant, ...victims, ...accused].filter((x) => x.photo).length)],
                    ["Officer", officers.find((o) => String(o.id) === f.officer_id)?.name],
                  ].map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-3 border-b border-black/[0.04] py-1">
                      <dt className="text-faint">{k}</dt>
                      <dd className="text-right font-medium text-ink">{v || "—"}</dd>
                    </div>
                  ))}
                </dl>
                <p className="mt-3 text-[11.5px] text-faint">
                  The Crime No. and Case No. are issued by the server on registration: category, district, station, year and the next serial for that
                  station this year.
                </p>
              </div>
            </>
          )}

          {errors._form && (
            <div className="flex items-center gap-2 rounded-xl border border-crimson/20 bg-crimson/[0.05] p-3 text-[12.5px] text-crimson">
              <AlertCircle className="h-4 w-4" /> {errors._form}
            </div>
          )}

          <div className="flex items-center justify-between border-t border-black/[0.06] pt-4">
            <button
              disabled={step === 0}
              onClick={() => setStep((s) => s - 1)}
              className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[13px] text-muted disabled:opacity-40"
            >
              <ArrowLeft className="h-4 w-4" /> Back
            </button>
            {step < STEPS.length - 1 ? (
              <button onClick={() => setStep((s) => s + 1)} className="inline-flex items-center gap-1.5 rounded-xl bg-ink px-4 py-2.5 text-[13px] font-semibold text-white">
                Next <ArrowRight className="h-4 w-4" />
              </button>
            ) : (
              <button
                onClick={submit}
                disabled={submitting}
                className="inline-flex items-center gap-1.5 rounded-xl bg-brand px-4 py-2.5 text-[13px] font-semibold text-white disabled:opacity-60"
              >
                {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <FilePlus2 className="h-4 w-4" />} Register FIR
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
