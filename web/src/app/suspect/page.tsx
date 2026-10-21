"use client";

import Link from "next/link";
import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  ShieldAlert,
  Users,
  Crosshair,
  Layers,
  MapPin,
  Sword,
  ArrowRight,
  Activity,
  BrainCircuit,
  TrendingUp,
} from "lucide-react";
import { getJSON } from "@/lib/api";
import AccessRequired from "@/components/AccessRequired";
import { Cites, GeoProfileView, SourceTimeline } from "@/components/Investigation";

const RISK_STYLES: Record<string, string> = {
  Critical: "border-crimson/30 bg-crimson/10 text-crimson",
  High: "border-brand/30 bg-brand/10 text-brand",
  Medium: "border-brand/25 bg-brand/[0.07] text-brand-soft",
  Low: "border-emerald/25 bg-emerald/10 text-emerald",
};

function SuspectDossierContent() {
  const searchParams = useSearchParams();
  const id = searchParams.get("id");
  const [dossier, setDossier] = useState<any>(null);
  const [forbidden, setForbidden] = useState(false);
  const [loading, setLoading] = useState(true);
  const [fused, setFused] = useState<any>(null);

  useEffect(() => {
    if (!id) return;
    // One person across every register; the dossier stands without it.
    setFused(null);
    getJSON(`/investigate/person/${id}/timeline`).then(setFused).catch(() => setFused(null));
  }, [id]);

  useEffect(() => {
    if (!id) {
      setLoading(false);
      return;
    }
    getJSON(`/suspect/${id}`)
      .then(setDossier)
      .catch((e) => {
        setForbidden(Boolean(e?.isForbidden));
        setDossier(null);
      })
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return <SuspectSkeleton />;
  }

  if (forbidden) {
    return <AccessRequired what="An offender dossier" />;
  }

  if (!id || !dossier || dossier.error) {
    return (
      <div className="flex h-[60vh] flex-col items-center justify-center text-center">
        <AlertCircle className="mb-4 h-12 w-12 text-crimson" />
        <h1 className="text-display text-2xl font-bold text-ink">Suspect Not Found</h1>
        <p className="mt-2 text-muted">The intelligence core could not locate this suspect ID.</p>
        <Link prefetch={false}
          href="/network"
          className="mt-6 rounded-full border border-black/10 bg-black/[0.03] px-5 py-2.5 text-sm font-medium text-ink transition-all hover:border-brand/40"
        >
          Back to link analysis
        </Link>
      </div>
    );
  }

  const { suspect, linked_cases, mo_summary, centrality_rank } = dossier;
  const riskStyle = RISK_STYLES[suspect.risk_level] || "border-black/15 bg-black/[0.03] text-muted";

  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-8">
      {/* Header */}
      <div className="flex flex-col gap-4 border-b border-black/[0.06] pb-6 md:flex-row md:items-center">
        {suspect.photo_url && (
          <img
            src={suspect.photo_url}
            alt={suspect.name}
            className="h-20 w-20 rounded-full border border-black/10 object-cover bg-black/[0.05]"
          />
        )}
        <div className="flex-1">
          <Link prefetch={false} href="/network" className="mb-2 flex w-fit items-center gap-2 text-sm text-faint transition-colors hover:text-ink">
            <ArrowLeft className="h-4 w-4" /> Back to link analysis
          </Link>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-display text-4xl font-bold tracking-tight text-ink">
              {suspect.name}
              {suspect.alias && <span className="ml-2 font-normal text-muted text-2xl">({suspect.alias})</span>}
            </h1>
            <span className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-bold ${riskStyle}`}>
              <ShieldAlert className="h-4 w-4" /> {suspect.risk_level || "Unknown"} Risk
            </span>
            {centrality_rank && (
              <span className="flex items-center gap-2 rounded-lg border border-azure/30 bg-azure/10 px-3 py-1.5 text-xs font-bold text-azure">
                <Crosshair className="h-4 w-4" /> Network Rank #{centrality_rank}
              </span>
            )}
            {suspect.active_warrant && (
              <span className="rounded-lg border border-crimson/30 bg-crimson/10 px-3 py-1.5 text-xs font-bold text-crimson">
                Active Warrant
              </span>
            )}
            {suspect.rowdy_sheeter && (
              <span className="rounded-lg border border-brand/30 bg-brand/10 px-3 py-1.5 text-xs font-bold text-brand">
                Rowdy Sheeter
              </span>
            )}
            {suspect.history_sheet && (
              <span className="rounded-lg border border-brand-soft/30 bg-brand-soft/10 px-3 py-1.5 text-xs font-bold text-brand-soft">
                History Sheeted
              </span>
            )}
          </div>
          <p className="mt-2 font-mono text-lg text-muted">
            {suspect.suspect_id} · {suspect.gang_affiliation} {suspect.age && `· Age ${suspect.age}`} {suspect.fingerprint_id && `· FP: ${suspect.fingerprint_id}`}
          </p>
        </div>
      </div>

      {/* MO stat cards */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          { label: "Linked Cases", value: mo_summary.total_cases, icon: Layers, tone: "text-ink" },
          { label: "Open Cases", value: mo_summary.open_cases, icon: AlertCircle, tone: "text-brand" },
          { label: "Avg Severity", value: `${mo_summary.avg_severity}`, icon: Activity, tone: "text-crimson" },
          { label: "Active Districts", value: mo_summary.active_districts, icon: MapPin, tone: "text-emerald" },
        ].map((s) => (
          <div key={s.label} className="bezel">
            <div className="bezel-core p-5">
              <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-faint">
                <s.icon className="h-3.5 w-3.5" /> {s.label}
              </div>
              <p className={`text-display tabular mt-2 text-3xl font-bold ${s.tone}`}>{s.value}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Left Column: MO + ML Risk */}
        <div className="space-y-6">
          {/* MO breakdown */}
          <div className="bezel">
            <div className="bezel-core space-y-6 p-6">
              <h2 className="flex items-center gap-2 text-[15px] font-bold text-ink">
                <span className="grid h-9 w-9 place-items-center rounded-xl bg-azure/12 text-azure">
                  <Sword className="h-[18px] w-[18px]" />
                </span>
                Modus Operandi
              </h2>
              {[
                { title: "Crime Types", items: mo_summary.top_crime_types },
                { title: "Districts", items: mo_summary.top_districts },
                { title: "Weapons", items: mo_summary.top_weapons },
              ].map((block) => (
                <div key={block.title}>
                  <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">{block.title}</p>
                  {block.items?.length > 0 ? (
                    <div className="space-y-1.5">
                      {block.items.map((it: any) => (
                        <div key={it.label} className="flex items-center justify-between text-[13px]">
                          <span className="text-ink/85">{it.label}</span>
                          <span className="tabular rounded bg-black/[0.04] px-2 py-0.5 font-mono text-muted">{it.count}×</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-[13px] text-faint">None recorded</p>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* AI Conviction Prediction */}
          {dossier.ml_conviction_probability !== null && dossier.ml_conviction_probability !== undefined && (
            <div className="bezel">
              <div className="bezel-core space-y-5 p-6">
                <h2 className="flex items-center gap-2 text-[15px] font-bold text-ink">
                  <span className="grid h-9 w-9 place-items-center rounded-xl bg-brand/12 text-brand">
                    <Activity className="h-[18px] w-[18px]" />
                  </span>
                  AI Risk & Conviction Prediction
                </h2>
                
                <div className="space-y-4">
                  {/* A challenge that changed nothing about what the officer sees
                      would be a complaints box, not a control. The score is not
                      suppressed - adjudication is not this page's call - but it never
                      appears without the fact that it is disputed. */}
                  {dossier.ml_conviction_basis?.contested && (
                    <div className="flex items-start gap-2.5 rounded-xl border border-brand/30 bg-brand/[0.07] p-3.5">
                      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
                      <div>
                        <p className="text-[12px] font-bold text-ink">
                          This assessment has been formally challenged
                        </p>
                        <p className="mt-1 text-[11.5px] leading-relaxed text-muted">
                          A contest is open and under review. The score below is shown
                          unchanged &mdash; filing a challenge does not decide it &mdash;
                          but it must not be relied on without checking the outcome of
                          that review first.
                        </p>
                      </div>
                    </div>
                  )}

                  <div className="rounded-xl border border-black/[0.06] bg-black/[0.02] p-4 text-center">
                    <p className="text-[11.5px] font-bold uppercase tracking-wider text-faint">ML Conviction Likelihood</p>
                    <p className={`text-5xl font-black mt-2 leading-none ${
                      dossier.ml_conviction_basis?.contested
                        ? "text-faint"
                        : dossier.ml_conviction_probability > 75
                        ? "text-crimson"
                        : dossier.ml_conviction_probability > 45 
                        ? "text-brand" 
                        : "text-emerald"
                    }`}>
                      {dossier.ml_conviction_probability}%
                    </p>
                    <p className="text-[10px] leading-relaxed text-muted mt-2">
                      Gradient Boosting with Platt calibration.{" "}
                      {dossier.ml_conviction_basis?.case_id
                        ? `Scored on ${dossier.ml_conviction_basis.case_id}, the most recent case naming this person.`
                        : "Scored on the most recent case naming this person."}{" "}
                      Decision support only &mdash; not evidence.
                    </p>
                  </div>

                  {dossier.ml_feature_importances && (
                    <div className="space-y-2.5">
                      <p className="text-[11px] font-semibold uppercase tracking-wider text-faint">Model Feature Importance</p>
                      <div className="space-y-2">
                        {Object.entries(dossier.ml_feature_importances).map(([feat, imp]: [string, any]) => {
                          const displayNames: Record<string, string> = {
                            age: "Age Year",
                            gender: "Gender ID",
                            past_firs: "Past FIRs Count",
                            past_arrests: "Past Arrests Count",
                            rowdy_sheeter: "Rowdy Sheeter Flag",
                            history_sheet: "History Sheeted Flag",
                            active_warrant: "Active Warrant Flag"
                          };
                          const pct = Math.round(imp * 100);
                          return (
                            <div key={feat} className="space-y-1">
                              <div className="flex justify-between text-[10.5px]">
                                <span className="text-ink/80">{displayNames[feat] || feat}</span>
                                <span className="font-semibold text-muted">{pct}%</span>
                              </div>
                              <div className="h-1.5 w-full bg-black/[0.05] rounded-full overflow-hidden">
                                <div className="h-full bg-azure rounded-full" style={{ width: `${pct}%` }} />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* AI Behavioral Specialization & Temporal Correlation */}
          {dossier.correlation_report && (
            <div className="bezel animate-fade-in">
              <div className="bezel-core space-y-5 p-6">
                <h2 className="flex items-center gap-2 text-[15px] font-bold text-ink">
                  <span className="grid h-9 w-9 place-items-center rounded-xl bg-azure/12 text-azure">
                    <BrainCircuit className="h-[18px] w-[18px]" />
                  </span>
                  AI Behavioral Correlation
                </h2>
                
                <div className="space-y-4">
                  {/* Specialization Index */}
                  {dossier.correlation_report.specialization && (
                    <div className="rounded-xl border border-black/[0.06] bg-black/[0.02] p-4">
                      <p className="text-[10px] font-bold uppercase tracking-wider text-faint mb-1">Criminological Profile</p>
                      <p className="text-[14px] font-bold text-ink">{dossier.correlation_report.specialization.level}</p>
                      <p className="text-[12px] text-muted mt-1">
                        Concentrates <span className="font-semibold text-brand">{dossier.correlation_report.specialization.percentage}%</span> of activities on <span className="font-semibold text-ink">{dossier.correlation_report.specialization.primary_crime}</span>.
                      </p>
                    </div>
                  )}

                  {/* Temporal Overlap */}
                  {dossier.correlation_report.temporal_similarity !== undefined && (
                    <div className="space-y-1.5">
                      <div className="flex justify-between text-[11px] font-semibold uppercase tracking-wider text-faint">
                        <span>Temporal Pattern Overlap</span>
                        <span className="text-azure">{Math.round(dossier.correlation_report.temporal_similarity * 100)}%</span>
                      </div>
                      <div className="h-2 w-full bg-black/[0.05] rounded-full overflow-hidden">
                        <div className="h-full bg-azure rounded-full" style={{ width: `${dossier.correlation_report.temporal_similarity * 100}%` }} />
                      </div>
                      <p className="text-[10.5px] text-muted leading-relaxed">
                        Correlates to state-wide peak crime hours. Temporal Deviance rate is <span className="font-semibold text-ink">{dossier.correlation_report.temporal_deviance_pct}%</span>.
                      </p>
                    </div>
                  )}

                  {/* Severity Deviance Z-Score */}
                  {dossier.correlation_report.severity_deviance_z !== undefined && (
                    <div className="rounded-xl border border-black/[0.06] bg-black/[0.02] p-3 text-[12px] flex justify-between items-center">
                      <span className="text-muted">Avg Crime Severity Deviance</span>
                      <span className={`font-mono font-bold px-2 py-0.5 rounded text-[11.5px] ${
                        dossier.correlation_report.severity_deviance_z > 0.8
                          ? "bg-crimson/10 text-crimson" 
                          : dossier.correlation_report.severity_deviance_z < -0.8
                          ? "bg-emerald/10 text-emerald" 
                          : "bg-black/[0.05] text-ink"
                      }`}>
                        {dossier.correlation_report.severity_deviance_z > 0 ? "+" : ""}{dossier.correlation_report.severity_deviance_z}σ
                      </span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Right Column: Socio-Economic & Co-Offenders Grid + Linked Cases */}
        <div className="space-y-6 lg:col-span-2">
          {/* Socio-Economic & Co-Offender ties grid */}
          {dossier.correlation_report && (
            <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
              {/* Socio-Economic Profile */}
              {dossier.correlation_report.socio_profile?.suspect_weighted && (
                <div className="bezel">
                  <div className="bezel-core p-6 space-y-4">
                    <h2 className="flex items-center gap-2 text-[15px] font-bold text-ink">
                      <span className="grid h-9 w-9 place-items-center rounded-xl bg-emerald/12 text-emerald">
                        <TrendingUp className="h-[18px] w-[18px]" />
                      </span>
                      Sociological Driver Profile
                    </h2>
                    
                    <div className="space-y-3">
                      {[
                        { label: "Unemployment", key: "unemployment", unit: "%" },
                        { label: "Urbanization", key: "urbanization", unit: "%" },
                        { label: "Literacy Rate", key: "literacy", unit: "%" },
                        { label: "Per-Capita Income", key: "per_capita_income_k", unit: "k" },
                      ].map((metric) => {
                        const susVal = dossier.correlation_report.socio_profile.suspect_weighted[metric.key];
                        const stateVal = dossier.correlation_report.socio_profile.state_average[metric.key];
                        const diff = susVal - stateVal;
                        
                        return (
                          <div key={metric.key} className="space-y-1">
                            <div className="flex justify-between text-[11.5px] text-ink/80">
                              <span>{metric.label}</span>
                              <span className="font-mono text-[11.5px]">
                                {susVal}{metric.unit} <span className="text-[10px] text-muted">(state: {stateVal}{metric.unit})</span>
                              </span>
                            </div>
                            <div className="flex items-center gap-2">
                              <div className="h-1.5 flex-1 bg-black/[0.05] rounded-full overflow-hidden">
                                <div className="h-full bg-emerald rounded-full" style={{ width: `${Math.min(100, (susVal / Math.max(1, stateVal)) * 50)}%` }} />
                              </div>
                              <span className={`text-[10.5px] font-semibold tabular ${diff >= 0 ? "text-crimson" : "text-emerald"}`}>
                                {diff >= 0 ? "+" : ""}{diff.toFixed(1)}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    
                    {dossier.correlation_report.socio_profile.insights?.length > 0 && (
                      <div className="rounded-lg bg-black/[0.02] border border-black/[0.04] p-3 text-[11px] text-muted space-y-1">
                        {dossier.correlation_report.socio_profile.insights.map((insight: string, idx: number) => (
                          <p key={idx}>• {insight}</p>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Co-Offender Ties */}
              <div className="bezel">
                <div className="bezel-core p-6 space-y-4">
                  <h2 className="flex items-center gap-2 text-[15px] font-bold text-ink">
                    <span className="grid h-9 w-9 place-items-center rounded-xl bg-crimson/12 text-crimson">
                      <Users className="h-[18px] w-[18px]" />
                    </span>
                    Co-Offender Network Ties
                  </h2>
                  
                  <div className="space-y-2.5">
                    {dossier.correlation_report.co_offenders && dossier.correlation_report.co_offenders.length > 0 ? (
                      dossier.correlation_report.co_offenders.map((co: any) => (
                        <Link prefetch={false}
                          key={co.id}
                          href={`/suspect?id=${co.id}`}
                          className="flex items-center justify-between gap-3 rounded-lg border border-black/[0.04] bg-black/[0.01] p-3 transition-all hover:border-brand/40"
                        >
                          <div className="min-w-0">
                            <p className="text-[13px] font-semibold text-ink hover:text-brand transition-colors truncate">{co.name}</p>
                            <p className="text-[11px] text-faint truncate">Gang: {co.gang_affiliation}</p>
                          </div>
                          <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold shrink-0 ${RISK_STYLES[co.risk_level] || "bg-black/10 text-muted"}`}>
                            {co.risk_level}
                          </span>
                        </Link>
                      ))
                    ) : (
                      <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-black/[0.08] bg-black/[0.005]">
                        <p className="text-[12px] text-faint">No co-offenders registered for this suspect.</p>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Linked cases */}
          <div className="bezel">
            <div className="bezel-core p-6">
              <h2 className="mb-5 flex items-center gap-2 text-[15px] font-bold text-ink">
                <span className="grid h-9 w-9 place-items-center rounded-xl bg-crimson/12 text-crimson">
                  <Layers className="h-[18px] w-[18px]" />
                </span>
                Linked Cases ({linked_cases.length})
              </h2>
              {linked_cases.length > 0 ? (
                <div className="space-y-2.5">
                  {linked_cases.map((c: any) => (
                    <Link prefetch={false}
                      key={c.case_id}
                      href={`/case?id=${c.case_id}`}
                      className="group flex items-center justify-between gap-4 rounded-xl border border-black/[0.06] bg-black/[0.02] p-4 transition-all hover:border-brand/40"
                    >
                      <div className="min-w-0">
                        <div className="mb-1 flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[12px] font-bold text-azure">{c.case_id}</span>
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                              c.status === "Closed" ? "bg-emerald/10 text-emerald" : "bg-brand/10 text-brand"
                            }`}
                          >
                            {c.status}
                          </span>
                          <span className="rounded bg-black/[0.05] px-1.5 py-0.5 text-[10px] text-muted">{c.relationship}</span>
                        </div>
                        <p className="truncate font-semibold text-ink">{c.crime_type}</p>
                        <p className="text-[13px] text-faint">
                          {c.district} · {c.time_of_day}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-3">
                        <span className={`text-[15px] font-bold ${c.severity > 70 ? "text-crimson" : "text-emerald"}`}>{c.severity}</span>
                        <ArrowRight className="h-4 w-4 text-faint transition-all group-hover:translate-x-1 group-hover:text-brand" />
                      </div>
                    </Link>
                  ))}
                </div>
              ) : (
                <div className="flex h-32 items-center justify-center rounded-xl border border-dashed border-black/[0.08]">
                  <p className="text-[13px] text-faint">No cases linked to this suspect.</p>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
      {fused && (
        <div className="bezel">
          <div className="bezel-core p-6">
            <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2 className="text-[15px] font-bold text-ink">Across every register</h2>
                <p className="text-[12px] text-faint">
                  {fused.summary.firs} FIRs at {fused.summary.stations} stations in {fused.summary.districts.length} districts ·
                  sources: {fused.sources.join(", ")}
                </p>
              </div>
            </div>
            {fused.identity_conflicts.length > 0 && (
              <div className="mb-5 space-y-2">
                {fused.identity_conflicts.map((c: any, i: number) => (
                  <div key={i} className="rounded-xl border border-crimson/25 bg-crimson/[0.05] p-3">
                    <p className="text-[12.5px] font-semibold text-crimson">
                      {c.code === "shared_fingerprint" ? "Identity conflict: one fingerprint, two records" : "Identity conflict: ages that are not one person's"}
                    </p>
                    <p className="mt-0.5 text-[12.5px] text-ink">{c.detail}</p>
                    <div className="mt-1.5"><Cites cites={c.cites} /></div>
                  </div>
                ))}
              </div>
            )}
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
              <div className="max-h-[520px] overflow-y-auto pr-1">
                <SourceTimeline events={fused.events} />
              </div>
              <div>
                <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wider text-faint">Geographic profile</h3>
                <GeoProfileView g={fused.geoprofile} />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SuspectSkeleton() {
  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-8 animate-pulse">
      {/* Header */}
      <div className="flex flex-col gap-4 border-b border-black/[0.06] pb-6 md:flex-row md:items-center">
        <div className="h-20 w-20 rounded-full bg-black/[0.06]" />
        <div className="flex-1 space-y-3">
          <div className="h-4 w-28 bg-black/[0.05] rounded-full" />
          <div className="flex flex-wrap items-center gap-3">
            <div className="h-10 w-64 bg-black/[0.08] rounded-lg" />
            <div className="h-6 w-24 bg-black/[0.05] rounded-full" />
            <div className="h-6 w-28 bg-black/[0.05] rounded-full" />
          </div>
          <div className="h-5 w-80 bg-black/[0.04] rounded-full" />
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {[1, 2, 3, 4].map((idx) => (
          <div key={idx} className="bezel">
            <div className="bezel-core p-5 space-y-2">
              <div className="h-3 w-24 bg-black/[0.05] rounded-full" />
              <div className="h-8 w-16 bg-black/[0.08] rounded-lg" />
            </div>
          </div>
        ))}
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Left Column */}
        <div className="space-y-6">
          <div className="bezel">
            <div className="bezel-core p-6 space-y-5">
              <div className="h-5 w-36 bg-black/[0.08] rounded-lg" />
              <div className="space-y-4">
                {[1, 2, 3].map((idx) => (
                  <div key={idx} className="space-y-2">
                    <div className="h-3 w-20 bg-black/[0.05] rounded-full" />
                    <div className="h-3.5 w-full bg-black/[0.04] rounded-full" />
                    <div className="h-3.5 w-5/6 bg-black/[0.04] rounded-full" />
                  </div>
                ))}
              </div>
            </div>
          </div>
          
          <div className="bezel">
            <div className="bezel-core p-6 space-y-4">
              <div className="h-5 w-40 bg-black/[0.08] rounded-lg" />
              <div className="h-20 w-full bg-black/[0.04] rounded-xl" />
              <div className="h-16 w-full bg-black/[0.03] rounded-xl" />
            </div>
          </div>
        </div>

        {/* Right Column */}
        <div className="space-y-6 lg:col-span-2">
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            {[1, 2].map((idx) => (
              <div key={idx} className="bezel">
                <div className="bezel-core p-6 space-y-4">
                  <div className="h-5 w-36 bg-black/[0.08] rounded-lg" />
                  <div className="space-y-2">
                    <div className="h-3 w-full bg-black/[0.04] rounded-full" />
                    <div className="h-3 w-5/6 bg-black/[0.04] rounded-full" />
                    <div className="h-3 w-4/5 bg-black/[0.04] rounded-full" />
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="bezel">
            <div className="bezel-core p-6 space-y-4">
              <div className="h-5 w-44 bg-black/[0.08] rounded-lg" />
              <div className="space-y-3">
                {[1, 2, 3].map((idx) => (
                  <div key={idx} className="h-14 w-full bg-black/[0.03] rounded-xl" />
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function SuspectDossier() {
  return (
    <Suspense fallback={<SuspectSkeleton />}>
      <SuspectDossierContent />
    </Suspense>
  );
}
