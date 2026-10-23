"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import {
  Plus, Search, Pin, PinOff, Trash2, Pencil, Check, X, Send, Mic, Copy, CheckCheck, ArrowRight, Lock,
  FolderSearch, Users, MapPinned, Cctv, Brain, PanelLeftClose, PanelLeftOpen, ExternalLink, AlertTriangle,
} from "lucide-react";
import { apiFetch, getJSON, ApiError } from "@/lib/api";
import { RichText, TraceStrip } from "@/components/RichText";
import { SiriOrb } from "@/components/SiriVisual";

type Msg = {
  role: "user" | "assistant";
  content: string;
  ts: number;
  trace?: any[];
  mode?: string;
  suggestions?: { label: string; query: string }[];
  action?: string;
  params?: Record<string, string>;
  grounded?: boolean;
  interpreted_as?: string;
  /** Set on an answer that just arrived, so only it is typed out. */
  fresh?: boolean;
};
type Conv = { id: string; title: string; updated: number; pinned?: boolean; messages: number; preview?: string; origin?: string | null };

/** The page a chat with the floating assistant began on, as a short label. */
function originLabel(path: string) {
  const name = path.split("?")[0].replace(/^\/+/, "").split("/")[0] || "home";
  return name.charAt(0).toUpperCase() + name.slice(1);
}

const STARTERS = [
  { icon: MapPinned, title: "What's happening in Bengaluru right now?", hint: "Live brief: FIRs, spikes, cameras" },
  { icon: FolderSearch, title: "Who are the accused in FIR-20345 and do any have warrants?", hint: "Reads the full case file" },
  { icon: Users, title: "Is SUS-7403 connected to SUS-8286?", hint: "Traces the link graph" },
  { icon: Cctv, title: "Where was SUS-8286 last seen on CCTV?", hint: "Face-match sightings, 24 h" },
  { icon: Brain, title: "Which police station has the most chain snatching?", hint: "Ranks 140 stations" },
  { icon: AlertTriangle, title: "How many murders in Mysuru in 2024, and what about Belagavi?", hint: "Counts with follow-ups" },
];

const ACTION_LABEL: Record<string, string> = {
  open_case: "Open case dossier", open_suspect: "Open offender profile", show_heatmap: "Show on the map",
  show_network: "Open link graph", show_dispatch: "Open dispatch plan", show_analytics: "Open analytics",
  show_models: "Open model registry", recent_cases: "Open these cases", show_registry: "Open registry",
  show_surveillance: "Open CCTV command",
};

function hrefFor(action?: string, p: Record<string, string> = {}) {
  const qs = (o: Record<string, string | undefined>) => {
    const u = new URLSearchParams();
    Object.entries(o).forEach(([k, v]) => v && u.set(k, v));
    const s = u.toString();
    return s ? `?${s}` : "";
  };
  switch (action) {
    case "open_case": return p.case_id ? `/case?id=${encodeURIComponent(p.case_id)}` : null;
    case "open_suspect": return p.suspect_id ? `/suspect?id=${encodeURIComponent(p.suspect_id)}` : null;
    case "show_heatmap": return `/map${qs({ district: p.district, crime_type: p.crime_type, mode: p.mode })}`;
    case "show_network": return `/network${qs({ suspect_id: p.suspect_id, case_id: p.case_id, q: p.q })}`;
    case "show_dispatch": return `/dispatch${qs({ district: p.district })}`;
    case "show_analytics": case "show_models": return "/analytics";
    case "recent_cases": return `/cases${qs({ district: p.district, crime_type: p.crime_type, sort: p.sort })}`;
    case "show_registry": return `/intel${qs({ tab: p.kind || "criminals" })}`;
    case "show_surveillance": return `/surveillance${qs({ district: p.district })}`;
    default: return null;
  }
}

function groupOf(ts: number) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const days = Math.floor((new Date(today.toDateString()).getTime() - new Date(d.toDateString()).getTime()) / 86400000);
  return days <= 0 ? "Today" : days === 1 ? "Yesterday" : days < 7 ? "Previous 7 days" : "Older";
}

