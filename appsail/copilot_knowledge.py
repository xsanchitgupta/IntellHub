"""What the copilot knows about the platform itself.

Two layers:

* **Curated topics** (`TOPICS`) — a short, hand-checked summary of what the platform
  is, its pages, data, models, governance and policing vocabulary. Fast and always
  available, but it only changes when someone edits it.
* **The generated index** — `artifacts/project_knowledge.json`, built from the
  repository by `build_project_knowledge.py`: every Markdown document by heading,
  every backend module docstring, every API route with its required role, every
  web page with its headings and copy. In a full checkout it is rebuilt in memory
  whenever a source file is newer than the artifact, so the copilot describes the
  project as it is today, not as it was when this file was last edited.

`search()` ranks both layers together (BM25 over the index, with curated topics
boosted), and `describe_live()` reads the running system — routes from Flask's URL
map, tables from the loaded corpus — which cannot go stale at all.
"""
import json
import math
import os
import re
import threading

TOPICS = {
    "overview": {
        "title": "KSP IntelHub — what it is",
        "keywords": ["intelhub", "platform", "project", "what is this", "about", "overview",
                     "purpose", "datathon", "scrb", "problem", "solution", "who built"],
        "text": (
            "KSP IntelHub is an AI crime-intelligence platform built for the State Crime "
            "Records Bureau (SCRB) of the Karnataka State Police for the KSP Datathon 2026. "
            "It replaces siloed, Excel-based reporting with one in-memory intelligence core "
            "over a 26-table CCTNS-shaped relational schema: 18,000 FIRs, 9,000 offenders, "
            "28,355 accused links, victims, complainants, arrests, chargesheets, court "
            "records, 85 officers, 700 stolen vehicles, 686 cyber-fraud cases, 320 missing "
            "persons, 500 CCTV cameras and 1,600 Dial-112 calls across all 31 Karnataka "
            "districts and 140 police stations. On top of it sit a knowledge graph "
            "(~28k nodes, ~61k edges), seven validated ML models, and this copilot. "
            "Core problems addressed: data silos and manual reporting; lack of advanced "
            "analytics; reactive rather than proactive policing; and missing relational "
            "intelligence across suspects, vehicles, cyber rings and rowdy-sheeter registries. "
            "IMPORTANT: the corpus is simulated, CCTNS-schema-shaped data with a documented "
            "generating process — not real KSP records."),
    },
    "pages": {
        "title": "Screens in the web client",
        "keywords": ["page", "pages", "screen", "screens", "navigate", "menu", "where can i",
                     "where do i", "dashboard", "ui", "interface", "tab"],
        "text": (
            "Sign-in (/) — official login with the Karnataka emblem and one-click demo personas "
            "(DGP, Admin, SP, DSP, SHO, SI, Analyst). "
            "Overview (/overview) — state-wide KPIs, trend alerts, anomalies, charts and the "
            "hour-by-weekday heatmap of when FIRs happen. "
            "Register FIR (/fir/new) — four-step CCTNS-schema FIR registration for station and "
            "district roles. "
            "Live News (/news) — Karnataka crime and policing headlines aggregated from news APIs, "
            "tagged by district and crime type. "
            "Cases (/cases) — searchable FIR directory filtered by district, crime type, status. "
            "Case dossier (/case?id=) — one FIR: narrative, accused, victim, IO, court status, "
            "similar cases (MO + proximity + narrative similarity) and AI suspect matching. "
            "Map (/map) — Google Maps or Leaflet heatmap with KDE predicted hotspots, DBSCAN "
            "zones and time-of-day filters. "
            "Network (/network) — force-directed 2D/3D link graph of suspects, FIRs, officers, "
            "cyber identifiers and vehicles, with predicted links and syndicates. "
            "Analytics (/analytics) — weekly forecast, annual crime-trend forecast per district "
            "(backtested against baselines), clusters, recidivism, link prediction, socio-economic "
            "correlation, the by-occupation profile and the model registry. "
            "Registry (/intel) — criminals/rowdy-sheeters, vehicles, missing persons, cyber, "
            "courts, officers tabs. "
            "Offender dossier (/suspect?id=) — one person: record, linked cases, associates, "
            "risk drivers. "
            "Officers (/officers) — roster, closure rates, assignment recommendations. "
            "Lifecycle (/lifecycle) — FIR → arrest → chargesheet → trial → conviction funnel and "
            "the case-flow Sankey (crime type → status → outcome). "
            "Pendency (/pendency) — survival-model triage of the open docket. "
            "Dispatch (/dispatch) — district risk index and recommended patrol strength. "
            "Account & roles (/access) — identity, jurisdiction, the role/permission matrix and "
            "the demo persona switcher."),
    },
    "data": {
        "title": "Data model",
        "keywords": ["data", "dataset", "schema", "table", "tables", "csv", "cctns", "columns",
                     "fields", "database", "records", "corpus", "simulated", "real data"],
        "text": (
            "26 relational tables mirroring CCTNS: CaseMaster (FIRs: CrimeNo, registration "
            "date, station, crime heads, status, court, coordinates, BriefFacts), Accused, "
            "Victim, ComplainantDetails, ArrestSurrender, ChargesheetDetails, "
            "ActSectionAssociation, CriminalsRegistry (rowdy_sheeter, history_sheet, "
            "active_warrant, gang, priors, known associates), CourtCasesRegistry, Employee/"
            "OfficersRegistry, Unit (police stations), District, Court, Act/Section, CrimeHead, "
            "plus lookup masters. Extended registries: stolen_vehicles, cybercrime (UPI ids, "
            "bank accounts, phones, IPs, URLs, wallets), missing_persons, cctv, complaints "
            "(Dial-112), socioeconomic (2011 Census-derived district indicators), "
            "case_reports (FIR narratives). Case ids are 18-digit CrimeNo values; offenders are "
            "SUS-####; officers are KGID######; missing persons MP-####. Real: district names, "
            "8 commissionerates, census population/literacy. Approximate: HQ coordinates. "
            "Derived: income, unemployment, police density. Everything else is simulated."),
    },
    "models": {
        "title": "The seven ML models",
        "keywords": ["model", "models", "ml", "machine learning", "accuracy", "auc", "metric",
                     "validated", "validation", "baseline", "registry", "how good", "trained"],
        "text": (
            "All are scored on a held-out window later in time than training, beside the "
            "trivial baseline they must beat (registry: artifacts/model_registry.json, page "
            "/analytics). 1) FIR narrative classifier — TF-IDF + logistic regression, "
            "10 classes, accuracy 0.808 vs majority 0.103. 2) Crime volume forecast — RidgeCV + "
            "Fourier seasonality, 8 weeks ahead, MASE 0.640, 77% realised coverage of an 80% "
            "interval; beats naive and seasonal-naive but NOT a 4-week moving average (0.634). "
            "3) Recidivism risk — re-offence within 365 days, ROC-AUC 0.709, calibrated; "
            "statistically indistinguishable from the dated hand rubric (0.708). "
            "4) Trial outcome — conviction vs acquittal, ROC-AUC 0.674. 5) Case progression — "
            "chargesheet within 180 days, ROC-AUC 0.646. 6) Case pendency — discrete-time "
            "survival, C-index 0.611, integrated Brier 0.209 vs Kaplan-Meier 0.241. "
            "7) Geospatial hotspots — haversine DBSCAN with a 32-point parameter sweep, "
            "silhouette 0.638. Also: KDE predicted hotspots, Isolation Forest anomalies, "
            "Adamic-Adar link prediction, Louvain syndicate detection, TF-IDF MO matching."),
    },
    "copilot": {
        "title": "This assistant (Kavya)",
        "keywords": ["you", "kavya", "assistant", "copilot", "who are you", "what can you do",
                     "capabilities", "help", "how do you work", "voice", "language", "kannada"],
        "text": (
            "Kavya is the IntelHub investigative copilot. It answers from the live corpus "
            "through retrieval tools — full case dossiers, person profiles and their case "
            "history, co-offender networks and paths between people, officers, stolen "
            "vehicles, cyber identifiers, missing persons, court records, filtered counts and "
            "breakdowns, and every ML output (forecast, hotspots, trends, recidivism, pendency, "
            "dispatch, syndicates). It can also drive the interface — open a dossier, the map, "
            "the network — and works by voice or text in English and Kannada. Every case, "
            "offender and officer id it names is verified against the corpus; person names "
            "are aliased before any prompt leaves the server. It will not declare anyone guilty, "
            "recommend action against a named individual, or turn a risk score into a "
            "prediction about a person."),
    },
    "scope": {
        "title": "What Kavya searches — and what it does not",
        "keywords": ["aadhaar", "aadhar", "internet", "social media", "osint", "google", "facebook",
                     "instagram", "outside database", "external database", "web search", "trace a person",
                     "track a person", "phone location", "call records", "cdr"],
        "text": (
            "Kavya searches only the records inside KSP IntelHub: FIRs, accused, victims, complainants, "
            "the offender registry, officers, courts, stolen vehicles, cyber-fraud identifiers, missing "
            "persons, CCTV and Dial-112 logs, the link graph and the ML outputs — every access role-gated "
            "and audited. It does not search the internet or social media, query Aadhaar or any UIDAI "
            "service, pull call records or locate a phone, and it does not assemble profiles of people "
            "from outside sources. Those requests go through the legal channels that govern them — for "
            "example a requisition to the telecom provider or UIDAI under the applicable law, approved by "
            "the competent authority — not through this platform. Within IntelHub, a phone number, UPI id, "
            "vehicle number or name can be checked against the records the platform already holds."),
    },
    "governance": {
        "title": "Governance",
        "keywords": ["governance", "ethic", "ethics", "bias", "fair", "fairness", "evidence",
                     "court", "responsible", "limits", "feedback loop", "contest", "dpia",
                     "retention", "erasure", "rights"],
        "text": (
            "Six constraints (GOVERNANCE.md): 1) no output is evidence; 2) nothing is stated as "
            "certainty — probabilities are calibrated and never 0% or 100%; 3) no output "
            "triggers an action automatically — thresholds order a queue for a person; "
            "4) every number is traceable to its source; 5) every model is measured against "
            "doing nothing (a baseline); 6) subgroup performance is checked (age band, gender). "
            "Contestability: /subject-report/<id> lists everything held about a person and "
            "/contest records a challenge, which marks the score on the serving path. "
            "Retention: /retention publishes schedules; erase and purge preview by default. "
            "Feedback-loop risk: recorded crime reflects enforcement as much as offending. "
            "Known gaps: challenge adjudication, scheduled retention, field outcomes and DPIA "
            "sign-off are absent and stated as such."),
    },
    "security": {
        "title": "Access control, audit and privacy",
        "keywords": ["security", "access", "login", "role", "roles", "analyst", "investigator",
                     "supervisor", "audit", "privacy", "pii", "alias", "password", "auth"],
        "text": (
            "Roles: analyst (aggregates only), investigator (cases, dossiers, network, "
            "copilot), supervisor (watchlist, audit log, settings, retention). Management "
            "statistics are open; aggregates containing person-level blocks are pseudonymised "
            "when signed out; dossiers return 403. Every person-level access is audited — "
            "actor, role, subject, stated reason (X-Access-Reason), IP, result — in a "
            "hash-chained (optionally HMAC-keyed) log verifiable at /audit-log/verify. "
            "Person names are replaced with [PERSON_n] aliases before any prompt reaches the "
            "LLM providers and restored locally. Passwords are scrypt-hashed with login "
            "lockout after 8 failures in 5 minutes."),
    },
    "tech": {
        "title": "Technology stack and deployment",
        "keywords": ["tech", "stack", "technology", "framework", "deploy", "deployment",
                     "catalyst", "appsail", "next", "react", "flask", "python", "llm", "groq",
                     "nvidia", "llama", "hosting", "architecture"],
        "text": (
            "Frontend: Next.js 16 (static export), React 19, Tailwind v4, Framer Motion, "
            "Recharts, Google Maps / Leaflet, react-force-graph + three.js. Backend: Python 3.13, "
            "Flask, Gunicorn on Zoho Catalyst AppSail, pandas/numpy, networkx, scikit-learn "
            "1.7.2 (the whole ML layer), CSV-backed in-memory store. LLM: Groq "
            "openai/gpt-oss-120b first (gpt-oss-20b and qwen3.8-27b on a rate limit), NVIDIA as fallback, via OpenAI-compatible tool "
            "calling; a deterministic retrieval engine answers when no provider is reachable. "
            "Client and API are on separate Catalyst domains; models load from committed "
            "joblib artifacts."),
    },
    "domain": {
        "title": "Policing terms",
        "keywords": ["rowdy", "sheeter", "history sheet", "history-sheet", "warrant", "fir",
                     "chargesheet", "charge sheet", "ipc", "bns", "io", "investigating officer",
                     "commissionerate", "cctns", "mo", "modus operandi", "pending trial"],
        "text": (
            "FIR — First Information Report, the registration of a cognisable offence; here "
            "identified by an 18-digit CrimeNo. Rowdy-sheeter — a person on a station's rowdy "
            "register (Karnataka Police Manual) for habitual violence, extortion or gang activity; "
            "tracked as CriminalsRegistry.rowdy_sheeter. History-sheeter — habitual offender "
            "under surveillance (history_sheet). Active warrant — an outstanding court warrant "
            "(active_warrant). Chargesheet — the final report filed in court after "
            "investigation; status 'Charge Sheeted'. Pending trial — chargesheeted cases "
            "awaiting verdict in CourtCasesRegistry. IO — investigating officer (KGID). "
            "MO — modus operandi: weapon, vehicle, target type and time of day. "
            "IPC/BNS — Indian Penal Code and its 2023 successor, Bharatiya Nyaya Sanhita. "
            "Commissionerates (Bengaluru, Mysuru, Mangaluru, Hubballi-Dharwad, Belagavi, "
            "Kalaburagi, Ballari, ...) are police jurisdictions separate from the 31 districts."),
    },
}


