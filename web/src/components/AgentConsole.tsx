"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useRouter, usePathname } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { Mic, MicOff, Send, X, ArrowRight, Volume2, VolumeX, RotateCcw, ChevronDown, Pause, Play, Square, MessagesSquare } from "lucide-react";
import { askAgent, AgentResult, AgentSuggestion, AgentTraceStep, ChatTurn, ApiError } from "@/lib/api";
import { SiriWave, SiriOrb, EdgeGlow, useMicLevel, VoiceState } from "./SiriVisual";
import { RichText, TraceStrip } from "./RichText";

type Lang = { code: string; label: string; native: string };
const LANGS: Lang[] = [
  { code: "en-IN", label: "EN", native: "English" },
  { code: "kn-IN", label: "ಕ", native: "ಕನ್ನಡ" },
];

const SUGGESTIONS = [
  "Who are the accused in FIR-20345?",
  "How many murders in Mysuru in 2024?",
  "Which station has the most chain snatching?",
  "Is SUS-7403 connected to SUS-8286?",
  "Which crimes are spiking?",
  "What is this platform?",
];

// Format text for realistic human voice synthesis with natural phonetics & pacing.
// Strips long numeric case IDs so the AI doesn't robotically read "three zero zero zero three…"
function speakable(t: string) {
  return t
    .replace(/[*_`#>|]+/g, " ")
    .replace(/₹/g, " rupees ")
    // Strip long numeric case/FIR IDs (8+ digits) — replace with "this case" or just remove
    .replace(/\b\d{8,}\b/g, "this case")
    // Handle formatted case IDs like FIR-300030013202600004 or Case 300030013202600004
    .replace(/FIR-0*(\d{8,})/gi, "this F I R")
    .replace(/case\s+0*(\d{8,})/gi, "this case")
    // Short FIR numbers are fine to read
    .replace(/FIR-0*(\d{1,7})/gi, " F I R $1 ")
    .replace(/SUS-0*(\d+)/gi, " Suspect $1 ")
    .replace(/KSP/gi, " K S P ")
    .replace(/SCRB/gi, " S C R B ")
    .replace(/MO\b/gi, " Modus Operandi ")
    .replace(/(\d+)%/g, " $1 percent ")
    .replace(/:\s*/g, "... ")
    .replace(/;\s*/g, ", ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 400);
}

function getHumanVoice(langCode: string, preferredVoiceName?: string): SpeechSynthesisVoice | null {
  if (typeof window === "undefined" || !window.speechSynthesis) return null;
  const voices = window.speechSynthesis.getVoices();
  if (!voices || voices.length === 0) return null;

  if (preferredVoiceName) {
    const userChoice = voices.find((v) => v.name === preferredVoiceName);
    if (userChoice) return userChoice;
  }

  const targetLang = (langCode || "en-IN").split("-")[0].toLowerCase(); // 'en', 'hi', 'kn'

  // Known Female Voice identifiers across Windows, Mac, iOS, Android, Chrome, Edge
  const femaleVoiceNames = [
    "aria", "jenny", "zira", "heera", "neerja", "samantha", "siri",
    "karen", "victoria", "fiona", "moira", "tessa", "veena", "sunhi",
    "xiaoxiao", "google uk english female", "google us english", "google हिन्दी",
    "female", "girl", "woman", "natural female"
  ];
  const maleVoiceNames = ["david", "mark", "george", "ravi", "prabhat", "guy", "male", "boy", "man"];

  const isFemaleVoice = (v: SpeechSynthesisVoice) => {
    const name = v.name.toLowerCase();
    if (femaleVoiceNames.some((k) => name.includes(k))) return true;
    if (maleVoiceNames.some((k) => name.includes(k))) return false;
    return !name.includes("david") && !name.includes("mark");
  };

  // 1. Priority 1: Neural/Natural FEMALE voice matching language
  const neuralFemaleLang = voices.find((v) => {
    const l = v.lang.toLowerCase();
    const n = v.name.toLowerCase();
    const isLang = l.startsWith(targetLang) || (targetLang === "en" && l.startsWith("en"));
    const isNeural = n.includes("natural") || n.includes("neural") || n.includes("online") || n.includes("enhanced");
    return isLang && isFemaleVoice(v) && isNeural;
  });
  if (neuralFemaleLang) return neuralFemaleLang;

  // 2. Priority 2: ANY Neural/Natural FEMALE voice on the system
  const neuralFemaleGlobal = voices.find((v) => {
    const n = v.name.toLowerCase();
    const isNeural = n.includes("natural") || n.includes("neural") || n.includes("online") || n.includes("enhanced");
    return isFemaleVoice(v) && isNeural;
  });
  if (neuralFemaleGlobal) return neuralFemaleGlobal;

  // 3. Priority 3: ANY FEMALE voice matching target language (e.g. Zira, Heera, Google Female)
  const femaleLang = voices.find((v) => {
    const l = v.lang.toLowerCase();
    return (l.startsWith(targetLang) || (targetLang === "en" && l.startsWith("en"))) && isFemaleVoice(v);
  });
  if (femaleLang) return femaleLang;

  // 4. Priority 4: ANY FEMALE voice on the system (e.g. Zira, Samantha, Siri, Google Female, Heera)
  const anyFemaleSystem = voices.find((v) => isFemaleVoice(v));
  if (anyFemaleSystem) return anyFemaleSystem;

  // 5. Absolute Fallback: Non-David voice
  const nonDavid = voices.find((v) => !v.name.toLowerCase().includes("david"));
  if (nonDavid) return nonDavid;

  return voices[0] || null;
}

const THINKING_STEPS = ["Resolving names and case numbers…", "Reading the case files…", "Cross-checking the link graph…", "Verifying every identifier…"];

function ThinkingLine({ dark }: { dark?: boolean }) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((v) => (v + 1) % THINKING_STEPS.length), 900);
    return () => clearInterval(t);
  }, []);
  return (
    <AnimatePresence mode="wait">
      <motion.span
        key={i}
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        transition={{ duration: 0.25 }}
        className={`text-[12.5px] ${dark ? "text-white/60" : "text-muted"}`}
      >
        {THINKING_STEPS[i]}
      </motion.span>
    </AnimatePresence>
  );
}

type Turn = { id: number; role: "user" | "assistant"; text: string; trace?: AgentTraceStep[]; mode?: string };

// Why the mic is paused. Kept apart from a plain boolean so that coming back to the
// tab only undoes a pause the tab caused — never one the officer chose.
type PauseReason = "manual" | "hidden" | null;

// Push-to-talk lifecycle. "starting" = start() called, onstart not yet seen;
// "live" = capturing; "ending" = released, waiting for the final result + onend.
type PttPhase = "idle" | "starting" | "live" | "ending";

// The mute choice survives reloads. It is a per-viewer convenience (which device is
// in a quiet room), so browser storage is the right home for it, and every access is
// guarded: private windows and blocked storage throw.
const TTS_PREF_KEY = "kavya.voiceReplies";

// Holding the dock orb this long turns a tap (toggle hands-free) into push-to-talk.
// Short enough to feel immediate, long enough that a quick tap is never mistaken.
const PTT_HOLD_MS = 320;

// After the officer cuts Kavya off, cancel() silences the speaker at once, so the long
// 1 s post-speech tail the natural end-of-speech path waits out is not needed. The
// mic comes back after this shorter gap, and the existing 1.2 s echo window is
// back-dated to end at the same moment, so nothing said after the gap is discarded.
const INTERRUPT_RESUME_MS = 450;
const ECHO_WINDOW_MS = 1200;

// Space is the push-to-talk key only when it would not otherwise do something:
// typing, pressing a focused button, toggling a checkbox, moving a slider.
function isInteractiveTarget(el: EventTarget | null): boolean {
  if (!(el instanceof Element)) return false;
  return !!el.closest(
    'input, textarea, select, button, a[href], summary, [contenteditable=""], [contenteditable="true"], ' +
      '[role="button"], [role="textbox"], [role="slider"], [role="checkbox"], [role="switch"], [role="tab"], [role="menuitem"], [role="option"]'
  );
}

export default function AgentConsole() {
  const router = useRouter();
  const pathname = usePathname() || "/";

  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [lang, setLang] = useState<Lang>(LANGS[0]);
  const [reply, setReply] = useState<{ text: string; action: string; trace?: AgentTraceStep[]; mode?: string } | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [panelOpen, setPanelOpen] = useState(true);
  const turnId = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Voice amplitude shared by the mic analyser, the TTS word pulses and the wave.
  const levelRef = useRef(0);
  const [suggestions, setSuggestions] = useState<AgentSuggestion[]>([]);
  const [speechOK, setSpeechOK] = useState(false);

  // Voice-mode state & Realistic Neural TTS
  const [voiceMode, setVoiceMode] = useState(false);
  const [tts, setTts] = useState(true);
  const [speaking, setSpeaking] = useState(false);
  const [interim, setInterim] = useState("");
  const [lastCommand, setLastCommand] = useState("");
  const [voiceNote, setVoiceNote] = useState("");
  const [availableVoices, setAvailableVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [selectedVoiceName, setSelectedVoiceName] = useState<string>("");

  const inputRef = useRef<HTMLInputElement>(null);
  const recogRef = useRef<any>(null);
  const historyRef = useRef<ChatTurn[]>([]);
  // The Copilot conversation these turns are saved to, so the chat continues there.
  const [conversationId, setConversationId] = useState<string | null>(null);
  const conversationRef = useRef<string | null>(null);
  // Wraps every piece of assistant UI (voice HUD, conversation, push-to-talk pill,
  // dock, hint line) so a pointerdown anywhere else can be read as "dismiss".
  const rootRef = useRef<HTMLDivElement>(null);

  // Mic pause (voice mode stays on; recognition is stopped and NOT auto-restarted).
  const [pauseReason, setPauseReasonState] = useState<PauseReason>(null);
  const pauseRef = useRef<PauseReason>(null);
  const micPaused = pauseReason !== null;
  // Set when WE stop the recogniser (pause), so its onend is not mistaken for the
  // mic dying instantly — that streak counter turns voice off after five.
  const deliberateStopRef = useRef(false);

  // Push-to-talk: a one-shot capture on the same recognition instance, sent on release.
  const [ptt, setPtt] = useState(false);
  const pttPhaseRef = useRef<PttPhase>("idle");
  const pttSourceRef = useRef<"pointer" | "key" | null>(null);
  const pttFinalRef = useRef("");
  const pttInterimRef = useRef("");
  const pttEndTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const holdTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const suppressClickRef = useRef(false);
  const [hint, setHint] = useState("");
  const hintTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Set when the officer dismisses Kavya while an answer is still in flight, so that
  // answer is shown when it lands but not read aloud into a room they walked away from.
  const suppressSpeechRef = useRef(false);

  // Load available SpeechSynthesis voices dynamically and bind best female voice
  useEffect(() => {
    if (typeof window === "undefined" || !window.speechSynthesis) return;
    const updateVoices = () => {
      const vs = window.speechSynthesis.getVoices();
      setAvailableVoices(vs);
      if (vs.length > 0) {
        const prefix = (lang.code || "en").split("-")[0].toLowerCase();
        const currentVoice = vs.find((v) => v.name === selectedVoiceNameRef.current);
        const matchesLang = currentVoice && currentVoice.lang.toLowerCase().startsWith(prefix);
        
        if (!selectedVoiceNameRef.current || !matchesLang) {
          const female = getHumanVoice(lang.code);
          if (female) {
            setSelectedVoiceName(female.name);
          }
        }
      }
    };
    updateVoices();
    window.speechSynthesis.onvoiceschanged = updateVoices;
    return () => {
      if (typeof window !== "undefined" && window.speechSynthesis) {
        window.speechSynthesis.onvoiceschanged = null;
      }
    };
  }, [lang.code]);

  // Refs mirror state so the long-lived recognition/TTS callbacks never read stale values.
  const pathRef = useRef(pathname);
  pathRef.current = pathname;
  const langRef = useRef(lang);
  langRef.current = lang;
  const voiceRef = useRef(false);
  const ttsRef = useRef(true);
  ttsRef.current = tts;
  const busyRef = useRef(false);
  busyRef.current = busy;
  const selectedVoiceNameRef = useRef(selectedVoiceName);
  selectedVoiceNameRef.current = selectedVoiceName;
  const speakingRef = useRef(false);
  // Timestamp when TTS finished — used to create a cooldown window where mic
  // results are discarded so the AI never hears its own voice from the speakers.
  const ttsEndedAt = useRef(0);

  const setPauseReason = useCallback((r: PauseReason) => {
    pauseRef.current = r;
    setPauseReasonState(r);
  }, []);

  const flashHint = useCallback((text: string) => {
    clearTimeout(hintTimer.current);
    setHint(text);
    hintTimer.current = setTimeout(() => setHint(""), 2800);
  }, []);

  // Restore the persisted mute choice after mount (storage is not readable during
  // the static-export prerender, so it cannot seed useState directly).
  useEffect(() => {
    try {
      if (window.localStorage.getItem(TTS_PREF_KEY) === "off") {
        ttsRef.current = false;
        setTts(false);
      }
    } catch {
      /* storage blocked — replies stay on, which is the default */
    }
  }, []);

  useEffect(() => {
    const SR =
      (typeof window !== "undefined" &&
        ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)) ||
      null;
    setSpeechOK(!!SR);
  }, []);

  const route = useCallback(
    (r: AgentResult) => {
      const p = r.params || {};
      const qs = new URLSearchParams();
      switch (r.action) {
        case "open_case":
          if (p.case_id) router.push(`/case?id=${encodeURIComponent(p.case_id)}`);
          break;
        case "open_suspect":
          if (p.suspect_id) router.push(`/suspect?id=${encodeURIComponent(p.suspect_id)}`);
          break;
        case "show_heatmap":
          if (p.district) qs.set("district", p.district);
          if (p.crime_type) qs.set("crime_type", p.crime_type);
          if (p.mode) qs.set("mode", p.mode);
          if (p.time_of_day) qs.set("time_of_day", p.time_of_day);
          if (p.case_id) qs.set("case_id", p.case_id);
          router.push(`/map${qs.toString() ? `?${qs}` : ""}`);
          break;
        case "show_network":
          if (p.suspect_id) qs.set("suspect_id", p.suspect_id);
          if (p.case_id) qs.set("case_id", p.case_id);
          const netTarget = p.q || p.query || p.target;
          if (netTarget) qs.set("q", netTarget);
          router.push(`/network${qs.toString() ? `?${qs}` : ""}`);
          break;
        case "show_dispatch":
          if (p.district) qs.set("district", p.district);
          router.push(`/dispatch${qs.toString() ? `?${qs}` : ""}`);
          break;
        case "show_anomalies":
          router.push("/overview#anomalies");
          break;
        case "show_analytics":
          router.push("/analytics");
          break;
        // The dedicated registry page was removed from the navigation; Analytics
        // carries the registry summary, so send the copilot there rather than to a
        // route that no longer exists. An action the client cannot honour is worse
        // than no action.
        case "show_models":
          router.push("/analytics");
          break;
        case "show_surveillance":
          if (p.district) qs.set("district", p.district);
          router.push(`/surveillance${qs.toString() ? `?${qs}` : ""}`);
          break;
        case "recent_cases":
          if (p.district) qs.set("district", p.district);
          if (p.crime_type) qs.set("crime_type", p.crime_type);
          if (p.sort) qs.set("sort", p.sort);
          if (p.q) qs.set("q", p.q);
          router.push(`/cases${qs.toString() ? `?${qs}` : ""}`);
          break;
        case "show_registry":
        case "show_suspects": {
          const isSuspectTab = p.kind === "criminals" || p.kind === "suspects" || (r.action as string) === "show_suspects";
          const tab = isSuspectTab ? "criminals" : (p.kind || "criminals");
          const term = p.q || p.query || p.target || "";
          qs.set("tab", tab);
          if (p.crime_type || term.toLowerCase().includes("homicide") || term.toLowerCase().includes("murder")) {
            qs.set("crime_type", p.crime_type || "Homicide");
          }
          if (p.repeat_only || term.toLowerCase().includes("repeat") || term.toLowerCase().includes("prior") || term.toLowerCase().includes("recidiv")) {
            qs.set("repeat_only", "true");
          }
          if (term && !term.toLowerCase().includes("homicide") && !term.toLowerCase().includes("murder")) {
            qs.set("q", term);
          }
          router.push(`/intel?${qs}`);
          break;
        }
        case "open_copilot":
          router.push("/copilot");
          break;
      }
    },
    [router]
  );

  // --- Text-to-speech: pause the mic while speaking so it never hears itself. ---
  const beginRecognitionRef = useRef<() => void>(() => {});

  const ttsWatchdog = useRef<any>(null);
  // Chrome/Edge TTS keep-alive: Chrome pauses SpeechSynthesis after ~15 s of
  // continuous playback. Calling resume() on a short interval prevents this.
  const ttsKeepAlive = useRef<any>(null);

  const speak = useCallback((text: string) => {
    const synth = typeof window !== "undefined" ? window.speechSynthesis : null;
    if (!synth || !ttsRef.current) return;
    try {
      speakingRef.current = true;
      setSpeaking(true);
      if (recogRef.current) {
        try { recogRef.current.stop(); } catch {}
      }
      try { synth.cancel(); } catch {}
      if (synth.paused) {
        try { synth.resume(); } catch {}
      }

      const clean = speakable(text);
      if (!clean) {
        speakingRef.current = false;
        setSpeaking(false);
        return;
      }

      const isEnglishText = /[a-zA-Z]/.test(clean);
      const targetLang = isEnglishText ? "en-IN" : (langRef.current?.code || "en-IN");

      const u = new SpeechSynthesisUtterance(clean);
      // Each spoken word swells the wave, so it moves with the voice.
      u.onboundary = () => {
        levelRef.current = Math.max(levelRef.current, 0.55 + Math.random() * 0.45);
      };
      u.rate = 0.96;
      u.pitch = 1.08;
      u.volume = 1.0;

      const voiceName = selectedVoiceNameRef.current || selectedVoiceName;
      const bestVoice = getHumanVoice(langRef.current?.code || "en-IN", voiceName);
      if (bestVoice) {
        try {
          u.voice = bestVoice;
        } catch {}
      }
      
      // Ensure u.lang matches the voice's language to prevent SpeechSynthesis errors/crashes
      u.lang = bestVoice ? bestVoice.lang : targetLang;

      let finished = false;
      const done = () => {
        if (finished) return;
        finished = true;
        clearTimeout(ttsWatchdog.current);
        clearInterval(ttsKeepAlive.current);
        speakingRef.current = false;
        setSpeaking(false);
        // Record when TTS ended — onresult will discard anything captured
        // within the cooldown window so the mic never echoes the speaker.
        ttsEndedAt.current = Date.now();
        // Long delay (1s) before restarting mic so the laptop speaker audio
        // fully dissipates and the mic doesn't pick up the AI's own voice.
        if (voiceRef.current) {
          setTimeout(() => beginRecognitionRef.current(), 1000);
        }
      };

      u.onend = done;
      u.onerror = (err: any) => {
        const errType = String(err?.error || err || "").toLowerCase();
        if (!finished) {
          finished = true;
          clearTimeout(ttsWatchdog.current);
          clearInterval(ttsKeepAlive.current);
          // If the user closed/canceled speech synthesis or stopped voice mode, DO NOT trigger fallback voice.
          if (errType.includes("canceled") || errType.includes("interrupted") || !speakingRef.current || !voiceRef.current) {
            speakingRef.current = false;
            setSpeaking(false);
            return;
          }
          try {
            try { synth.cancel(); } catch {}
            // Reliable Fallback: Create a clean utterance WITHOUT forcing a custom voice,
            // letting the browser engine use its robust default voice for the language.
            const fb = new SpeechSynthesisUtterance(clean);
            fb.onboundary = () => {
              levelRef.current = Math.max(levelRef.current, 0.55 + Math.random() * 0.45);
            };
            fb.rate = 0.96;
            fb.pitch = 1.0;
            fb.volume = 1.0;
            fb.lang = targetLang;
            fb.onend = () => {
              speakingRef.current = false;
              setSpeaking(false);
              ttsEndedAt.current = Date.now();
              if (voiceRef.current) setTimeout(() => beginRecognitionRef.current(), 1000);
            };
            fb.onerror = () => {
              speakingRef.current = false;
              setSpeaking(false);
              ttsEndedAt.current = Date.now();
              if (voiceRef.current) setTimeout(() => beginRecognitionRef.current(), 1000);
            };
            setTimeout(() => {
              try {
                if (speakingRef.current && voiceRef.current) synth.speak(fb);
                else {
                  speakingRef.current = false;
                  setSpeaking(false);
                }
              } catch {
                done();
              }
            }, 60);
            return;
          } catch {}
        }
        done();
      };

      setTimeout(() => {
        // Cut off (Stop speaking / dismiss / voice off) inside this 50 ms gap:
        // starting the utterance now would talk over the officer's own decision.
        if (!speakingRef.current) return;
        try {
          if (synth.paused) synth.resume();
          synth.speak(u);
          // Chrome keep-alive: nudge synth every 5s to prevent the 15s pause bug
          clearInterval(ttsKeepAlive.current);
          ttsKeepAlive.current = setInterval(() => {
            if (!synth.speaking) {
              clearInterval(ttsKeepAlive.current);
              return;
            }
            if (synth.paused) synth.resume();
          }, 5000);
        } catch {
          done();
        }
      }, 50);

      clearTimeout(ttsWatchdog.current);
      // More generous watchdog: 2.5s base + 85ms per char, max 25s
      ttsWatchdog.current = setTimeout(done, Math.min(25000, 2500 + clean.length * 85));
    } catch {
      speakingRef.current = false;
      setSpeaking(false);
    }
  }, [selectedVoiceName]);

  // Cut Kavya off mid-sentence and, in voice mode, go back to listening.
  // In Chrome/Edge cancel() surfaces as onerror("interrupted"), a path in speak()
  // that deliberately does NOT restart the mic, so the restart is scheduled here.
  // Engines that report onend instead run speak()'s done(), which schedules its own
  // (1 s) restart; safeStart's running guard makes the second call a no-op.
  const stopSpeaking = useCallback((resumeListening: boolean = true) => {
    clearTimeout(ttsWatchdog.current);
    clearInterval(ttsKeepAlive.current);
    const wasSpeaking = speakingRef.current;
    speakingRef.current = false;
    setSpeaking(false);
    try {
      window.speechSynthesis?.cancel();
    } catch {
      /* ignore */
    }
    if (!wasSpeaking) return;
    ttsEndedAt.current = Date.now() - (ECHO_WINDOW_MS - INTERRUPT_RESUME_MS);
    if (resumeListening && voiceRef.current) {
      setTimeout(() => beginRecognitionRef.current(), INTERRUPT_RESUME_MS);
    }
  }, []);

  const submit = useCallback(
    async (text: string) => {
      const q = text.trim();
      if (!q || busyRef.current) return;
      busyRef.current = true; // set synchronously so rapid speech-finals can't double-submit
      suppressSpeechRef.current = false; // a new question re-engages Kavya
      setBusy(true);
      setInput("");
      setInterim("");
      setLastCommand(q);
      setReply(null);
      setSuggestions([]);
      setPanelOpen(true);
      setTurns((t) => [...t, { id: ++turnId.current, role: "user", text: q }]);
      try {
        // Unlock browser SpeechSynthesis audio context if needed
        if (typeof window !== "undefined" && window.speechSynthesis) {
          if (window.speechSynthesis.paused) window.speechSynthesis.resume();
        }
        // Current-page entity so the agent resolves "this case / that suspect / open its suspect".
        let context: { case_id?: string; suspect_id?: string } | undefined;
        if (typeof window !== "undefined") {
          const sp = new URLSearchParams(window.location.search);
          const id = sp.get("id") || undefined;
          if (pathRef.current.startsWith("/case") && id) context = { case_id: id };
          else if (pathRef.current.startsWith("/suspect") && id) context = { suspect_id: id };
        }
        let r: AgentResult;
        try {
          r = await askAgent(q, historyRef.current.slice(-8), langRef.current.code.split("-")[0], pathRef.current, context, conversationRef.current);
          if (r.conversation_id) {
            conversationRef.current = r.conversation_id;
            setConversationId(r.conversation_id);
          }
        } catch (err: any) {
          if (err instanceof ApiError && err.isForbidden) {
            r = {
              action: "answer",
              reply: "Access denied. Please log in with investigator privileges to use the AI assistant.",
              params: {},
            };
          } else {
            // Client-side intelligent fallback if backend is offline
            r = {
              action: "answer",
              reply: "Could not reach the intelligence core. Executing local navigation.",
              params: {},
            };
          }
        }

        // The backend's routing decision is authoritative. A block here used to
        // discard r.action, r.params AND r.reply for any query containing
        // "suspect" / "offender" / "rowdy" / "criminal", substituting a registry
        // navigation and a hand-written sentence. That silently overrode the server
        // for a whole class of queries — including its grounding check — so the
        // reply shown was not the reply the server had verified.

        historyRef.current.push({ role: "user", content: q });
        historyRef.current.push({ role: "assistant", content: r.reply });
        setReply({ text: r.reply, action: r.action, trace: r.trace, mode: r.mode });
        setTurns((t) => [...t, { id: ++turnId.current, role: "assistant", text: r.reply, trace: r.trace, mode: r.mode }]);
        setSuggestions(r.suggestions?.slice(0, 3) ?? []);
        route(r);
        if (ttsRef.current && !suppressSpeechRef.current) (r.grounding && r.grounding.grounded === false
            // An unverified answer is not read aloud. Synthesised speech lends an
            // invented statement more authority, not less, and the listener has no
            // way to see the caution text.
            ? speak("This answer could not be verified against the case records. Please read it on screen.")
            : speak(r.speech || r.reply));
      } catch {
        const msg = "Could not process request. Please try again.";
        setReply({ text: msg, action: "answer" });
        setTurns((t) => [...t, { id: ++turnId.current, role: "assistant", text: msg }]);
        if (ttsRef.current && !suppressSpeechRef.current) speak(msg);
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [route, speak]
  );

  // --- Continuous recognition: ONE persistent instance (the "always listening" core). ---
  // Creating a fresh instance on every restart multiplied handlers and made the mic flicker
  // open/closed, recognising nothing. We keep a single instance and just re-start() it.
  const runningRef = useRef(false);
  const restartTimer = useRef<any>(null);
  const lastStartAt = useRef(0);
  const listeningStartedAt = useRef(0);
  const quickEndsRef = useRef(0);
  const safeStartRef = useRef<() => void>(() => {});
  const submitRef = useRef(submit);
  submitRef.current = submit;
  // finishPtt is defined after ensureRecog (it needs safeStart); the long-lived
  // onend handler reaches it through this ref.
  const finishPttRef = useRef<() => void>(() => {});
  const pttRetriedRef = useRef(false);

  const ensureRecog = useCallback(() => {
    if (recogRef.current) return recogRef.current;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) return null;
    let recog: any;
    try {
      recog = new SR();
    } catch {
      return null;
    }
    recog.continuous = true;
    recog.interimResults = true;
    recog.maxAlternatives = 1;

    recog.onstart = () => {
      runningRef.current = true;
      listeningStartedAt.current = Date.now();
      lastStartAt.current = Date.now();
      if (pttPhaseRef.current === "starting") pttPhaseRef.current = "live";
      setListening(true);
      setVoiceNote("");
    };
    recog.onend = () => {
      runningRef.current = false;
      setListening(false);
      const deliberate = deliberateStopRef.current;
      deliberateStopRef.current = false;

      // Push-to-talk owns this session: its end is the "send", never a restart.
      if (pttPhaseRef.current === "live" || pttPhaseRef.current === "ending") {
        finishPttRef.current();
        return;
      }
      // A stale end from the session paused just before push-to-talk began:
      // the capture the officer is holding for has not started yet — start it.
      // Retried once only: a mic that refuses to start must not spin here.
      if (pttPhaseRef.current === "starting") {
        if (pttRetriedRef.current) finishPttRef.current();
        else {
          pttRetriedRef.current = true;
          safeStartRef.current();
        }
        return;
      }

      // Paused: the whole point is that the mic stays off until Resume.
      if (!voiceRef.current || speakingRef.current || pauseRef.current) return;

      // We stopped it ourselves and the officer has already resumed — restart
      // cleanly without counting this as the mic failing.
      if (deliberate) {
        quickEndsRef.current = 0;
        listeningStartedAt.current = 0;
        clearTimeout(restartTimer.current);
        restartTimer.current = setTimeout(() => safeStartRef.current(), 150);
        return;
      }

      // Don't restart if we're inside the post-TTS cooldown window —
      // the done() callback in speak() will handle the restart after the delay.
      const msSinceTTS = ttsEndedAt.current > 0 ? Date.now() - ttsEndedAt.current : Infinity;
      if (msSinceTTS < 1200) return;
      
      const listeningDur = listeningStartedAt.current > 0 ? Date.now() - listeningStartedAt.current : 0;
      listeningStartedAt.current = 0; // Reset for next session

      if (listeningDur < 1500) {
        // Died almost instantly or very quickly (mic busy / permission blocked / OS hiccup / rapid error). 
        // Back off, and after a streak give up with guidance so it can NEVER flicker open/closed forever.
        quickEndsRef.current += 1;
        if (quickEndsRef.current >= 5) {
          voiceRef.current = false;
          setVoiceMode(false);
          setVoiceNote(
            "Voice couldn't hold the microphone. Check this site's mic permission (icon in the address bar) " +
              "and close any app using the mic (Zoom, Meet, Teams), then turn voice on again. Chrome or Edge works best."
          );
          return;
        }
        clearTimeout(restartTimer.current);
        restartTimer.current = setTimeout(() => safeStartRef.current(), 1000); // 1s backoff to prevent fast cycling
        return;
      }
      // Normal end (Chrome's silence timeout) → restart the SAME instance after a brief delay
      // to let the browser release the device, ensuring the restart is clean and lastStartAt is updated.
      quickEndsRef.current = 0;
      clearTimeout(restartTimer.current);
      restartTimer.current = setTimeout(() => safeStartRef.current(), 150);
    };
    recog.onerror = (e: any) => {
      const fatal = ["not-allowed", "service-not-allowed", "audio-capture"].includes(e?.error);
      // A refused mic ends push-to-talk on the coming onend instead of retrying it.
      if (fatal && pttPhaseRef.current !== "idle") pttPhaseRef.current = "ending";
      if (e?.error === "not-allowed" || e?.error === "service-not-allowed") {
        voiceRef.current = false;
        setVoiceMode(false);
        setVoiceNote("Microphone blocked. Click the mic/lock icon in the address bar, allow access, then turn voice on again.");
      } else if (e?.error === "audio-capture") {
        voiceRef.current = false;
        setVoiceMode(false);
        setVoiceNote("No microphone found. Plug one in and try again.");
      }
      // 'no-speech' / 'aborted' / 'network' are transient — onend handles the restart.
    };
    recog.onresult = (ev: any) => {
      quickEndsRef.current = 0; // Got a result, microphone is capturing audio successfully

      // Push-to-talk: collect everything said while held and send it once, on
      // release. The post-speech echo window is skipped here because the officer
      // is holding the control — the capture window is theirs, not ambient.
      if (pttPhaseRef.current !== "idle") {
        if (speakingRef.current) return;
        let pttInterim = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) {
          const res = ev.results[i];
          if (res.isFinal) pttFinalRef.current += res[0].transcript + " ";
          else pttInterim += res[0].transcript;
        }
        pttInterimRef.current = pttInterim;
        setInterim((pttFinalRef.current + pttInterim).trim());
        return;
      }

      // A result that was in flight when the officer pressed Pause — or turned voice
      // off / clicked away (stopVoice uses stop(), which still delivers a final) —
      // is dropped: "paused" and "off" must mean nothing more gets sent.
      if (pauseRef.current || !voiceRef.current) return;

      // Discard ALL results during TTS and within 1.2 s after TTS ends.
      // This prevents the mic from picking up the laptop speaker playing
      // the AI's own voice and treating it as a new user command.
      if (speakingRef.current) return;
      const msSinceTTS = ttsEndedAt.current > 0 ? Date.now() - ttsEndedAt.current : Infinity;
      if (msSinceTTS < 1200) return;

      let interimText = "";
      let finalText = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const res = ev.results[i];
        if (res.isFinal) finalText += res[0].transcript;
        else interimText += res[0].transcript;
      }
      if (interimText) setInterim(interimText);
      if (finalText.trim().length > 1 && !busyRef.current && !speakingRef.current) {
        setInterim("");
        submitRef.current(finalText);
      }
    };
    recogRef.current = recog;
    return recog;
  }, []);

  const safeStart = useCallback(() => {
    if (speakingRef.current || runningRef.current) return;
    // Push-to-talk may start the mic with voice mode off or paused; everything
    // else (onend restarts, post-speech restarts, language switches) may not.
    const forPtt = pttPhaseRef.current === "starting";
    if (!forPtt && (!voiceRef.current || pauseRef.current)) return;
    const recog = ensureRecog();
    if (!recog) return;
    recog.lang = langRef.current.code;
    lastStartAt.current = Date.now();
    try {
      recog.start();
      runningRef.current = true;
    } catch (e: any) {
      // "already started" → it IS running; anything else, onend will retry.
      if (e?.name === "InvalidStateError") runningRef.current = true;
    }
  }, [ensureRecog]);
  safeStartRef.current = safeStart;
  beginRecognitionRef.current = safeStart;

  // --- Push-to-talk. Only ever runs where it cannot fight always-listening: with
  // voice mode off, or with voice mode on but the mic paused. It borrows the SAME
  // recognition instance (never a second one) and is told apart by pttPhaseRef. ---
  const cancelPtt = useCallback(() => {
    if (pttPhaseRef.current === "idle") return;
    clearTimeout(pttEndTimer.current);
    pttPhaseRef.current = "idle";
    pttSourceRef.current = null;
    pttFinalRef.current = "";
    pttInterimRef.current = "";
    setPtt(false);
    setInterim("");
    try {
      recogRef.current?.abort?.();
    } catch {
      /* ignore */
    }
  }, []);

  const beginPtt = useCallback(
    (source: "pointer" | "key"): boolean => {
      if (pttPhaseRef.current !== "idle") return false;
      if (voiceRef.current && !pauseRef.current) return false; // already listening hands-free
      const w = window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown };
      if (!w.SpeechRecognition && !w.webkitSpeechRecognition) return false;
      // Talking over Kavya means "my turn": cut her off without the auto-restart.
      if (speakingRef.current) stopSpeaking(false);
      clearTimeout(pttEndTimer.current);
      clearTimeout(restartTimer.current);
      pttSourceRef.current = source;
      pttFinalRef.current = "";
      pttInterimRef.current = "";
      pttRetriedRef.current = false;
      pttPhaseRef.current = "starting";
      setPtt(true);
      setInterim("");
      setHint("");
      if (runningRef.current) {
        // The session paused a moment ago is still winding down; its onend
        // (seeing phase "starting") starts this capture.
        try {
          recogRef.current?.abort?.();
        } catch {
          /* ignore */
        }
        return true;
      }
      safeStart();
      return true;
    },
    [safeStart, stopSpeaking]
  );

  const endPtt = useCallback(() => {
    const phase = pttPhaseRef.current;
    if (phase === "idle" || phase === "ending") return;
    pttPhaseRef.current = "ending";
    // stop() (not abort) so the engine turns what it heard into a final result.
    try {
      recogRef.current?.stop();
    } catch {
      /* ignore */
    }
    // An engine that never reports onend (start() itself failed) must not leave
    // the "listening" pill stuck on screen.
    clearTimeout(pttEndTimer.current);
    pttEndTimer.current = setTimeout(() => {
      if (pttPhaseRef.current !== "ending") return;
      try {
        recogRef.current?.abort?.();
      } catch {
        /* ignore */
      }
      runningRef.current = false;
      finishPttRef.current();
    }, 2500);
  }, []);

  const finishPtt = useCallback(() => {
    if (pttPhaseRef.current === "idle") return;
    clearTimeout(pttEndTimer.current);
    pttPhaseRef.current = "idle";
    pttSourceRef.current = null;
    setPtt(false);
    setInterim("");
    // stop() normally finalises the last phrase; if this engine did not, send what
    // the officer saw on screen rather than silently dropping it.
    const text = (pttFinalRef.current + pttInterimRef.current).trim();
    pttFinalRef.current = "";
    pttInterimRef.current = "";
    if (text.length <= 1) flashHint("Didn't catch that. Hold, speak, then release.");
    else if (busyRef.current) flashHint("Kavya is still answering the last question. Try again in a moment.");
    else submitRef.current(text);
    // Resumed while holding: hand the mic back to always-listening.
    if (voiceRef.current && !pauseRef.current) {
      clearTimeout(restartTimer.current);
      restartTimer.current = setTimeout(() => safeStartRef.current(), 150);
    }
  }, [flashHint]);
  useEffect(() => {
    finishPttRef.current = finishPtt;
  }, [finishPtt]);

  // --- Mic pause / resume. Voice mode stays on; only recognition stops. ---
  const pauseMic = useCallback(
    (reason: Exclude<PauseReason, null>) => {
      if (!voiceRef.current) return;
      // A manual pause is never downgraded to a background one, or coming back to
      // the tab would un-pause a mic the officer deliberately switched off.
      if (pauseRef.current === "manual" || pauseRef.current === reason) return;
      setPauseReason(reason);
      clearTimeout(restartTimer.current);
      setInterim("");
      setListening(false);
      if (runningRef.current) {
        deliberateStopRef.current = true;
        try {
          // abort(), not stop(): a half-heard phrase must not be sent after Pause.
          if (recogRef.current?.abort) recogRef.current.abort();
          else recogRef.current?.stop();
        } catch {
          /* ignore */
        }
      }
    },
    [setPauseReason]
  );

  const resumeMic = useCallback(() => {
    if (!voiceRef.current || !pauseRef.current) return;
    setPauseReason(null);
    quickEndsRef.current = 0;
    listeningStartedAt.current = 0;
    // Same instance via ensureRecog. If the paused session's onend has not landed
    // yet, safeStart is a no-op and that onend (deliberateStopRef) restarts instead.
    // While Kavya is speaking, speak()'s done() does the restart, as before.
    safeStart();
  }, [safeStart, setPauseReason]);

  const toggleMicPause = useCallback(() => {
    if (pauseRef.current) resumeMic();
    else pauseMic("manual");
  }, [pauseMic, resumeMic]);

  const toggleTts = useCallback(() => {
    const next = !ttsRef.current;
    ttsRef.current = next;
    setTts(next);
    try {
      window.localStorage.setItem(TTS_PREF_KEY, next ? "on" : "off");
    } catch {
      /* storage blocked — the choice holds for this visit only */
    }
    // Muting mid-sentence silences her now; in voice mode she goes back to listening.
    if (!next) stopSpeaking(true);
  }, [stopSpeaking]);

  const stopVoice = useCallback(() => {
    voiceRef.current = false;
    speakingRef.current = false;
    // Turning voice off mid-answer means "be quiet", not "tell me later".
    suppressSpeechRef.current = true;
    // A push-to-talk in progress is abandoned, not sent.
    pttPhaseRef.current = "idle";
    pttSourceRef.current = null;
    pttFinalRef.current = "";
    pttInterimRef.current = "";
    clearTimeout(pttEndTimer.current);
    setPtt(false);
    setPauseReason(null);
    deliberateStopRef.current = false;
    // A stale watchdog firing done() after voice is restarted would open a bogus
    // 1.2 s echo window and drop the officer's first words.
    clearTimeout(ttsWatchdog.current);
    setVoiceMode(false);
    setSpeaking(false);
    setListening(false);
    setInterim("");
    clearTimeout(restartTimer.current);
    clearInterval(ttsKeepAlive.current);
    try {
      recogRef.current?.stop();
    } catch {
      /* ignore */
    }
    runningRef.current = false;
    if (typeof window !== "undefined") {
      try { window.speechSynthesis?.cancel(); } catch {}
    }
  }, [setPauseReason]);

  const startVoice = useCallback(() => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      setVoiceNote("Voice needs a Chromium browser (Chrome/Edge) over HTTPS or localhost.");
      return;
    }
    // Clear any stuck TTS/failure state from a previous session before starting fresh.
    if (typeof window !== "undefined") {
      window.speechSynthesis?.cancel();
      // Unlock audio context on Safari/Chrome by playing silent audio during user gesture
      const unlock = new SpeechSynthesisUtterance("");
      unlock.volume = 0;
      window.speechSynthesis?.speak(unlock);
    }
    speakingRef.current = false;
    setSpeaking(false);
    quickEndsRef.current = 0;
    listeningStartedAt.current = 0;
    deliberateStopRef.current = false;
    suppressSpeechRef.current = false;
    setPauseReason(null);
    voiceRef.current = true;
    setVoiceMode(true);
    // Voice replies follow the officer's (persisted) mute choice. Voice mode used
    // to force them back on, which made Mute silently undo itself every time voice
    // was restarted; the HUD now says "Replies muted" in plain text instead, so a
    // quiet Kavya is never a mystery.
    setOpen(true);
    setReply(null);
    setSuggestions([]);
    safeStart();
  }, [safeStart, setPauseReason]);

  const toggleVoice = useCallback(() => {
    if (voiceRef.current) stopVoice();
    else startVoice();
  }, [startVoice, stopVoice]);

  // Restart recognition with the new language when it changes mid-session.
  useEffect(() => {
    if (voiceRef.current && !speakingRef.current && runningRef.current) {
      try {
        recogRef.current?.stop(); // onend relaunches via safeStart with the new lang
      } catch {
        /* ignore */
      }
    }
  }, [lang]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      voiceRef.current = false;
      pttPhaseRef.current = "idle";
      clearTimeout(restartTimer.current);
      clearInterval(ttsKeepAlive.current);
      clearTimeout(pttEndTimer.current);
      clearTimeout(holdTimer.current);
      clearTimeout(hintTimer.current);
      try {
        recogRef.current?.stop();
      } catch {
        /* ignore */
      }
      if (typeof window !== "undefined") window.speechSynthesis?.cancel();
    };
  }, []);

  // "Close the assistant": collapse the conversation and the suggestion tray, and
  // switch the AI controls off — voice mode ends (mic released, speech cancelled);
  // in text mode any reply being read aloud stops. An answer still in flight is
  // shown when it lands but not spoken. Shared by click-outside and Esc.
  const dismiss = useCallback(() => {
    clearTimeout(holdTimer.current);
    cancelPtt();
    suppressSpeechRef.current = true;
    if (voiceRef.current) stopVoice();
    else stopSpeaking(false);
    setOpen(false);
    setPanelOpen(false);
  }, [cancelPtt, stopSpeaking, stopVoice]);

  // ⌘K focuses the text console. Esc keeps its meaning: in voice mode it turns
  // voice off (exactly as before); otherwise it closes the assistant.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
        setTimeout(() => inputRef.current?.focus(), 60);
      }
      if (e.key === "Escape") {
        cancelPtt();
        if (voiceRef.current) stopVoice();
        else dismiss();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cancelPtt, dismiss, stopVoice]);

  // Something of the assistant is on screen (or audible). Only then does a click
  // elsewhere mean "dismiss"; with everything closed the listener is not attached.
  const engaged = voiceMode || open || speaking || ptt || (panelOpen && turns.length > 0);
  const engagedRef = useRef(false);
  useEffect(() => {
    engagedRef.current = engaged;
  }, [engaged]);

  // Click / tap outside closes the assistant. Capture phase so page widgets that
  // stop propagation (map canvases, graph views) cannot swallow the dismissal.
  // This listens to the officer's pointer only: navigation the assistant performs
  // itself (voice commands, entity links inside the panel) is not a pointerdown
  // outside, so it never ends voice mode.
  useEffect(() => {
    if (!engaged) return;
    const onDown = (e: PointerEvent) => {
      if (e.pointerType === "mouse" && e.button !== 0) return; // right/middle click is not "dismiss"
      const root = rootRef.current;
      const target = e.target;
      if (!root || !(target instanceof Node) || root.contains(target)) return;
      if (pttPhaseRef.current !== "idle") return; // mid push-to-talk
      dismiss();
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [engaged, dismiss]);

  // Background tab: release the mic (a hidden page should not be listening to the
  // room), and take it back on return — but only if the tab caused the pause.
  useEffect(() => {
    const onVis = () => {
      if (document.hidden) {
        cancelPtt();
        if (voiceRef.current) pauseMic("hidden");
      } else if (pauseRef.current === "hidden") {
        resumeMic();
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [cancelPtt, pauseMic, resumeMic]);

  // Hold Space to talk. Active only where it cannot steal a keystroke that means
  // something else: voice mode paused, or voice off while the assistant is open —
  // never while typing or with a control focused (Space presses buttons), and
  // never during always-listening (nothing to hold for). Outside those cases Space
  // scrolls the page as usual.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      if (pttSourceRef.current === "key") {
        e.preventDefault(); // key-repeat while held must not scroll the page
        return;
      }
      if (e.repeat || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      const allowed = voiceRef.current ? !!pauseRef.current : engagedRef.current;
      if (!allowed || isInteractiveTarget(e.target)) return;
      if (beginPtt("key")) e.preventDefault();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== "Space" || pttSourceRef.current !== "key") return;
      e.preventDefault();
      endPtt();
    };
    // Focus left mid-hold (alt-tab): the keyup will never come. Abandon rather
    // than send half a question.
    const blur = () => {
      if (pttSourceRef.current === "key") cancelPtt();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, [beginPtt, cancelPtt, endPtt]);

  // Orb: tap toggles hands-free voice (unchanged); press-and-hold is push-to-talk.
  // The hold is only armed where push-to-talk is allowed, so with hands-free voice
  // live a long press is still just a tap.
  const onOrbPointerDown = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      suppressClickRef.current = false;
      if (e.pointerType === "mouse" && e.button !== 0) return;
      if (voiceRef.current && !pauseRef.current) return;
      // Capture at once: a release that lands off the orb before the hold timer
      // fires must still reach onOrbPointerUp and cancel it, or the timer would
      // open the mic with no release left to close it.
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        /* pointer already gone — pointerup/cancel below still end it */
      }
      clearTimeout(holdTimer.current);
      holdTimer.current = setTimeout(() => {
        if (!beginPtt("pointer")) return;
        suppressClickRef.current = true; // the release must not also toggle voice
      }, PTT_HOLD_MS);
    },
    [beginPtt]
  );
  const onOrbPointerUp = useCallback(() => {
    clearTimeout(holdTimer.current);
    if (pttSourceRef.current === "pointer") {
      endPtt();
      // If the release landed off the orb no click follows to consume the flag;
      // clear it so the next real tap or Enter is not swallowed.
      setTimeout(() => {
        suppressClickRef.current = false;
      }, 400);
    }
  }, [endPtt]);
  const onOrbPointerCancel = useCallback(() => {
    clearTimeout(holdTimer.current);
    if (pttSourceRef.current === "pointer") cancelPtt();
  }, [cancelPtt]);
  const onOrbClick = useCallback(() => {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    toggleVoice();
  }, [toggleVoice]);

  // The analyser only runs while the hands-free wave is showing live audio.
  useMicLevel(voiceMode && listening && !speaking && (!micPaused || ptt), levelRef);

  const openEntity = useCallback(
    (id: string) => {
      if (/^SUS-/i.test(id)) router.push(`/suspect?id=${encodeURIComponent(id.toUpperCase())}`);
      else router.push(`/case?id=${encodeURIComponent(id)}`);
    },
    [router]
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [turns, busy]);

  const clearConversation = useCallback(() => {
    historyRef.current = [];
    conversationRef.current = null;
    setConversationId(null);
    setTurns([]);
    setReply(null);
    setSuggestions([]);
  }, []);

  const orbState: VoiceState = speaking
    ? "speaking"
    : busy
    ? "thinking"
    : listening
    ? "listening"
    : "idle";

  const stateLabel = speaking
    ? "Speaking…"
    : busy
    ? "Thinking…"
    : ptt
    ? "Listening · release to send"
    : micPaused
    ? pauseReason === "hidden"
      ? "Mic paused · tab in background"
      : "Mic paused"
    : listening
    ? "Listening"
    : voiceMode
    ? "Starting mic…"
    : "";

  // Paused reads as paused everywhere the orb appears: desaturated, with a mic-off
  // badge. Done here rather than as a new VoiceState so the wave and the edge-glow
  // CSS (which key off the four existing states) are untouched.
  const showPaused = voiceMode && micPaused && !ptt;
  const pausedOrb = (orb: React.ReactNode, iconClass: string) => (
    <span className="relative inline-grid place-items-center">
      <span className={`inline-grid transition-[filter,opacity] duration-300 ${showPaused ? "opacity-70 grayscale" : ""}`}>{orb}</span>
      {showPaused && <MicOff className={`absolute text-white drop-shadow ${iconClass}`} aria-hidden="true" />}
    </span>
  );

  // Dark-HUD control pill. Focus ring comes from the global :focus-visible rule.
  const hudPill =
    "inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-[12px] font-semibold transition-colors active:scale-95";

  const chips = (dark: boolean) =>
    suggestions.length > 0 &&
    !busy && (
      <div className={`mt-3 flex flex-wrap gap-1.5 ${dark ? "justify-center" : ""}`}>
        {suggestions.map((s) => (
          <button
            key={s.query}
            onClick={() => submit(s.query)}
            disabled={busy}
            className={`group inline-flex items-center gap-1 rounded-full px-3 py-1.5 text-[11.5px] font-medium transition-all active:scale-95 disabled:opacity-40 ${
              dark
                ? "border border-white/15 bg-white/[0.06] text-white/85 hover:border-white/30 hover:bg-white/[0.12]"
                : "border border-brand/25 bg-brand/[0.06] text-brand/90 hover:border-brand/50 hover:bg-brand/12 hover:text-brand"
            }`}
          >
            {s.label}
            <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
          </button>
        ))}
      </div>
    );

  return (
    <div id="agent-console" className="fixed inset-x-0 bottom-0 z-40 flex justify-center px-4 pb-5">
      <AnimatePresence>
        {voiceMode && (
          // Dimmed while the mic is paused, so the screen edge stops claiming
          // "listening". Opacity on the wrapper leaves the fixed glow positioned
          // against the viewport as before.
          <div key="edge" className="transition-opacity duration-500" style={{ opacity: showPaused ? 0.3 : 1 }}>
            <EdgeGlow state={orbState} />
          </div>
        )}
      </AnimatePresence>
      <div ref={rootRef} className="relative z-50 w-full max-w-2xl">
        {/* ---------------- Siri-style voice HUD ---------------- */}
        <AnimatePresence>
          {voiceMode && (
            <motion.div
              key="voice-hud"
              initial={{ opacity: 0, y: 24, scale: 0.96, filter: "blur(6px)" }}
              animate={{ opacity: 1, y: 0, scale: 1, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: 16, scale: 0.97, filter: "blur(4px)" }}
              transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] }}
              className="mb-3 overflow-hidden rounded-[2rem] border border-white/10 bg-[#07070d]/95 text-white shadow-[0_40px_100px_-30px_rgba(60,40,140,0.65)] backdrop-blur-2xl"
            >
              {/* aurora backdrop */}
              <div className="pointer-events-none absolute inset-0 overflow-hidden rounded-[2rem]">
                <motion.div
                  className="absolute -left-1/4 -top-1/2 h-[140%] w-[150%] opacity-40"
                  style={{
                    background:
                      "radial-gradient(40% 35% at 30% 60%, rgba(150,95,255,0.45), transparent 70%), radial-gradient(35% 30% at 70% 55%, rgba(47,140,255,0.4), transparent 70%), radial-gradient(30% 25% at 50% 75%, rgba(231,180,60,0.35), transparent 70%)",
                  }}
                  animate={{ rotate: orbState === "thinking" ? 360 : [0, 8, -6, 0] }}
                  transition={
                    orbState === "thinking"
                      ? { duration: 6, repeat: Infinity, ease: "linear" }
                      : { duration: 14, repeat: Infinity, ease: "easeInOut" }
                  }
                />
              </div>
              <div className="relative p-5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    {pausedOrb(<SiriOrb state={orbState} size={30} />, "h-3.5 w-3.5")}
                    <div className="leading-tight">
                      <div className="text-[12px] font-semibold tracking-[0.18em] text-white/90">KAVYA</div>
                      <div
                        role="status"
                        aria-live="polite"
                        className={`text-[11px] ${showPaused ? "font-semibold text-brand-soft" : "text-white/50"}`}
                      >
                        {stateLabel || "Voice assistant"}
                        {!tts && <span className="text-white/40"> · replies muted</span>}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {availableVoices.length > 0 && (
                      <select
                        value={selectedVoiceName}
                        onChange={(e) => setSelectedVoiceName(e.target.value)}
                        className="hidden max-w-[150px] cursor-pointer rounded-full border border-white/15 bg-white/[0.06] px-2.5 py-1 text-[11px] font-semibold text-white/85 outline-none transition-all hover:bg-white/[0.12] sm:block"
                        title="Select voice"
                      >
                        <option value="" className="text-ink">Natural voice (auto)</option>
                        {availableVoices
                          .filter((v) => v.lang.toLowerCase().startsWith((lang.code || "en").split("-")[0].toLowerCase()))
                          .map((v) => (
                            <option key={v.name} value={v.name} className="text-ink">
                              {v.name.replace(/Microsoft|Google|Apple|Online|\(Natural\)/gi, "").trim()}
                            </option>
                          ))}
                      </select>
                    )}
                    <div className="hidden items-center rounded-full bg-white/[0.06] p-0.5 sm:flex">
                      {LANGS.map((l) => (
                        <button
                          key={l.code}
                          type="button"
                          onClick={() => setLang(l)}
                          aria-pressed={lang.code === l.code}
                          aria-label={`Switch language to ${l.native}`}
                          title={l.native}
                          className={`h-7 min-w-7 rounded-full px-2 text-[11.5px] font-semibold transition-all ${
                            lang.code === l.code ? "bg-white text-black" : "text-white/60 hover:text-white"
                          }`}
                        >
                          {l.label}
                        </button>
                      ))}
                    </div>
                    <button
                      type="button"
                      onClick={stopVoice}
                      title="Turn voice off (Esc)"
                      aria-label="Turn voice off"
                      className="grid h-8 w-8 place-items-center rounded-full bg-white/[0.06] text-white/60 transition-colors hover:bg-crimson/30 hover:text-white"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {/* live transcript */}
                <div className="mt-4 min-h-[2.4rem] px-2 text-center">
                  {interim ? (
                    <p className="text-[18px] font-medium leading-snug text-white">
                      {interim}
                      <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-white/80 align-middle" />
                    </p>
                  ) : ptt ? (
                    <p className="text-[15px] text-white/60">Listening… release to send</p>
                  ) : hint ? (
                    <p className="text-[15px] text-white/60">{hint}</p>
                  ) : showPaused && !busy && !speaking ? (
                    <p className="text-[15px] text-white/55">
                      {pauseReason === "hidden"
                        ? "Mic paused while this tab was in the background."
                        : "Mic paused. Nothing is being heard. Press Resume, or hold the orb to talk."}
                    </p>
                  ) : lastCommand && (busy || speaking || reply) ? (
                    <p className="text-[15px] leading-snug text-white/60">“{lastCommand}”</p>
                  ) : (
                    <p className="text-[15px] text-white/45">
                      {listening ? "Listening… ask about any case, person or district" : "Warming up the microphone…"}
                    </p>
                  )}
                </div>

                {/* the wave */}
                <div className="-mx-5 mt-1">
                  <SiriWave state={orbState} levelRef={levelRef} height={92} />
                </div>

                {busy && (
                  <div className="mt-1 text-center">
                    <ThinkingLine dark />
                  </div>
                )}

                <AnimatePresence mode="wait">
                  {reply && !busy && (
                    <motion.div
                      key={reply.text}
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.35 }}
                      className="kavya-scroll mt-2 max-h-[32vh] overflow-y-auto rounded-2xl border border-white/10 bg-white/[0.04] p-4 text-left text-[13.5px] leading-relaxed text-white/85"
                    >
                      <RichText text={reply.text} onEntity={openEntity} dark />
                      <TraceStrip trace={reply.trace} mode={reply.mode} dark />
                    </motion.div>
                  )}
                </AnimatePresence>
                {chips(true)}

                {/* Voice controls: labelled pills, not bare icons, so pausing the mic,
                    cutting Kavya off and muting her are findable at a glance. */}
                <div role="group" aria-label="Voice controls" className="mt-4 flex flex-wrap items-center justify-center gap-2">
                  <button
                    type="button"
                    onClick={toggleMicPause}
                    aria-pressed={micPaused}
                    aria-label="Pause microphone"
                    title={micPaused ? "Resume listening" : "Pause the microphone without leaving voice mode"}
                    className={`${hudPill} ${
                      micPaused
                        ? "border-brand-soft/60 bg-brand-soft/20 text-white hover:bg-brand-soft/30"
                        : "border-white/15 bg-white/[0.06] text-white/85 hover:border-white/30 hover:bg-white/[0.12]"
                    }`}
                  >
                    {micPaused ? <Play className="h-3.5 w-3.5" aria-hidden="true" /> : <Pause className="h-3.5 w-3.5" aria-hidden="true" />}
                    {micPaused ? "Resume mic" : "Pause mic"}
                  </button>
                  {speaking && (
                    <button
                      type="button"
                      onClick={() => stopSpeaking()}
                      aria-label="Stop speaking"
                      title={micPaused ? "Stop Kavya speaking" : "Stop Kavya speaking and listen again"}
                      className={`${hudPill} border-crimson/40 bg-crimson/20 text-white hover:bg-crimson/30`}
                    >
                      <Square className="h-3 w-3 fill-current" aria-hidden="true" />
                      Stop speaking
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={toggleTts}
                    aria-pressed={!tts}
                    aria-label="Mute voice replies"
                    title={tts ? "Kavya reads answers aloud. Click to mute (remembered)" : "Voice replies are muted (remembered). Click to unmute"}
                    className={`${hudPill} ${
                      tts
                        ? "border-white/15 bg-white/[0.06] text-white/85 hover:border-white/30 hover:bg-white/[0.12]"
                        : "border-white/25 bg-white/15 text-white hover:bg-white/20"
                    }`}
                  >
                    {tts ? <Volume2 className="h-3.5 w-3.5" aria-hidden="true" /> : <VolumeX className="h-3.5 w-3.5" aria-hidden="true" />}
                    {tts ? "Replies on" : "Replies muted"}
                  </button>
                </div>
                <p className="mt-3 hidden text-center text-[10.5px] tracking-wide text-white/35 sm:block">
                  Esc or click outside turns voice off · while paused, hold the orb (or Space, with no button focused) to talk
                </p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ---------------- Push-to-talk (voice mode off) ---------------- */}
        <AnimatePresence>
          {!voiceMode && (ptt || hint) && (
            <motion.div
              key="ptt"
              role="status"
              aria-live="polite"
              initial={{ opacity: 0, y: 10, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.98 }}
              transition={{ duration: 0.3, ease: [0.32, 0.72, 0, 1] }}
              className="mb-3 flex items-center gap-3 rounded-[1.4rem] border border-white/10 bg-[#07070d]/95 px-4 py-2.5 text-white shadow-[0_28px_70px_-22px_rgba(60,40,140,0.55)] backdrop-blur-2xl"
            >
              <SiriOrb state={ptt ? orbState : "idle"} size={24} />
              <p className="min-w-0 flex-1 truncate text-[13.5px] text-white/85">
                {ptt ? interim || "Listening… release to send" : hint}
              </p>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ---------------- Conversation (text mode) ---------------- */}
        <AnimatePresence>
          {!voiceMode && turns.length > 0 && panelOpen && (
            <motion.div
              key="conversation"
              initial={{ opacity: 0, y: 14, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.98 }}
              transition={{ duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
              className="mb-3 overflow-hidden rounded-[1.6rem] border border-black/10 bg-white/97 shadow-[0_28px_70px_-22px_rgba(17,20,32,0.22)] backdrop-blur-xl"
            >
              <div className="flex items-center justify-between border-b border-black/[0.06] px-4 py-2.5">
                <div className="flex items-center gap-2.5">
                  <SiriOrb state={busy ? "thinking" : "idle"} size={24} />
                  <span className="text-[12px] font-semibold tracking-[0.16em] text-ink">KAVYA</span>
                  <span className="text-[11px] text-faint">investigative copilot</span>
                </div>
                <div className="flex items-center gap-1">
                  {conversationId && (
                    <button
                      type="button"
                      onClick={() => router.push(`/copilot?c=${conversationId}`)}
                      title="Continue in Copilot"
                      aria-label="Continue this conversation in Copilot"
                      className="grid h-7 w-7 place-items-center rounded-full text-faint transition-colors hover:bg-black/[0.05] hover:text-ink"
                    >
                      <MessagesSquare className="h-3.5 w-3.5" />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={clearConversation}
                    title="New conversation"
                    aria-label="Start a new conversation"
                    className="grid h-7 w-7 place-items-center rounded-full text-faint transition-colors hover:bg-black/[0.05] hover:text-ink"
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setPanelOpen(false)}
                    title="Minimise"
                    aria-label="Minimise the conversation"
                    className="grid h-7 w-7 place-items-center rounded-full text-faint transition-colors hover:bg-black/[0.05] hover:text-ink"
                  >
                    <ChevronDown className="h-4 w-4" />
                  </button>
                </div>
              </div>
              <div ref={scrollRef} className="kavya-scroll max-h-[46vh] space-y-3 overflow-y-auto px-4 py-3.5">
                {turns.map((t) =>
                  t.role === "user" ? (
                    <motion.div
                      key={t.id}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="flex justify-end"
                    >
                      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-ink px-3.5 py-2 text-[13.5px] leading-snug text-white">
                        {t.text}
                      </div>
                    </motion.div>
                  ) : (
                    <motion.div
                      key={t.id}
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.35, ease: [0.32, 0.72, 0, 1] }}
                      className="flex items-start gap-2.5"
                    >
                      <span className="mt-0.5">
                        <SiriOrb state="idle" size={22} />
                      </span>
                      <div className="min-w-0 flex-1 text-[13.5px] leading-relaxed text-ink/90">
                        <RichText text={t.text} onEntity={openEntity} />
                        <TraceStrip trace={t.trace} mode={t.mode} />
                      </div>
                    </motion.div>
                  )
                )}
                {busy && (
                  <div className="flex items-center gap-2.5">
                    <SiriOrb state="thinking" size={22} />
                    <ThinkingLine />
                  </div>
                )}
                {chips(false)}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {voiceNote && !voiceMode && (
          <div className="mb-3 rounded-2xl border border-crimson/25 bg-crimson/[0.06] px-4 py-2.5 text-center text-[12.5px] text-crimson">
            {voiceNote}
          </div>
        )}

        {/* ---------------- Console dock ---------------- */}
        <motion.div
          layout
          initial={{ y: 28, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          transition={{ duration: 0.7, ease: [0.32, 0.72, 0, 1], delay: 0.15 }}
          className="rounded-[1.6rem] border border-black/10 bg-[#ffffff]/97 p-2 shadow-[0_28px_70px_-20px_rgba(17,20,32,0.16)] backdrop-blur-xl"
        >
          <AnimatePresence>
            {open && !voiceMode && turns.length === 0 && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.3, ease: [0.32, 0.72, 0, 1] }}
                className="overflow-hidden"
              >
                <div className="flex flex-wrap gap-2 px-2 pb-2.5 pt-1.5">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => submit(s)}
                      className="rounded-full border border-black/10 bg-black/[0.03] px-3 py-1.5 text-[11.5px] text-muted transition-all hover:border-brand/40 hover:text-ink active:scale-95"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit(input);
            }}
            className="flex items-center gap-2"
          >
            {/* Voice toggle — the orb itself is the button. Tap: hands-free on/off.
                Hold (voice off, or voice paused): push-to-talk, release to send. */}
            <button
              type="button"
              onClick={onOrbClick}
              onPointerDown={onOrbPointerDown}
              onPointerUp={onOrbPointerUp}
              onPointerCancel={onOrbPointerCancel}
              onContextMenu={(e) => e.preventDefault()}
              disabled={!speechOK}
              aria-pressed={voiceMode}
              aria-label={
                speechOK
                  ? voiceMode
                    ? micPaused
                      ? "Turn voice off (mic paused; hold to push-to-talk)"
                      : "Turn voice off"
                    : "Turn voice on (hold to push-to-talk)"
                  : "Voice not supported in this browser"
              }
              title={
                speechOK
                  ? voiceMode
                    ? micPaused
                      ? "Tap: turn voice off · Hold: talk while paused"
                      : "Turn voice off"
                    : "Tap: talk to Kavya hands-free · Hold: push-to-talk"
                  : "Voice not supported in this browser"
              }
              // touch-none + select-none: a long press on touch must not scroll,
              // select text or open the callout menu instead of talking.
              className="relative grid h-11 w-11 shrink-0 touch-none select-none place-items-center rounded-full transition-transform active:scale-95 disabled:opacity-40 [-webkit-touch-callout:none]"
            >
              {speechOK ? (
                <span className="relative grid place-items-center">
                  {pausedOrb(
                    <SiriOrb state={voiceMode || ptt || speaking ? orbState : busy ? "thinking" : "idle"} size={42} />,
                    "h-[17px] w-[17px]"
                  )}
                  {!voiceMode && !ptt && <Mic className="absolute h-[17px] w-[17px] text-white drop-shadow" />}
                </span>
              ) : (
                <span className="grid h-11 w-11 place-items-center rounded-full bg-black/[0.04] text-muted">
                  <MicOff className="h-[18px] w-[18px]" />
                </span>
              )}
            </button>

            <input
              ref={inputRef}
              value={input}
              onFocus={() => {
                setOpen(true);
                setPanelOpen(true);
              }}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                voiceMode
                  ? "Kavya is listening — speak, or type here…"
                  : "Ask Kavya anything — a case, a person, a district, a trend…"
              }
              className="min-w-0 flex-1 bg-transparent px-2 text-[14px] text-ink placeholder:text-faint focus:outline-none"
            />

            {!voiceMode && turns.length > 0 && !panelOpen && (
              <button
                type="button"
                onClick={() => setPanelOpen(true)}
                aria-label={`Show the conversation (${Math.ceil(turns.length / 2)} messages)`}
                className="hidden rounded-full bg-black/[0.04] px-2.5 py-1 text-[11px] font-semibold text-muted transition-colors hover:text-ink sm:block"
              >
                {Math.ceil(turns.length / 2)} msg
              </button>
            )}

            {!voiceMode && (
              <div className="hidden items-center rounded-full bg-black/[0.04] p-0.5 sm:flex">
                {LANGS.map((l) => (
                  <button
                    key={l.code}
                    type="button"
                    onClick={() => setLang(l)}
                    aria-pressed={lang.code === l.code}
                    aria-label={`Switch language to ${l.native}`}
                    title={l.native}
                    className={`h-8 min-w-8 rounded-full px-2 text-[12px] font-semibold transition-all ${
                      lang.code === l.code ? "bg-brand text-black" : "text-muted hover:text-ink"
                    }`}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            )}

            {/* Text mode reads answers aloud too, so the same two controls live here. */}
            {!voiceMode && speaking && (
              <button
                type="button"
                onClick={() => stopSpeaking(false)}
                aria-label="Stop speaking"
                title="Stop Kavya speaking"
                className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-crimson/10 text-crimson transition-colors hover:bg-crimson/20"
              >
                <Square className="h-3 w-3 fill-current" aria-hidden="true" />
              </button>
            )}
            {!voiceMode && (
              <button
                type="button"
                onClick={toggleTts}
                aria-pressed={!tts}
                aria-label="Mute voice replies"
                title={tts ? "Kavya reads answers aloud. Click to mute (remembered)" : "Voice replies muted (remembered). Click to unmute"}
                className={`grid h-8 w-8 shrink-0 place-items-center rounded-full transition-colors ${
                  tts ? "text-muted hover:bg-black/[0.05] hover:text-ink" : "bg-black/[0.06] text-ink"
                }`}
              >
                {tts ? <Volume2 className="h-4 w-4" aria-hidden="true" /> : <VolumeX className="h-4 w-4" aria-hidden="true" />}
              </button>
            )}

            <button
              type="submit"
              aria-label="Send query to the assistant"
              disabled={!input.trim() || busy}
              className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-gradient-to-b from-brand-soft to-brand text-black shadow-[0_4px_18px_-4px_rgba(231,180,60,0.7)] transition-all hover:brightness-105 active:scale-95 disabled:opacity-40 disabled:shadow-none"
            >
              {busy ? (
                <span className="flex items-end gap-0.5">
                  <span className="eq-bar h-3 w-0.5 bg-black/80" style={{ animationDelay: "0ms" }} />
                  <span className="eq-bar h-3 w-0.5 bg-black/80" style={{ animationDelay: "150ms" }} />
                  <span className="eq-bar h-3 w-0.5 bg-black/80" style={{ animationDelay: "300ms" }} />
                </span>
              ) : (
                <Send className="h-[17px] w-[17px]" />
              )}
            </button>
          </form>
        </motion.div>

        <p className="mt-2 hidden text-center text-[10.5px] tracking-wide text-faint sm:block">
          <kbd className="rounded bg-black/5 px-1.5 py-0.5 font-sans">⌘K</kbd> to ask ·{" "}
          {speechOK ? "tap the orb to talk hands-free, hold it to push-to-talk" : "EN / ಕನ್ನಡ"} · Esc or click away
          to close · every case and person id is verified against the records
        </p>
      </div>
    </div>
  );
}