/* Reveal an answer progressively, like it is being written. */
function useTyped(text: string, on: boolean) {
  const [n, setN] = useState(on ? 0 : text.length);
  useEffect(() => {
    if (!on) { setN(text.length); return; }
    setN(0);
    const step = Math.max(3, Math.round(text.length / 90));
    const t = setInterval(() => setN((v) => (v + step >= text.length ? (clearInterval(t), text.length) : v + step)), 16);
    return () => clearInterval(t);
  }, [text, on]);
  return text.slice(0, n);
}

function AssistantMessage({ m, animate, onAsk, onOpen }: { m: Msg; animate: boolean; onAsk: (q: string) => void; onOpen: (id: string) => void }) {
  const typed = useTyped(m.content, animate);
  const [copied, setCopied] = useState(false);
  const href = hrefFor(m.action, m.params);
  const done = typed.length === m.content.length;
  return (
    <div className="flex gap-3.5">
      <div className="mt-0.5 shrink-0"><SiriOrb state="idle" size={30} /></div>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2 text-[12px] font-semibold text-ink">Kavya
          {m.interpreted_as && <span className="truncate font-normal text-faint">· understood as “{m.interpreted_as}”</span>}
        </div>
        {m.grounded === false && (
          <div className="mb-2 rounded-lg bg-crimson/10 px-2.5 py-1.5 text-[12px] text-crimson">Contains identifiers not found in the records — treat as unverified.</div>
        )}
        <div className="text-[14.5px] leading-[1.7] text-ink/90"><RichText text={typed} onEntity={onOpen} /></div>
        {done && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <TraceStrip trace={m.trace} mode={m.mode} />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {href && m.action !== "answer" && (
                <Link href={href} className="inline-flex items-center gap-1.5 rounded-full bg-ink px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-brand">
                  <ExternalLink className="h-3.5 w-3.5" /> {ACTION_LABEL[m.action || ""] || "Open"}
                </Link>
              )}
              <button onClick={() => { navigator.clipboard?.writeText(m.content); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
                      className="inline-flex items-center gap-1 rounded-full px-2.5 py-1.5 text-[12px] text-muted hover:bg-black/[0.05] hover:text-ink">
                {copied ? <CheckCheck className="h-3.5 w-3.5 text-emerald" /> : <Copy className="h-3.5 w-3.5" />} {copied ? "Copied" : "Copy"}
              </button>
            </div>
            {!!m.suggestions?.length && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {m.suggestions.slice(0, 3).map((s) => (
                  <button key={s.query} onClick={() => onAsk(s.query)}
                          className="group inline-flex items-center gap-1 rounded-full border border-brand/25 bg-brand/[0.06] px-3 py-1.5 text-[12px] font-medium text-brand-deep hover:border-brand/50">
                    {s.label} <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
                  </button>
                ))}
              </div>
            )}
          </motion.div>
        )}
      </div>
    </div>
  );
}

function CopilotInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [convs, setConvs] = useState<Conv[]>([]);
  const [active, setActive] = useState<string | null>(params.get("c"));
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [locked, setLocked] = useState(false);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editVal, setEditVal] = useState("");
  const [sidebar, setSidebar] = useState(true);
  const [lang, setLang] = useState<"en" | "kn">("en");
  const [listening, setListening] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const recRef = useRef<any>(null);

  const loadList = useCallback((query = "") =>
    getJSON(`/copilot/conversations${query ? `?q=${encodeURIComponent(query)}` : ""}`)
      .then((d) => { setConvs(d.conversations || []); setLocked(false); })
      .catch((e) => e instanceof ApiError && e.isForbidden && setLocked(true)), []);

  useEffect(() => { loadList(); }, [loadList]);
  // Chats with the floating assistant on other pages land here too; pick them up on return.
  useEffect(() => {
    const onFocus = () => loadList(q);
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [loadList, q]);
  useEffect(() => { const t = setTimeout(() => loadList(q), 250); return () => clearTimeout(t); }, [q, loadList]);

  useEffect(() => {
    if (!active) { setMsgs([]); return; }
    getJSON(`/copilot/conversations/${active}`).then((c) => setMsgs(c.messages || [])).catch(() => setActive(null));
  }, [active]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, busy]);

  const select = (id: string | null) => {
    setActive(id);
    router.replace(id ? `/copilot?c=${id}` : "/copilot");
  };

  const send = useCallback(async (text: string) => {
    const content = text.trim();
    if (!content || busy) return;
    setBusy(true);
    setInput("");
    let cid = active;
    try {
      if (!cid) {
        const r = await apiFetch("/copilot/conversations", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        cid = (await r.json()).id as string;
        setActive(cid);
        router.replace(`/copilot?c=${cid}`);
      }
      setMsgs((m) => [...m, { role: "user", content, ts: Date.now() / 1000 }]);
      const r = await apiFetch(`/copilot/conversations/${cid}/messages`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content, lang }),
      });
      const d = await r.json();
      setMsgs((m) => [...m, { ...d.message, fresh: true }]);
      setConvs((cs) => [d.conversation, ...cs.filter((c) => c.id !== d.conversation.id)]);
    } catch (e) {
      if (e instanceof ApiError && e.isForbidden) setLocked(true);
      setMsgs((m) => [...m, { role: "assistant", content: "I couldn't reach the intelligence core. Check the connection and try again.", ts: Date.now() / 1000 }]);
    } finally {
      setBusy(false);
      taRef.current?.focus();
    }
  }, [active, busy, lang, router]);

  const rename = async (id: string) => {
    await apiFetch(`/copilot/conversations/${id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: editVal }) }).catch(() => {});
    setEditing(null);
    loadList(q);
  };
  const pin = async (c: Conv) => {
    await apiFetch(`/copilot/conversations/${c.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pinned: !c.pinned }) }).catch(() => {});
    loadList(q);
  };
  const remove = async (id: string) => {
    if (!confirm("Delete this conversation? This cannot be undone.")) return;
    await apiFetch(`/copilot/conversations/${id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ delete: true }) }).catch(() => {});
    if (active === id) select(null);
    loadList(q);
  };

  const dictate = () => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) return;
    if (listening) { recRef.current?.stop(); return; }
    const r = new SR();
    r.lang = lang === "kn" ? "kn-IN" : "en-IN";
    r.interimResults = true;
    r.onresult = (e: any) => setInput(Array.from(e.results).map((x: any) => x[0].transcript).join(""));
    r.onend = () => setListening(false);
    recRef.current = r;
    setListening(true);
    r.start();
  };

  const openEntity = (id: string) => router.push(/^SUS-/i.test(id) ? `/suspect?id=${id.toUpperCase()}` : `/case?id=${id}`);

  const grouped = useMemo(() => {
    const g: Record<string, Conv[]> = {};
    // An empty thread is a "new investigation" that was never asked anything; only the
    // open one is worth listing.
    for (const c of convs) if (c.messages > 0 || c.id === active) (g[c.pinned ? "Pinned" : groupOf(c.updated)] ||= []).push(c);
    return ["Pinned", "Today", "Yesterday", "Previous 7 days", "Older"].filter((k) => g[k]).map((k) => [k, g[k]] as const);
  }, [convs, active]);

  if (locked)
    return (
      <div className="mx-auto mt-16 max-w-md rounded-3xl border border-black/10 bg-white p-8 text-center shadow-sm">
        <Lock className="mx-auto h-8 w-8 text-brand" />
        <h1 className="mt-3 text-xl font-bold text-ink">Copilot needs an investigator sign-in</h1>
        <p className="mt-1 text-[13.5px] text-muted">Conversations read case files and person records, and are saved to your account.</p>
        <Link href="/?next=%2Fcopilot" className="mt-4 inline-block rounded-full bg-ink px-5 py-2 text-[13px] font-semibold text-white">Sign in</Link>
      </div>
    );

  return (
    <div className="-mt-6 flex h-[calc(100vh-7.5rem)] min-h-[560px] overflow-hidden rounded-[1.6rem] border border-black/10 bg-white shadow-[0_20px_60px_-30px_rgba(17,20,32,0.3)]">
      {/* --------------------------------------------------------- sidebar */}
      <AnimatePresence initial={false}>
        {sidebar && (
          <motion.aside initial={{ width: 0, opacity: 0 }} animate={{ width: 290, opacity: 1 }} exit={{ width: 0, opacity: 0 }}
                        transition={{ duration: 0.25 }} className="flex shrink-0 flex-col overflow-hidden border-r border-black/[0.07] bg-[#f8f8fa]">
            <div className="p-3">
              <button onClick={() => select(null)} className="flex w-full items-center justify-center gap-2 rounded-xl bg-ink py-2.5 text-[13px] font-semibold text-white hover:bg-brand">
                <Plus className="h-4 w-4" /> New investigation
              </button>
              <div className="mt-2 flex items-center gap-2 rounded-xl border border-black/10 bg-white px-2.5 py-2">
                <Search className="h-3.5 w-3.5 text-faint" />
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search conversations" className="min-w-0 flex-1 bg-transparent text-[12.5px] focus:outline-none" />
              </div>
            </div>
            <nav className="flex-1 overflow-y-auto px-2 pb-3">
              {convs.length === 0 && <p className="px-3 py-6 text-center text-[12px] text-faint">{q ? "Nothing matches." : "Your conversations will appear here."}</p>}
              {grouped.map(([label, list]) => (
                <div key={label} className="mb-2">
                  <div className="px-2.5 pb-1 pt-2 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-faint">{label}</div>
                  {list.map((c) => (
                    <div key={c.id} className={`group relative rounded-xl ${active === c.id ? "bg-white shadow-sm ring-1 ring-black/[0.06]" : "hover:bg-black/[0.04]"}`}>
                      {editing === c.id ? (
                        <form onSubmit={(e) => { e.preventDefault(); rename(c.id); }} className="flex items-center gap-1 p-1.5">
                          <input autoFocus value={editVal} onChange={(e) => setEditVal(e.target.value)} className="min-w-0 flex-1 rounded-lg border border-brand/40 px-2 py-1 text-[12.5px]" />
                          <button className="text-emerald"><Check className="h-4 w-4" /></button>
                          <button type="button" onClick={() => setEditing(null)} className="text-faint"><X className="h-4 w-4" /></button>
                        </form>
                      ) : (
                        <button onClick={() => select(c.id)} className="block w-full px-2.5 py-2 pr-16 text-left">
                          <div className="flex items-center gap-1.5">
                            <span className="truncate text-[13px] font-medium text-ink">{c.title}</span>
                            {c.origin && (
                              <span title={`Started with the assistant on ${c.origin}`}
                                    className="shrink-0 rounded bg-black/[0.05] px-1 py-px text-[9.5px] font-semibold uppercase tracking-wider text-faint">
                                {originLabel(c.origin)}
                              </span>
                            )}
                          </div>
                          <div className="truncate text-[11px] text-faint">{c.preview?.replace(/\*\*/g, "") || "No answers yet"}</div>
                        </button>
                      )}
                      {editing !== c.id && (
                        <div className="absolute right-1.5 top-1/2 hidden -translate-y-1/2 items-center gap-0.5 group-hover:flex">
                          <button onClick={() => pin(c)} title={c.pinned ? "Unpin" : "Pin"} className="grid h-6 w-6 place-items-center rounded-md text-faint hover:bg-black/[0.06] hover:text-ink">{c.pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}</button>
                          <button onClick={() => { setEditing(c.id); setEditVal(c.title); }} title="Rename" className="grid h-6 w-6 place-items-center rounded-md text-faint hover:bg-black/[0.06] hover:text-ink"><Pencil className="h-3.5 w-3.5" /></button>
                          <button onClick={() => remove(c.id)} title="Delete" className="grid h-6 w-6 place-items-center rounded-md text-faint hover:bg-crimson/10 hover:text-crimson"><Trash2 className="h-3.5 w-3.5" /></button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ))}
            </nav>
            <div className="border-t border-black/[0.06] p-3 text-[10.5px] leading-relaxed text-faint">
              Saved to your account only. Answers come from IntelHub records; every case and person id is verified.
            </div>
          </motion.aside>
        )}
      </AnimatePresence>

      {/* --------------------------------------------------------- thread */}
      <section className="relative flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-black/[0.06] px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <button onClick={() => setSidebar((s) => !s)} className="grid h-8 w-8 place-items-center rounded-lg text-muted hover:bg-black/[0.05] hover:text-ink" title="Toggle conversations">
              {sidebar ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeftOpen className="h-4 w-4" />}
            </button>
            <div className="truncate text-[14px] font-semibold text-ink">{convs.find((c) => c.id === active)?.title || "New investigation"}</div>
          </div>
          <div className="flex items-center rounded-full bg-black/[0.04] p-0.5">
            {(["en", "kn"] as const).map((l) => (
              <button key={l} onClick={() => setLang(l)} className={`h-7 rounded-full px-2.5 text-[11.5px] font-semibold ${lang === l ? "bg-brand text-black" : "text-muted"}`}>{l === "en" ? "EN" : "ಕನ್ನಡ"}</button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {msgs.length === 0 ? (
            <div className="mx-auto flex max-w-3xl flex-col items-center px-6 pb-10 pt-14 text-center">
              <SiriOrb state={busy ? "thinking" : "idle"} size={64} />
              <h1 className="text-display mt-5 text-3xl font-bold tracking-tight text-ink">What are we investigating?</h1>
              <p className="mt-2 max-w-lg text-[14px] text-muted">
                Ask about any FIR, offender, officer, district, vehicle or camera. Kavya reads the records, traces links,
                checks the CCTV network and keeps the thread so you can follow up.
              </p>
              <div className="mt-8 grid w-full gap-2.5 sm:grid-cols-2">
                {STARTERS.map((s) => (
                  <button key={s.title} onClick={() => send(s.title)} className="group flex items-start gap-3 rounded-2xl border border-black/10 bg-white p-3.5 text-left transition-all hover:-translate-y-0.5 hover:border-brand/40 hover:shadow-md">
                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-brand/10 text-brand"><s.icon className="h-4 w-4" /></span>
                    <span>
                      <span className="block text-[13.5px] font-semibold text-ink">{s.title}</span>
                      <span className="block text-[11.5px] text-faint">{s.hint}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-7 px-6 py-8">
              {msgs.map((m, i) =>
                m.role === "user" ? (
                  <div key={i} className="flex justify-end">
                    <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-ink px-4 py-2.5 text-[14px] leading-relaxed text-white">{m.content}</div>
                  </div>
                ) : (
                  <AssistantMessage key={i} m={m} animate={!!m.fresh} onAsk={send} onOpen={openEntity} />
                )
              )}
              {busy && (
                <div className="flex items-center gap-3.5">
                  <SiriOrb state="thinking" size={30} />
                  <span className="text-[13px] text-muted">Reading the records…</span>
                </div>
              )}
              <div ref={endRef} />
            </div>
          )}
        </div>

        {/* --------------------------------------------------------- composer */}
        <div className="border-t border-black/[0.06] bg-white/90 px-4 py-3 backdrop-blur">
          <form onSubmit={(e) => { e.preventDefault(); send(input); }}
                className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-black/10 bg-white p-2 shadow-sm focus-within:border-brand/40">
            <textarea
              ref={taRef}
              value={input}
              onChange={(e) => { setInput(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${Math.min(180, e.target.scrollHeight)}px`; }}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
              rows={1}
              placeholder={lang === "kn" ? "ಕಾವ್ಯಾಳನ್ನು ಕೇಳಿ…" : "Ask Kavya — Enter to send, Shift+Enter for a new line"}
              className="max-h-[180px] min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[14px] text-ink placeholder:text-faint focus:outline-none"
            />
            <button type="button" onClick={dictate} title="Dictate" className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl transition-colors ${listening ? "bg-crimson text-white" : "text-muted hover:bg-black/[0.05] hover:text-ink"}`}>
              <Mic className="h-[18px] w-[18px]" />
            </button>
            <button disabled={!input.trim() || busy} className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-b from-brand-soft to-brand text-black disabled:opacity-40">
              <Send className="h-[17px] w-[17px]" />
            </button>
          </form>
          <p className="mx-auto mt-1.5 max-w-3xl text-center text-[10.5px] text-faint">Kavya can be wrong. A match, score or link is a lead — verify against the record before acting.</p>
        </div>
      </section>
    </div>
  );
}

export default function CopilotPage() {
  return (
    <Suspense fallback={null}>
      <CopilotInner />
    </Suspense>
  );
}