def lookup(query):
    """Best-matching knowledge sections for a free-text topic, most relevant first."""
    q = (query or "").lower()
    scored = []
    for key, t in TOPICS.items():
        score = 0
        if key in q:
            score += 3
        for kw in t["keywords"]:
            if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", q):
                score += 2 if " " in kw else 1
        if score:
            scored.append((score, key))
    scored.sort(reverse=True)
    keys = [k for sc, k in scored[:2] if sc >= scored[0][0] - 1] or ["overview"]
    out = [{"topic": k, "title": TOPICS[k]["title"], "text": TOPICS[k]["text"]} for k in keys]
    # A definition question wants the sentence that defines the term, not the section.
    terms = [w for w in re.findall(r"[a-z][a-z-]{2,}", q) if w not in _STOP]
    if terms:
        sentences = re.split(r"(?<=[.;])\s+(?=[A-Z0-9])", out[0]["text"])
        hits = [s for s in sentences if any(t.rstrip("s") in s.lower() for t in terms)]
        if hits and len(hits) < len(sentences):
            out[0]["excerpt"] = " ".join(hits[:3])
    return out


_STOP = {"what", "is", "are", "the", "does", "mean", "meaning", "define", "explain", "who", "how",
         "tell", "about", "this", "that", "and", "for", "with", "you", "your", "can", "platform",
         "project", "intelhub", "of", "in", "a", "an", "me", "please", "kind", "type", "model", "models",
         "validated", "work", "works", "page", "show", "shows", "against", "raise", "do", "i", "use", "get",
         "see", "find", "where", "which", "there", "any", "it", "its", "be", "they", "them"}


def is_platform_question(text):
    """True when a question is about the product itself rather than the records."""
    q = (text or "").lower()
    cues = ("this platform", "this project", "this app", "intelhub", "intel hub", "the platform",
            "who are you", "what can you do", "what are you", "your capabilities", "tech stack",
            "how does this work", "how was this built", "what models", "which models",
            "governance", "what is this", "how do you work", "about the project",
            "how is the data", "is the data real", "what pages", "what screens", "datathon",
            "which page", "what page", "which screen", "this page", "this screen", "feature", "features",
            "module", "source code", "codebase", "dpia", "audit chain", "audit log", "contest", "retention",
            "erasure", "access control", "role", "roles", "login", "sign in", "deploy", "catalyst",
            "appsail", "privacy", "pii", "alias", "how are names", "how do i use", "how to use",
            "where can i see", "where do i find", "aadhaar", "aadhar", "internet", "social media", "osint",
            "google", "facebook", "instagram", "outside database", "external database", "web search",
            "call records", "cdr", "phone location")
    if any(c in q for c in cues):
        return True
    if re.search(r"\b(api|apis|endpoints?|http route|backend route)\b", q):
        return True
    return bool(re.search(r"how (is|was|are) (the |this )?\w+( \w+)? (model|classifier|forecast)s? "
                          r"(validated|trained|evaluated|tested|built|calibrated)", q))


# =============================================================================
# The generated index
# =============================================================================
_HERE = os.path.dirname(os.path.abspath(__file__))
_ARTIFACT = os.path.join(_HERE, "artifacts", "project_knowledge.json")
_LOCK = threading.Lock()
_STATE = {"index": None, "built_at": 0.0}
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]+")
_STOPWORDS = {"the", "and", "for", "with", "that", "this", "are", "was", "from", "but", "not", "you",
              "can", "its", "has", "have", "into", "what", "which", "who", "how", "does", "is", "of",
              "in", "on", "to", "a", "an", "it", "be", "as", "by", "or", "at", "we", "they", "there",
              "tell", "me", "about", "please", "do", "i", "my", "our", "any", "all", "so", "if"}


def _tokens(text):
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOPWORDS]


def _sources_newer_than(path):
    try:
        import build_project_knowledge as B
        art = os.path.getmtime(path) if os.path.exists(path) else 0
        return any(os.path.getmtime(p) > art for p in B.source_paths() if os.path.exists(p))
    except Exception:
        return False


def _load_raw():
    """The artifact — rebuilt in memory when the checkout holds newer sources."""
    readme = os.path.join(os.path.dirname(_HERE), "README.md")
    if os.path.exists(readme) and _sources_newer_than(_ARTIFACT):
        try:
            import build_project_knowledge as B
            return B.build()
        except Exception:
            pass
    try:
        with open(_ARTIFACT, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"chunks": [], "routes": [], "pages": []}


def index():
    """BM25 index over generated chunks plus the curated topics. Built once, lazily."""
    with _LOCK:
        if _STATE["index"] is not None:
            return _STATE["index"]
        raw = _load_raw()
        docs = []
        for key, t in TOPICS.items():
            docs.append({"id": f"topic:{key}", "source": "curated", "kind": "topic", "title": t["title"],
                         "text": t["text"], "boost": 1.6, "extra": " ".join(t["keywords"])})
        for c in raw.get("chunks") or []:
            docs.append(dict(c, boost=1.0, extra=""))
        toks = [_tokens(f"{d['title']} {d['title']} {os.path.basename(d['source']).rsplit('.', 1)[0]} "
                        f"{d.get('extra', '')} {d['text']}") for d in docs]
        df = {}
        for tl in toks:
            for w in set(tl):
                df[w] = df.get(w, 0) + 1
        n = len(docs) or 1
        avg = sum(len(t) for t in toks) / n
        _STATE["index"] = {"docs": docs, "toks": [_counts(t) for t in toks], "lens": [len(t) for t in toks],
                           "idf": {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()},
                           "avg": avg or 1.0, "raw": raw}
        return _STATE["index"]


def _counts(tokens):
    out = {}
    for t in tokens:
        out[t] = out.get(t, 0) + 1
    return out


def reload():
    """Drop the cached index so the next query rebuilds it (used by /sync and tests)."""
    with _LOCK:
        _STATE["index"] = None


def search(query, k=4, kinds=None):
    """Best matching knowledge chunks for a free-text query, most relevant first."""
    ix = index()
    q = _tokens(query)
    if not q:
        return []
    k1, b = 1.4, 0.72
    scored = []
    for i, d in enumerate(ix["docs"]):
        if kinds and d["kind"] not in kinds:
            continue
        tf = ix["toks"][i]
        s = 0.0
        for w in q:
            f = tf.get(w) or tf.get(w.rstrip("s"))
            if f:
                s += ix["idf"].get(w, ix["idf"].get(w.rstrip("s"), 0.0)) * f * (k1 + 1) / (
                    f + k1 * (1 - b + b * ix["lens"][i] / ix["avg"]))
        if s > 0:
            scored.append((s * d["boost"], i))
    scored.sort(reverse=True)
    out = []
    for s, i in scored[:k]:
        d = ix["docs"][i]
        out.append({"title": d["title"], "source": d["source"], "kind": d["kind"],
                    "score": round(s, 2), "text": d["text"]})
    return out


def pages():
    return (index()["raw"].get("pages")) or []


def routes():
    return (index()["raw"].get("routes")) or []


def platform_map():
    """A compact, generated map of the product for the copilot's system prompt."""
    lines = []
    for p in pages():
        if p.get("description", "").startswith("Alias"):
            continue
        heads = ", ".join(p.get("headings", [])[:3]) or ", ".join(p.get("labels", [])[:3])
        lines.append(f"{p['url']}: {heads}")
    return "\n".join(lines)


_APP = {"app": None}


def configure(app=None):
    _APP["app"] = app


def describe_live(aspect, cache=None):
    """Facts read from the running system rather than from any document."""
    aspect = (aspect or "summary").lower()
    if aspect in ("routes", "api", "endpoints"):
        app = _APP["app"]
        documented = {r["path"]: r for r in routes()}
        live = []
        if app is not None:
            for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
                if not rule.rule.startswith("/serverless/api"):
                    continue
                d = documented.get(rule.rule, {})
                live.append({"path": rule.rule, "methods": sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS")),
                             "role": d.get("role", "public"), "doc": d.get("doc", "")[:160]})
        return {"count": len(live), "routes": live or list(documented.values())}
    if aspect in ("tables", "data", "schema", "dataset"):
        out = []
        for k, v in sorted((cache or {}).items()):
            if hasattr(v, "columns") and not k.startswith("_"):
                out.append({"table": k, "rows": int(len(v)), "columns": [str(c) for c in v.columns][:30]})
        return {"count": len(out), "tables": out}
    if aspect in ("pages", "screens", "ui"):
        return {"pages": [{"url": p["url"], "headings": p.get("headings", [])[:6], "labels": p.get("labels", [])[:8],
                           "note": p.get("description", "")[:160]} for p in pages()]}
    if aspect in ("models", "ml"):
        try:
            import agent_answers
            models = (agent_answers._registry() or {}).get("models") or {}
        except Exception:
            models = {}
        return {"count": len(models), "models": {n: m.get("headline") for n, m in models.items()}}
    if aspect in ("sources", "files", "modules"):
        return {"sources": index()["raw"].get("sources", []), "digest": index()["raw"].get("digest")}
    ix = index()
    return {"knowledge_chunks": len(ix["docs"]), "documents": len(index()["raw"].get("sources", [])),
            "pages": len(pages()), "documented_routes": len(routes()),
            "tables_loaded": sum(1 for v in (cache or {}).values() if hasattr(v, "columns")),
            "knowledge_digest": ix["raw"].get("digest")}
