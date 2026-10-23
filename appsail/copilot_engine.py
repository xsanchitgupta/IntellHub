"""The copilot's reasoning core: retrieval tools over the live corpus, an LLM
tool-calling loop, and a deterministic engine that answers when no model is reachable.

Why this exists
---------------
The copilot used to see the corpus through one twenty-line aggregate briefing plus a
substring search that returned six rows per table. Asked about a specific FIR it knew
the crime type and status and nothing else; asked about a person it knew a name and a
risk band; asked a follow-up ("who else was in that case?") it had nothing to look at.
It answered fluently anyway, which is worse than not answering.

This module gives it the same view of a record an investigator has:

* **Tools** that read the full record — a case with its narrative, accused, victim,
  complainant, IO, arrests, chargesheet, court stage, linked vehicles, cyber
  identifiers, CCTV and Dial-112 calls; a person with every case they are named in,
  co-offenders, syndicates, risk drivers and court outcomes; officers; registries;
  filtered counts and breakdowns; paths through the link graph; every ML output; and
  the platform's own documentation (`copilot_knowledge`).
* **An LLM loop** (OpenAI-compatible tool calling) that decides which tools to call,
  reads the results, and answers. Entities the question names are resolved and
  pre-fetched before the first call, so the common case costs one round trip.
* **A deterministic engine** that resolves the same entities and composes an answer
  from the same tools when no provider is configured or all of them fail. The demo
  must not depend on a third-party API being up.

Rules it holds to
-----------------
* Every tool result sent to a provider has person names replaced with request-scoped
  aliases (`privacy.mask`) and contact numbers dropped; answers are restored locally.
* Tools return what is recorded and say what is missing. They never fill a gap.
* Ranked lists of people (watchlists) are returned as identifiers and scores, never
  names — the platform's stated position (agent_answers._watchlist).
"""
import json
import logging
import os
import re
import time

import pandas as pd

import copilot_knowledge
import privacy
import statutes

LOG = logging.getLogger("ksp.copilot")

MAX_TOOL_STEPS = int(os.environ.get("COPILOT_MAX_TOOL_STEPS", "6"))
LLM_TIMEOUT_S = float(os.environ.get("COPILOT_LLM_TIMEOUT", "25"))
# The whole answer, every tool round included. The Catalyst gateway drops a request at
# 30 s, so an answer still thinking at 31 s reaches nobody; past this the loop answers
# from what it has gathered, or hands over to the deterministic engine.
DEADLINE_S = float(os.environ.get("COPILOT_DEADLINE", "20"))
FINAL_RESERVE_S = 8.0            # left for one tool-less answer at the end
_cooldown = {}                   # (provider, model) -> monotonic time it may be asked again

_GRAPH_GETTER = None


def configure(graph_getter):
    """main.py rebinds its knowledge graph on reload, so read it through a getter."""
    global _GRAPH_GETTER
    _GRAPH_GETTER = graph_getter


def _graph():
    try:
        return _GRAPH_GETTER() if _GRAPH_GETTER else None
    except Exception:
        return None


# =============================================================================
# Small helpers
# =============================================================================
def _frame(cache, key):
    df = cache.get(key)
    return df if df is not None and hasattr(df, "empty") and not df.empty else None


def _clean(v):
    """JSON-safe scalar; NaN/None/'nan' become None."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:
            pass
    if isinstance(v, str) and v.strip().lower() in ("", "nan", "none", "null"):
        return None
    return v


def _truthy(v):
    v = _clean(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y")
    return bool(v)


def _rec(row, cols):
    return {c: _clean(row.get(c)) for c in cols if c in row}


def _fmt_int(n):
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _date(v):
    v = _clean(v)
    return str(v)[:10] if v else None


# =============================================================================
# Corpus index — built once per loaded corpus
# =============================================================================
class _Index:
    pass


def _index(cache):
    firs = _frame(cache, "firs")
    if firs is None:
        return None
    key = (id(firs), len(firs))
    idx = cache.get("_copilot_index")
    if idx is not None and getattr(idx, "key", None) == key:
        return idx

    t0 = time.time()
    idx = _Index()
    idx.key = key
    f = firs.copy()
    f["case_id"] = f["case_id"].astype(str)
    f["_ts"] = pd.to_datetime(f["timestamp"], errors="coerce")
    idx.firs = f
    idx.case_pos = {cid: i for i, cid in enumerate(f["case_id"].tolist())}
    idx.cm2case = {}
    if "CaseMasterID" in f:
        idx.cm2case = {int(cm): cid for cm, cid in zip(f["CaseMasterID"], f["case_id"]) if pd.notna(cm)}
    idx.case2cm = {v: k for k, v in idx.cm2case.items()}
    idx.caseno2case = {}
    if "CaseNo" in f:
        for cno, cid in zip(f["CaseNo"].astype(str), f["case_id"]):
            idx.caseno2case.setdefault(cno, []).append(cid)

    links = _frame(cache, "links")
    idx.links_by_case, idx.links_by_person = {}, {}
    if links is not None:
        for cid, sid, rel in zip(links["case_id"].astype(str), links["suspect_id"].astype(str),
                                 links["relationship"].astype(str)):
            idx.links_by_case.setdefault(cid, []).append((sid, rel))
            idx.links_by_person.setdefault(sid, []).append((cid, rel))

    crim = _frame(cache, "criminals")
    idx.criminals = {}
    if crim is not None:
        for r in crim.to_dict("records"):
            idx.criminals[str(r["criminal_id"])] = r
    susp = _frame(cache, "suspects")
    if susp is not None:
        for r in susp.to_dict("records"):
            idx.criminals.setdefault(str(r["suspect_id"]), r)

    acc = _frame(cache, "Accused")
    idx.accused_age = {}
    if acc is not None:
        for pid, age in zip(acc["PersonID"].astype(str), acc["AgeYear"]):
            if pd.notna(age):
                idx.accused_age.setdefault(pid, int(age))

    rep = _frame(cache, "reports")
    idx.reports = dict(zip(rep["case_id"].astype(str), rep["report_text"])) if rep is not None else {}

    courts = _frame(cache, "courts")
    idx.court = {}
    if courts is not None:
        for r in courts.to_dict("records"):
            idx.court[str(r.get("fir_number"))] = r

    cs = _frame(cache, "ChargesheetDetails")
    idx.chargesheet = {}
    if cs is not None:
        for r in cs.to_dict("records"):
            idx.chargesheet.setdefault(int(r["CaseMasterID"]), r)

    ar = _frame(cache, "ArrestSurrender")
    idx.arrests = {}
    if ar is not None:
        for r in ar.to_dict("records"):
            idx.arrests.setdefault(int(r["CaseMasterID"]), []).append(r)

    comp = _frame(cache, "ComplainantDetails")
    idx.complainant = {}
    if comp is not None:
        for r in comp.to_dict("records"):
            idx.complainant.setdefault(int(r["CaseMasterID"]), r)

    off = _frame(cache, "officers")
    idx.officers = {str(r["officer_id"]): r for r in off.to_dict("records")} if off is not None else {}

    def _by(key_df, col):
        out = {}
        df = _frame(cache, key_df)
        if df is not None and col in df:
            for r in df.to_dict("records"):
                k = _clean(r.get(col))
                if k:
                    out.setdefault(str(k), []).append(r)
        return out

    idx.vehicles_by_case = _by("vehicles", "linked_case_id")
    idx.cyber_by_case = _by("cybercrime", "case_id")
    idx.cctv_by_case = _by("cctv", "case_link")
    idx.calls_by_case = _by("complaints", "linked_case_id")

    pend = (cache.get("pendency") or {}).get("cases") or []
    idx.pendency = {str(c.get("case_id")): c for c in pend if isinstance(c, dict)}
    idx.anomalies = {str(a.get("case_id")): a for a in (cache.get("anomalies") or []) if isinstance(a, dict)}

    # Person-name index: lowercase full name -> [(kind, id)].
    names = {}

    def _add(name, kind, ident):
        n = _clean(name)
        if not n:
            return
        k = re.sub(r"\s+", " ", str(n).strip().lower())
        if len(k) >= 4:
            names.setdefault(k, []).append((kind, str(ident)))

    for sid, r in idx.criminals.items():
        _add(r.get("name"), "offender", sid)
    for oid, r in idx.officers.items():
        _add(r.get("name"), "officer", oid)
    miss = _frame(cache, "missing")
    if miss is not None:
        for r in miss.to_dict("records"):
            _add(r.get("name"), "missing", r.get("person_id"))
    idx.names = names
    # Victims are indexed separately: thousands of them share common names, and a
    # victim match should never outrank an offender or officer of the same name.
    idx.victim_names = {}
    for cid, vn in zip(f["case_id"], f.get("victim_name", pd.Series([None] * len(f)))):
        n = _clean(vn)
        if n:
            idx.victim_names.setdefault(str(n).strip().lower(), []).append(cid)

    rec = cache.get("recidivism") or {}
    idx.rec_scores = rec.get("scores") or {}
    idx.rec_prob = rec.get("probabilities") or {}
    idx.rec_top = {str(t.get("criminal_id")): t for t in (rec.get("top") or [])}

    idx.syndicate_of = {}
    for syn in cache.get("syndicate_communities") or []:
        for m in syn.get("members") or []:
            idx.syndicate_of.setdefault(str(m.get("id")), []).append(syn)

    idx.districts = sorted(f["district"].dropna().astype(str).unique().tolist())
    idx.crime_types = sorted(f["crime_type"].dropna().astype(str).unique().tolist())
    idx.stations = sorted(f["police_station"].dropna().astype(str).unique().tolist()) if "police_station" in f else []

    try:
        cache["_copilot_index"] = idx
    except Exception:
        pass
    LOG.info("copilot index built in %.2fs (%d names)", time.time() - t0, len(names))
    return idx


# =============================================================================
# Entity resolution
# =============================================================================
RE_CRIMENO = re.compile(r"\b(\d{15,20})\b")
RE_FIRSHORT = re.compile(r"\b(?:fir|case|crime)\s*(?:no\.?|number|#)?\s*[-_:]?\s*0*(\d{4,9})\b", re.I)
RE_SUS = re.compile(r"\b(?:sus|suspect|offender|accused)\s*[-_#]?\s*0*(\d{3,6})\b", re.I)
RE_KGID = re.compile(r"\bKGID\s*-?\s*(\d{4,8})\b", re.I)
RE_MP = re.compile(r"\bMP\s*-\s*(\d{3,6})\b", re.I)
RE_VEH = re.compile(r"\b(KA)\s*-?\s*(\d{1,2})\s*-?\s*([A-Z]{1,3})\s*-?\s*(\d{3,4})\b", re.I)
RE_PHONE = re.compile(r"\b([6-9]\d{9})\b")
RE_UPI = re.compile(r"\b([\w.\-]{2,}@[a-z]{2,})\b", re.I)
RE_YEAR = re.compile(r"\b(20[12]\d)\b")

CRIME_SYNONYMS = {
    "Chain Snatching": ["chain snatch", "snatching", "chain theft", "mangalsutra", "gold chain"],
    "Extortion": ["extortion", "hafta", "protection money", "blackmail", "extort", "ransom"],
    "Narcotics Distribution": ["narcotic", "drug", "ndps", "peddl", "ganja", "mdma", "cocaine"],
    "Vehicle Theft": ["vehicle theft", "car theft", "bike theft", "motorcycle theft", "auto theft",
                      "vehicle lifting", "two-wheeler theft"],
    "House Break-in (Night)": ["house break", "housebreak", "burglary", "burglar", "break-in",
                               "break in", "home invasion", "dacoity"],
    "Sandalwood Smuggling": ["sandalwood", "sandal wood", "red sanders", "timber smuggl", "smuggling"],
    "Cyber Fraud (Jamtara style)": ["cyber fraud", "cybercrime", "cyber crime", "online fraud",
                                    "phishing", "jamtara", "upi fraud", "otp fraud", "digital arrest",
                                    "online scam", "cyber"],
    "Murder": ["murder", "murders", "killing", "killed"],
    "Homicide": ["homicide", "homicides", "culpable"],
    "Assault": ["assault", "assaults", "attack", "beating", "hurt", "grievous"],
}

STATUS_SYNONYMS = {
    "Under Investigation": ["under investigation", "pending investigation", "open case", "open cases",
                            "unsolved", "pending fir", "pending firs", "investigating", "still open"],
    "Charge Sheeted": ["charge sheeted", "chargesheeted", "charge-sheeted", "chargesheet filed"],
    "Closed": ["closed", "disposed"],
}

PRONOUN_CASE = re.compile(r"\b(this|that|the same|same|current|above|previous|last)\s+(case|fir|crime|incident|one)\b|\b(it|its)\b", re.I)
PRONOUN_PERSON = re.compile(r"\b(he|she|him|her|his|hers|they|them|their|this (person|suspect|offender|accused|guy|man|woman)|that (person|suspect|offender|accused|guy|man|woman))\b", re.I)


def _norm_key(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


DISTRICT_ALIASES = {
    "bangalore rural": "Bengaluru Rural", "bengaluru rural": "Bengaluru Rural",
    "bangalore urban": "Bengaluru Urban", "bangalore city": "Bengaluru Urban",
    "bengaluru city": "Bengaluru Urban", "bangalore": "Bengaluru Urban", "bengaluru": "Bengaluru Urban",
    "bengalooru": "Bengaluru Urban", "blr": "Bengaluru Urban", "mysore": "Mysuru",
    "mangalore": "Dakshina Kannada", "mangaluru": "Dakshina Kannada", "hubli": "Dharwad",
    "hubballi": "Dharwad", "belgaum": "Belagavi", "gulbarga": "Kalaburagi", "kalburgi": "Kalaburagi",
    "bellary": "Ballari", "shimoga": "Shivamogga", "tumkur": "Tumakuru", "bijapur": "Vijayapura",
    "chikmagalur": "Chikkamagaluru", "chikballapur": "Chikkaballapura", "hospet": "Vijayanagara",
    "hosapete": "Vijayanagara", "hampi": "Vijayanagara", "coorg": "Kodagu", "madikeri": "Kodagu",
    "karwar": "Uttara Kannada", "davangere": "Davanagere", "ramanagaram": "Ramanagara",
    "chamarajanagar": "Chamarajanagara", "bagalkot": "Bagalkote", "raichuru": "Raichur",
}


def district_span(text, idx):
    """(district, start, end) of the district named in text, longest match first."""
    low = (text or "").lower()
    best = None
    names = [(d.lower(), d) for d in idx.districts] + list(DISTRICT_ALIASES.items())
    for key, d in names:
        if d not in idx.districts:
            continue
        m = re.search(r"(?<![a-z])" + re.escape(key) + r"(?![a-z])", low)
        if m and (best is None or (m.end() - m.start()) > (best[2] - best[1])):
            best = (d, m.start(), m.end())
    return best


def detect_district(text, idx):
    sp = district_span(text, idx)
    return sp[0] if sp else None


def all_districts(text, idx):
    """Every district named in text, in the order named: "Mysuru, and what about
    Belagavi?" is two questions, and answering only the longest name drops one."""
    low = (text or "").lower()
    hits = []
    for key, d in [(d.lower(), d) for d in idx.districts] + list(DISTRICT_ALIASES.items()):
        if d in idx.districts:
            hits += [(m.start(), m.end(), d) for m in re.finditer(r"(?<![a-z])" + re.escape(key) + r"(?![a-z])", low)]
    out, taken = [], []
    for a, b, d in sorted(hits, key=lambda h: (h[0], -(h[1] - h[0]))):
        if any(a < y and x < b for x, y in taken):
            continue                      # "Bengaluru" inside "Bengaluru Rural"
        taken.append((a, b))
        if d not in out:
            out.append(d)
    return out


def detect_crime(text, idx):
    low = (text or "").lower()
    best, best_len = None, 0
    for ct in idx.crime_types:
        head = ct.split("(")[0].strip().lower()
        if head and head in low and len(head) > best_len:
            best, best_len = ct, len(head)
    for ct, syns in CRIME_SYNONYMS.items():
        if ct not in idx.crime_types:
            continue
        for s in syns:
            if re.search(r"(?<![a-z])" + re.escape(s), low) and len(s) > best_len:
                best, best_len = ct, len(s)
    return best


def detect_status(text):
    low = (text or "").lower()
    for st, syns in STATUS_SYNONYMS.items():
        if any(s in low for s in syns):
            return st
    return None


def crime_span(text, idx):
    """(crime_type, start, end) of the crime named in text."""
    low = (text or "").lower()
    best = None
    keys = [(ct.split("(")[0].strip().lower(), ct) for ct in idx.crime_types]
    keys += [(syn, ct) for ct, syns in CRIME_SYNONYMS.items() if ct in idx.crime_types for syn in syns]
    for key, ct in keys:
        m = re.search(r"(?<![a-z])" + re.escape(key) + r"[a-z]*", low)
        if m and (best is None or (m.end() - m.start()) > (best[2] - best[1])):
            best = (ct, m.start(), m.end())
    return best


ELLIPSIS = re.compile(r"^\s*(?:(?:what|how)\s+about|and(?:\s+in)?|same\s+(?:for|in)|what\s+of|now(?:\s+for)?|also(?:\s+for)?|then|ok(?:ay)?\s+(?:and|now))\b", re.I)
ANAPHORA = re.compile(r"\b(them|those|these|there|that district|that area|that place|same district|same crime|such cases|those cases|these cases)\b", re.I)


def _rewrite_one(text, prev, idx):
    """Rewrite one elliptical follow-up against the (already resolved) previous question."""
    if not prev:
        return text
    words = len(re.findall(r"\w+", text))
    cur_d, cur_c = district_span(text, idx), crime_span(text, idx)
    cur_y = RE_YEAR.search(text)
    prev_d, prev_c = district_span(prev, idx), crime_span(prev, idx)
    prev_y = RE_YEAR.search(prev)
    if ELLIPSIS.search(text) and words <= 9 and (cur_d or cur_c or cur_y):
        # "what about Mysuru?" -> the previous question with the new value swapped in.
        out = prev
        edits = []
        for cur, old, label in ((cur_d, prev_d, "in"), (cur_c, prev_c, ""), (cur_y, prev_y, "in")):
            if not cur:
                continue
            val = cur[0] if isinstance(cur, tuple) else cur.group(1)
            if old:
                start, end = (old[1], old[2]) if isinstance(old, tuple) else old.span(1)
                edits.append((start, end, val))
            else:
                out = f"{out.rstrip(' ?.')} {label} {val}".replace("  ", " ")
        for start, end, val in sorted(edits, reverse=True):
            out = out[:start] + val + out[end:]
        return out
    if ANAPHORA.search(text) or ELLIPSIS.search(text):
        add = []
        if not cur_c and prev_c:
            add.append(prev_c[0])
        if not cur_d and prev_d:
            add.append(f"in {prev_d[0]}")
        if not cur_y and prev_y:
            add.append(f"in {prev_y.group(1)}")
        if add:
            return f"{text.rstrip(' ?.')} ({' '.join(add)})"
    return text


def rewrite_followup(text, history, idx):
    """Resolve a follow-up that leans on earlier turns into a self-contained question.

    Earlier user turns are resolved in order first, so a chain of follow-ups —
    "how many murders in Mysuru in 2024", "what about Belagavi?", "and 2023?" — each
    rewrite against the fully resolved question before them.
    """
    if idx is None:
        return text
    resolved = None
    for h in history or []:
        if h.get("role") == "user" and (h.get("content") or "").strip():
            resolved = _rewrite_one(h["content"].strip(), resolved, idx)
    return _rewrite_one(text, resolved, idx)


def detect_station(text, idx):
    low = (text or "").lower()
    best = None
    for s in idx.stations:
        base = s.lower().replace(" ps", "").strip()
        if len(base) >= 4 and re.search(r"(?<![a-z])" + re.escape(base) + r"(?![a-z])", low):
            if best is None or len(s) > len(best):
                best = s
    return best


# Enough Kannada for the offline engine to read the common question shapes; the LLM
# path understands Kannada natively.
KANNADA_TERMS = {
    "ಕೊಲೆ": "murder", "ಹಲ್ಲೆ": "assault", "ಸರಗಳ್ಳತನ": "chain snatching", "ಕಳ್ಳತನ": "theft",
    "ವಾಹನ ಕಳ್ಳತನ": "vehicle theft", "ಮನೆಗಳ್ಳತನ": "house break", "ಸುಲಿಗೆ": "extortion",
    "ಮಾದಕ": "narcotics", "ಗಾಂಜಾ": "ganja", "ಶ್ರೀಗಂಧ": "sandalwood", "ಸೈಬರ್": "cyber fraud",
    "ವಂಚನೆ": "fraud", "ಎಷ್ಟು": "how many", "ಪ್ರಕರಣ": "cases", "ಜಿಲ್ಲೆ": "district",
    "ಠಾಣೆ": "station", "ತನಿಖೆಯಲ್ಲಿ": "under investigation", "ರೌಡಿ": "rowdy", "ವಾರಂಟ್": "warrant",
    "ಮುನ್ಸೂಚನೆ": "forecast", "ಹೆಚ್ಚು": "most", "ಆರೋಪಿ": "accused",
    "ಬೆಂಗಳೂರು": "bengaluru", "ಮೈಸೂರ": "mysuru", "ಬೆಳಗಾವಿ": "belagavi", "ಕಲಬುರಗಿ": "kalaburagi",
    "ಮಂಗಳೂರು": "mangaluru", "ಹುಬ್ಬಳ್ಳಿ": "hubballi", "ತುಮಕೂರು": "tumakuru", "ಬಳ್ಳಾರಿ": "ballari",
    "ಶಿವಮೊಗ್ಗ": "shivamogga", "ಹಾಸನ": "hassan", "ಮಂಡ್ಯ": "mandya", "ಉಡುಪಿ": "udupi",
}


def _kannada_to_english(text):
    if not re.search(r"[\u0C80-\u0CFF]", text or ""):
        return text
    out = text
    for kn, en in sorted(KANNADA_TERMS.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(kn, f" {en} ")
    return out


def resolve_case(ref, idx):
    """Any case reference -> canonical case_id, or None."""
    if ref is None:
        return None
    s = str(ref).strip()
    m = RE_CRIMENO.search(s)
    if m and m.group(1) in idx.case_pos:
        return m.group(1)
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if digits in idx.case_pos:
        return digits
    try:
        n = int(digits)
    except ValueError:
        return None
    if n in idx.cm2case:
        return idx.cm2case[n]
    if digits in idx.caseno2case:
        return idx.caseno2case[digits][0]
    return None


def resolve_suspect(ref, idx):
    if ref is None:
        return None
    s = str(ref).strip().upper()
    m = re.search(r"(\d{3,6})", s)
    if not m:
        return None
    cand = f"SUS-{int(m.group(1))}"
    if cand in idx.criminals:
        return cand
    cand2 = f"SUS-{m.group(1)}"
    return cand2 if cand2 in idx.criminals else None


def resolve_officer(ref, idx):
    if ref is None:
        return None
    m = re.search(r"(\d{4,8})", str(ref))
    if not m:
        return None
    oid = f"KGID{m.group(1)}"
    return oid if oid in idx.officers else None


def find_names(text, idx):
    """Person names appearing in free text, longest first: [(name, [(kind,id)...])]."""
    words = re.findall(r"[A-Za-z][A-Za-z.'-]+", text or "")
    found, used = [], set()
    for n in (3, 2):
        for i in range(len(words) - n + 1):
            if any(j in used for j in range(i, i + n)):
                continue
            key = _norm_key(" ".join(words[i:i + n]))
            if key in idx.names:
                found.append((" ".join(words[i:i + n]), idx.names[key]))
                used.update(range(i, i + n))
    return found


def extract_entities(text, idx, context=None, history=None):
    """Everything identifiable in a question, plus pronoun resolution."""
    e = {"cases": [], "suspects": [], "officers": [], "missing": [], "vehicles": [],
         "identifiers": [], "names": []}
    t = text or ""
    for m in RE_CRIMENO.finditer(t):
        c = resolve_case(m.group(1), idx)
        if c and c not in e["cases"]:
            e["cases"].append(c)
    for m in RE_FIRSHORT.finditer(t):
        c = resolve_case(m.group(1), idx)
        if c and c not in e["cases"]:
            e["cases"].append(c)
    for m in re.finditer(r"\bSUS\s*-?\s*(\d{3,6})\b", t, re.I):
        s = resolve_suspect(m.group(1), idx)
        if s and s not in e["suspects"]:
            e["suspects"].append(s)
    for m in RE_SUS.finditer(t):
        s = resolve_suspect(m.group(1), idx)
        if s and s not in e["suspects"]:
            e["suspects"].append(s)
    for m in RE_KGID.finditer(t):
        o = resolve_officer(m.group(1), idx)
        if o and o not in e["officers"]:
            e["officers"].append(o)
    for m in RE_MP.finditer(t):
        e["missing"].append(f"MP-{m.group(1)}")
    for m in RE_VEH.finditer(t):
        e["vehicles"].append(f"{m.group(1).upper()}-{int(m.group(2)):02d}-{m.group(3).upper()}-{m.group(4)}")
    for m in RE_PHONE.finditer(t):
        if not RE_CRIMENO.search(m.group(0)):
            e["identifiers"].append(m.group(1))
    for m in RE_UPI.finditer(t):
        e["identifiers"].append(m.group(1).lower())
    for name, hits in find_names(t, idx):
        e["names"].append({"name": name, "matches": hits})
        for kind, ident in hits:
            if kind == "offender" and len(hits) == 1 and ident not in e["suspects"]:
                e["suspects"].append(ident)
            if kind == "officer" and len(hits) == 1 and ident not in e["officers"]:
                e["officers"].append(ident)

    # "the first one", "the second accused" -> that position in the previous answer's list.
    om = re.search(r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|last)\s+(one|person|accused|suspect|offender|co-?offender|guy|man|woman)?\b", t, re.I)
    if om and not e["suspects"]:
        prev = next((str(h.get("content") or "") for h in reversed(history or []) if h.get("role") == "assistant"), "")
        ids = list(dict.fromkeys(m.upper() for m in re.findall(r"SUS-\d{3,6}", prev, re.I)))
        pos = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3,
               "fifth": 4, "5th": 4, "last": -1}[om.group(1).lower()]
        if ids and (pos == -1 or pos < len(ids)):
            e["suspects"].append(ids[pos])
            e["resolved_from_context"] = ids[pos]

    t = _kannada_to_english(t)
    e["district"] = detect_district(t, idx)
    e["crime_type"] = detect_crime(t, idx)
    e["status"] = detect_status(t)
    e["station"] = detect_station(t, idx)
    ym = RE_YEAR.search(t)
    e["year"] = int(ym.group(1)) if ym else None

    # Pronouns -> the entity in focus: the page being viewed, else the latest one
    # named in the conversation.
    e["resolved_from_context"] = None
    pm = PRONOUN_CASE.search(t)
    if pm and pm.group(0).lower() in ("it", "its") and (e["district"] or e["crime_type"]):
        pm = None      # "is it rising in Mysuru" is about Mysuru, not the case on screen
    if not e["cases"] and pm:
        c = (context or {}).get("case_id") or _last_in_history(history, "case", idx)
        c = resolve_case(c, idx) if c else None
        if c:
            e["cases"].append(c)
            e["resolved_from_context"] = c
    if not e["suspects"] and not e["names"] and PRONOUN_PERSON.search(t):
        s = (context or {}).get("suspect_id") or _last_in_history(history, "suspect", idx)
        s = resolve_suspect(s, idx) if s else None
        if s:
            e["suspects"].append(s)
            e["resolved_from_context"] = s
    return e


def _last_in_history(history, kind, idx):
    """The subject of the most recent turn that named one.

    The *first* id in a message, not the last: an answer leads with the record it is
    about and then lists related ones, so "does he have a warrant?" after a profile
    means the person profiled, not the fifth co-offender listed under them.
    """
    for h in reversed(history or []):
        content = str(h.get("content") or "")
        if kind == "case":
            for m in RE_CRIMENO.findall(content):
                if m in idx.case_pos:
                    return m
            for m in RE_FIRSHORT.findall(content):
                c = resolve_case(m, idx)
                if c:
                    return c
        else:
            ms = re.findall(r"SUS-\d{3,6}", content, re.I)
            if ms:
                return ms[0].upper()
    return None


# =============================================================================
# Tools
# =============================================================================
CASE_COLS = ["crime_type", "district", "police_station", "status", "severity_score", "time_of_day",
             "target_type", "weapon_used", "vehicle_used", "ipc_bns_sections", "fatalities",
             "injured", "witnesses", "accused_count"]


def _person_brief(sid, idx, rel=None):
    r = idx.criminals.get(sid, {})
    out = {"suspect_id": sid, "name": _clean(r.get("name")), "relationship": rel,
           "alias": _clean(r.get("alias")), "age": idx.accused_age.get(sid),
           "gang": _clean(r.get("gang_affiliation")), "risk_level": _clean(r.get("risk_level")),
           "prior_firs": _clean(r.get("past_fir_count")),
           "rowdy_sheeter": _truthy(r.get("rowdy_sheeter")),
           "history_sheeter": _truthy(r.get("history_sheet")),
           "active_warrant": _truthy(r.get("active_warrant"))}
    if sid in idx.rec_scores:
        out["recidivism_index"] = idx.rec_scores[sid]
    return {k: v for k, v in out.items() if v not in (None, "")}


def tool_get_case(cache, case_ref=None, **_):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    cid = resolve_case(case_ref, idx)
    if not cid:
        return {"error": f"No case matching '{case_ref}' exists in the corpus.",
                "hint": "Case ids are 18-digit CrimeNo values; short forms like FIR-20345 use CaseMasterID."}
    row = idx.firs.iloc[idx.case_pos[cid]].to_dict()
    cm = idx.case2cm.get(cid)
    out = {"case_id": cid, "case_master_id": cm, "case_no": _clean(row.get("CaseNo")),
           "registered": _clean(row.get("timestamp"))}
    out.update(_rec(row, CASE_COLS))
    # Which penal code governs depends on the offence date, not on the section text; a
    # model reading "IPC 457" alone called a post-July-2024 booking correct.
    when = row.get("timestamp")
    if statutes.code_for(when):
        booked = re.findall(r"\b(IPC|BNS)\s*([0-9]+[A-Za-z]?(?:\(\w+\))*)", str(row.get("ipc_bns_sections") or ""))
        out["code_in_force"] = statutes.code_for(when)
        found = statutes.check(booked, when)
        if found:
            out["statute_findings"] = [f["message"] for f in found]
    out["location"] = {"lat": _clean(row.get("latitude")), "lng": _clean(row.get("longitude"))}
    narrative = idx.reports.get(cid)
    if narrative:
        out["narrative"] = str(narrative)[:1100]
    out["victim"] = {k: v for k, v in {
        "name": _clean(row.get("victim_name")), "age": _clean(row.get("victim_age")),
        "gender": _clean(row.get("victim_gender")),
        "is_police_personnel": _truthy(row.get("victim_police"))}.items() if v is not None}
    comp = idx.complainant.get(cm) if cm is not None else None
    if comp:
        out["complainant"] = {"name": _clean(comp.get("ComplainantName")), "age": _clean(comp.get("AgeYear"))}
    out["accused"] = [_person_brief(sid, idx, rel) for sid, rel in idx.links_by_case.get(cid, [])]
    oid = _clean(row.get("officer_id"))
    if oid and oid in idx.officers:
        o = idx.officers[oid]
        out["investigating_officer"] = {"officer_id": oid, "name": _clean(o.get("name")),
                                        "rank": _clean(o.get("rank")), "station": _clean(o.get("station"))}
    elif oid:
        out["investigating_officer"] = {"officer_id": oid}
    arrests = idx.arrests.get(cm, []) if cm is not None else []
    out["arrests"] = {"count": len(arrests),
                      "first_date": min((_date(a.get("ArrestSurrenderDate")) for a in arrests
                                         if _date(a.get("ArrestSurrenderDate"))), default=None)}
    cs = idx.chargesheet.get(cm) if cm is not None else None
    if cs:
        out["chargesheet"] = {"filed_on": _date(cs.get("csdate")), "type": _clean(cs.get("cstype"))}
    court = idx.court.get(cid)
    if court:
        out["court"] = _rec(court, ["court_case_no", "court_name", "chargesheet_status",
                                    "conviction_status", "next_hearing"])
    veh = idx.vehicles_by_case.get(cid)
    if veh:
        out["stolen_vehicles"] = [_rec(v, ["vehicle_number", "make_model", "theft_date",
                                           "recovery_status", "theft_district"]) for v in veh[:3]]
    cy = idx.cyber_by_case.get(cid)
    if cy:
        c = cy[0]
        out["cyber_identifiers"] = _rec(c, ["upi_ids", "bank_accounts", "phone_numbers", "ip_addresses",
                                            "urls", "crypto_wallet", "fraud_amount", "victim_count"])
        rings = [r for r in ((cache.get("extended") or {}).get("cyber_rings") or []) if cid in (r.get("cases") or [])]
        if rings:
            out["cyber_rings"] = [{"identifier": r["identifier"], "linked_cases": r["linked_cases"],
                                   "total_fraud": r["total_fraud"]} for r in rings[:3]]
    cams = idx.cctv_by_case.get(cid)
    if cams:
        out["cctv"] = [_rec(c, ["camera_id", "timestamp", "detected_vehicle", "detected_faces"]) for c in cams[:3]]
    calls = idx.calls_by_case.get(cid)
    if calls:
        out["dial112_calls"] = [_rec(c, ["complaint_id", "emergency_type", "response_time_min",
                                         "resolution_status"]) for c in calls[:3]]
    p = idx.pendency.get(cid)
    if p:
        out["pendency_model"] = {"days_open": p.get("days_open"),
                                 "expected_additional_days": p.get("expected_additional_days"),
                                 "breach_risk": p.get("breach_risk")}
    if cid in idx.anomalies:
        out["anomaly_flag"] = idx.anomalies[cid].get("reasons")
    out["similar_cases"] = _similar_cases(row, cid, idx)
    return out


def _similar_cases(row, cid, idx, k=3):
    f = idx.firs
    pool = f[(f["crime_type"] == row.get("crime_type")) & (f["case_id"] != cid)]
    if pool.empty:
        return []
    score = ((pool["district"] == row.get("district")).astype(int) * 3
             + (pool["weapon_used"] == row.get("weapon_used")).astype(int) * 2
             + (pool["vehicle_used"] == row.get("vehicle_used")).astype(int) * 2
             + (pool["time_of_day"] == row.get("time_of_day")).astype(int)
             + (pool["target_type"] == row.get("target_type")).astype(int))
    top = pool.assign(_s=score).sort_values(["_s", "severity_score"], ascending=False).head(k)
    out = []
    for _, r in top.iterrows():
        shared = [lab for lab, col in (("district", "district"), ("weapon", "weapon_used"),
                                       ("vehicle", "vehicle_used"), ("time band", "time_of_day"),
                                       ("target", "target_type")) if r[col] == row.get(col)]
        out.append({"case_id": r["case_id"], "district": r["district"], "status": r["status"],
                    "registered": _date(r["timestamp"]), "shared_mo": shared})
    return out


def tool_get_person(cache, person_ref=None, **_):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    ref = str(person_ref or "").strip()
    sid = resolve_suspect(ref, idx) if re.search(r"\d", ref) else None
    if not sid:
        hits = idx.names.get(_norm_key(ref), [])
        offenders = [i for k, i in hits if k == "offender"]
        others = [{"kind": k, "id": i} for k, i in hits if k != "offender"]
        victim_cases = idx.victim_names.get(_norm_key(ref), [])
        if len(offenders) == 1:
            sid = offenders[0]
        elif len(offenders) > 1:
            return {"ambiguous": True, "query": ref,
                    "candidates": [_person_brief(s, idx) for s in offenders[:8]],
                    "note": f"{len(offenders)} offenders share this name; ask which id."}
        else:
            if others or victim_cases:
                return {"query": ref, "offender_record": None, "other_matches": others,
                        "named_as_victim_in": victim_cases[:8],
                        "victim_case_count": len(victim_cases)}
            return {"error": f"No person named or numbered '{ref}' is in the registry."}
    r = idx.criminals.get(sid, {})
    prof = _person_brief(sid, idx)
    prof.update({"fingerprint_id": _clean(r.get("fingerprint_id")),
                 "past_arrests": _clean(r.get("past_arrests")),
                 "known_associates_on_file": [a for a in str(_clean(r.get("known_associates")) or "").split(";") if a]})
    cases = []
    for cid, rel in idx.links_by_person.get(sid, []):
        pos = idx.case_pos.get(cid)
        if pos is None:
            continue
        c = idx.firs.iloc[pos]
        court = idx.court.get(cid) or {}
        cases.append({"case_id": cid, "role": rel, "crime_type": c["crime_type"], "district": c["district"],
                      "station": c.get("police_station"), "registered": _date(c["timestamp"]),
                      "status": c["status"], "severity": _clean(c["severity_score"]),
                      "court_outcome": _clean(court.get("conviction_status"))})
    cases.sort(key=lambda x: x["registered"] or "", reverse=True)
    prof["cases_in_corpus"] = len(cases)
    if cases:
        cdf = pd.DataFrame(cases)
        prof["crime_types"] = cdf["crime_type"].value_counts().to_dict()
        prof["districts"] = cdf["district"].value_counts().head(6).to_dict()
        prof["case_status"] = cdf["status"].value_counts().to_dict()
        prof["court_outcomes"] = cdf["court_outcome"].dropna().value_counts().to_dict()
        prof["first_seen"] = cases[-1]["registered"]
        prof["last_seen"] = cases[0]["registered"]
    prof["cases"] = cases[:15]
    co = {}
    for cid, _rel in idx.links_by_person.get(sid, []):
        for other, _r2 in idx.links_by_case.get(cid, []):
            if other != sid:
                co[other] = co.get(other, 0) + 1
    prof["co_offenders"] = [dict(_person_brief(o, idx), shared_cases=n)
                            for o, n in sorted(co.items(), key=lambda x: -x[1])[:8]]
    t = idx.rec_top.get(sid)
    if sid in idx.rec_scores:
        prof["recidivism"] = {"index": idx.rec_scores.get(sid),
                              "probability": idx.rec_prob.get(sid),
                              "band": (t or {}).get("band"),
                              "drivers": (t or {}).get("drivers"),
                              "meaning": "percentile rank for allocating attention; not a finding about the person"}
    syns = idx.syndicate_of.get(sid, [])
    if syns:
        prof["syndicates"] = [{"syndicate_id": s.get("syndicate_id"), "size": s.get("size"),
                               "risk_level": s.get("risk_level"),
                               "members": [m.get("id") for m in (s.get("members") or [])][:10]} for s in syns[:2]]
    links = [l for l in (cache.get("predicted_links") or []) if sid in (l.get("a"), l.get("b"))]
    if links:
        prof["predicted_links"] = [{"with": l["b"] if l["a"] == sid else l["a"], "score": l.get("score"),
                                    "common_associates": l.get("common_associates")} for l in links]
    return prof


def _filter_cases(idx, a):
    f = idx.firs
    m = pd.Series(True, index=f.index)
    applied = {}

    def _pick(val, options):
        if not val:
            return None
        v = str(val).strip().lower()
        for o in options:
            if o.lower() == v:
                return o
        for o in options:
            if v in o.lower() or o.lower().split("(")[0].strip() in v:
                return o
        return "__none__"

    d = _pick(a.get("district"), idx.districts)
    if d:
        m &= f["district"] == d
        applied["district"] = d
    ct = _pick(a.get("crime_type"), idx.crime_types)
    if a.get("crime_type") and ct == "__none__":
        ct = detect_crime(str(a.get("crime_type")), idx) or "__none__"
    if ct:
        m &= f["crime_type"] == ct
        applied["crime_type"] = ct
    st = a.get("status")
    if st:
        st2 = detect_status(str(st)) or _pick(st, ["Under Investigation", "Charge Sheeted", "Closed"])
        m &= f["status"] == st2
        applied["status"] = st2
    if a.get("police_station"):
        m &= f["police_station"].astype(str).str.lower().str.contains(str(a["police_station"]).lower().replace(" ps", ""), na=False)
        applied["police_station"] = a["police_station"]
    for col in ("weapon_used", "vehicle_used", "time_of_day", "target_type"):
        key = col.replace("_used", "")
        val = a.get(col) or a.get(key)
        if val:
            m &= f[col].astype(str).str.lower().str.contains(str(val).lower(), na=False)
            applied[col] = val
    if a.get("year"):
        try:
            m &= f["_ts"].dt.year == int(a["year"])
            applied["year"] = int(a["year"])
        except (TypeError, ValueError):
            pass
    if a.get("date_from"):
        m &= f["_ts"] >= pd.to_datetime(a["date_from"], errors="coerce")
        applied["date_from"] = a["date_from"]
    if a.get("date_to"):
        m &= f["_ts"] <= pd.to_datetime(a["date_to"], errors="coerce")
        applied["date_to"] = a["date_to"]
    if a.get("min_severity") is not None:
        m &= f["severity_score"] >= float(a["min_severity"])
        applied["min_severity"] = a["min_severity"]
    if _truthy(a.get("has_fatalities")):
        m &= f["fatalities"].fillna(0) > 0
        applied["has_fatalities"] = True
    if a.get("keyword"):
        kw = str(a["keyword"]).lower()
        ids = {cid for cid, txt in idx.reports.items() if kw in str(txt).lower()}
        m &= f["case_id"].isin(ids)
        applied["keyword"] = a["keyword"]
    return f[m], applied


def tool_search_cases(cache, limit=8, sort_by="recent", **filters):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    sel, applied = _filter_cases(idx, filters)
    n = len(sel)
    out = {"filters_applied": applied, "total_matching": n, "share_of_corpus_pct": round(n / len(idx.firs) * 100, 2)}
    if not n:
        return out
    out["by_status"] = sel["status"].value_counts().to_dict()
    if "crime_type" not in applied:
        out["by_crime_type"] = sel["crime_type"].value_counts().head(6).to_dict()
    if "district" not in applied:
        out["by_district"] = sel["district"].value_counts().head(6).to_dict()
    out["fatalities"] = int(sel["fatalities"].fillna(0).sum())
    out["injured"] = int(sel["injured"].fillna(0).sum())
    out["avg_severity"] = round(float(sel["severity_score"].mean()), 1)
    ts = sel["_ts"].dropna()
    if len(ts):
        out["date_range"] = [f"{ts.min():%Y-%m-%d}", f"{ts.max():%Y-%m-%d}"]
    key = {"severity": "severity_score", "fatalities": "fatalities"}.get(str(sort_by), "_ts")
    rows = sel.sort_values(key, ascending=(str(sort_by) == "oldest"))
    lim = max(1, min(int(limit or 8), 25))
    out["sorted_by"] = sort_by
    out["cases"] = [{"case_id": r["case_id"], "crime_type": r["crime_type"], "district": r["district"],
                     "station": r.get("police_station"), "registered": _date(r["timestamp"]),
                     "status": r["status"], "severity": _clean(r["severity_score"]),
                     "fatalities": _clean(r["fatalities"])} for _, r in rows.head(lim).iterrows()]
    return out


GROUPABLE = {"district", "crime_type", "status", "police_station", "time_of_day", "weapon_used",
             "vehicle_used", "target_type", "year", "month", "weekday", "victim_gender"}


def tool_aggregate(cache, group_by="district", metric="count", top_n=10, **filters):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    sel, applied = _filter_cases(idx, filters)
    g = str(group_by or "district")
    if g not in GROUPABLE:
        return {"error": f"cannot group by {g}", "allowed": sorted(GROUPABLE)}
    if g == "year":
        key = sel["_ts"].dt.year
    elif g == "month":
        key = sel["_ts"].dt.strftime("%Y-%m")
    elif g == "weekday":
        key = sel["_ts"].dt.day_name()
    else:
        key = sel[g]
    metric = str(metric or "count")
    if metric in ("fatalities", "injured"):
        s = sel[metric].fillna(0).groupby(key).sum()
    elif metric in ("avg_severity", "severity"):
        s = sel["severity_score"].groupby(key).mean().round(1)
    else:
        metric = "count"
        s = key.value_counts()
    order = str(filters.get("order", "desc")).lower()
    ascending = order == "asc"
    if g in ("year", "month") and order == "chronological":
        s = s.sort_index()
    else:
        s = s.sort_values(ascending=ascending)
    n = max(1, min(int(top_n or 10), 40))
    return {"filters_applied": applied, "group_by": g, "metric": metric, "total_cases": int(len(sel)),
            "chronological": order == "chronological",
            "groups": {str(k): (float(v) if metric == "avg_severity" else int(v)) for k, v in s.head(60 if order == "chronological" else n).items()},
            "n_groups": int(s.shape[0])}


def tool_search_people(cache, name=None, gang=None, risk_level=None, rowdy_sheeter=None,
                       history_sheeter=None, active_warrant=None, min_priors=None,
                       district=None, crime_type=None, limit=10, **_):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    df = pd.DataFrame(list(idx.criminals.values()))
    if "criminal_id" not in df:
        df["criminal_id"] = df.get("suspect_id")
    if "suspect_id" in df:
        df["criminal_id"] = df["criminal_id"].fillna(df["suspect_id"])
    m = pd.Series(True, index=df.index)
    applied = {}
    if name:
        m &= df["name"].astype(str).str.lower().str.contains(str(name).lower(), na=False) | \
             df.get("alias", pd.Series("", index=df.index)).astype(str).str.lower().str.contains(str(name).lower(), na=False)
        applied["name"] = name
    if gang:
        m &= df["gang_affiliation"].astype(str).str.lower().str.contains(str(gang).lower(), na=False)
        applied["gang"] = gang
    if risk_level:
        m &= df["risk_level"].astype(str).str.lower() == str(risk_level).lower()
        applied["risk_level"] = risk_level
    for flag, col in ((rowdy_sheeter, "rowdy_sheeter"), (history_sheeter, "history_sheet"),
                      (active_warrant, "active_warrant")):
        if flag is not None and col in df:
            m &= df[col].map(_truthy) == _truthy(flag)
            applied[col] = _truthy(flag)
    if min_priors is not None and "past_fir_count" in df:
        m &= pd.to_numeric(df["past_fir_count"], errors="coerce").fillna(0) >= float(min_priors)
        applied["min_priors"] = min_priors
    if district or crime_type:
        sel, ap = _filter_cases(idx, {"district": district, "crime_type": crime_type})
        ids = set()
        for cid in sel["case_id"]:
            ids.update(s for s, _ in idx.links_by_case.get(cid, []))
        m &= df["criminal_id"].isin(ids)
        applied.update(ap)
    sel = df[m]
    out = {"filters_applied": applied, "total_matching": int(len(sel))}
    if len(sel):
        out["by_risk_level"] = sel["risk_level"].value_counts().to_dict()
        gangs = sel["gang_affiliation"].dropna().astype(str)
        if len(gangs):
            out["top_gangs"] = gangs.value_counts().head(6).to_dict()
        for col, label in (("rowdy_sheeter", "rowdy_sheeters"), ("history_sheet", "history_sheeters"),
                           ("active_warrant", "with_active_warrant")):
            if col in sel:
                out[label] = int(sel[col].map(_truthy).sum())
        srt = pd.to_numeric(sel.get("past_fir_count"), errors="coerce").fillna(0)
        top = sel.assign(_p=srt).sort_values("_p", ascending=False).head(max(1, min(int(limit or 10), 20)))
        out["people"] = [_person_brief(str(s), idx) for s in top["criminal_id"]]
    return out


def tool_get_officer(cache, officer_ref=None, **_):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    oid = resolve_officer(officer_ref, idx)
    if not oid:
        hits = [i for k, i in idx.names.get(_norm_key(officer_ref), []) if k == "officer"]
        if len(hits) == 1:
            oid = hits[0]
        elif hits:
            return {"ambiguous": True, "candidates": [{"officer_id": h, **_rec(idx.officers[h], ["name", "rank", "station"])} for h in hits]}
    if not oid:
        return {"error": f"No officer matching '{officer_ref}'."}
    o = idx.officers[oid]
    out = {"officer_id": oid}
    out.update(_rec(o, ["name", "rank", "station", "district", "specialization", "cases_assigned",
                        "closure_rate", "commendations", "appointment_date"]))
    f = idx.firs
    mine = f[f["officer_id"].astype(str) == oid]
    out["cases_as_io_in_corpus"] = int(len(mine))
    if len(mine):
        out["by_status"] = mine["status"].value_counts().to_dict()
        out["by_crime_type"] = mine["crime_type"].value_counts().head(5).to_dict()
        out["recent_cases"] = [{"case_id": r["case_id"], "crime_type": r["crime_type"], "status": r["status"],
                                "registered": _date(r["timestamp"])}
                               for _, r in mine.sort_values("_ts", ascending=False).head(5).iterrows()]
    return out


# Below this many cases a closure rate is mostly noise; a worst-first list would otherwise
# be led by whoever happened to draw two hard cases.
MIN_CASES_FOR_RANKING = 15


def tool_search_officers(cache, district=None, rank=None, specialization=None, sort_by="closure_rate", order="desc",
                         min_cases=None, limit=8, **_):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    df = pd.DataFrame(list(idx.officers.values()))
    if df.empty:
        return {"total_matching": 0}
    state_avg = float(df["closure_rate"].mean()) if "closure_rate" in df else None
    for col, val in (("district", district), ("rank", rank), ("specialization", specialization)):
        if val:
            df = df[df[col].astype(str).str.lower().str.contains(str(val).lower(), na=False)]
    key = sort_by if sort_by in ("closure_rate", "cases_assigned", "commendations") else "closure_rate"
    asc = str(order).lower() == "asc"
    if min_cases is None and asc and key == "closure_rate":
        min_cases = MIN_CASES_FOR_RANKING
    if min_cases:
        df = df[df["cases_assigned"] >= int(min_cases)]
    df = df.sort_values(key, ascending=asc)
    out = {"total_matching": int(len(df)), "sorted_by": key, "order": "asc" if asc else "desc",
           "min_cases": int(min_cases) if min_cases else None,
           "state_avg_closure_rate": round(state_avg, 3) if state_avg is not None else None,
           "by_rank": df["rank"].value_counts().to_dict() if len(df) else {},
           "officers": [_rec(r, ["officer_id", "name", "rank", "station", "district", "specialization",
                                 "cases_assigned", "closure_rate", "commendations"])
                        for r in df.head(max(1, min(int(limit or 8), 20))).to_dict("records")]}
    if asc:
        out["caution"] = ("A closure rate alone does not establish poor work: it moves with case mix (cyber and "
                          "homicide cases close slower), station workload, tenure and transfers. Use it to pick files "
                          "to review; suspension is a disciplinary decision for a departmental inquiry.")
    return out


REGISTRY_FIELDS = {
    "vehicles": ("vehicles", ["vehicle_number", "make_model", "theft_district", "theft_date",
                              "recovery_status", "linked_case_id", "owner_name"]),
    "cybercrime": ("cybercrime", ["case_id", "upi_ids", "bank_accounts", "phone_numbers", "ip_addresses",
                                  "urls", "crypto_wallet", "fraud_amount", "victim_count"]),
    "missing": ("missing", ["person_id", "name", "age", "gender", "last_seen_district", "date_missing",
                            "found_status"]),
    "courts": ("courts", ["court_case_no", "fir_number", "court_name", "chargesheet_status",
                          "conviction_status", "next_hearing"]),
    "complaints": ("complaints", ["complaint_id", "district", "emergency_type", "response_time_min",
                                  "assigned_officer", "resolution_status", "linked_case_id"]),
    "cctv": ("cctv", ["camera_id", "district", "timestamp", "detected_faces", "detected_vehicle", "case_link"]),
}


def tool_search_registry(cache, kind="vehicles", query=None, district=None, status=None, limit=8, **_):
    kind = str(kind or "").lower()
    if kind in ("vehicle", "stolen_vehicles"):
        kind = "vehicles"
    if kind in ("cyber", "fraud"):
        kind = "cybercrime"
    if kind in ("missing_persons", "missing_person"):
        kind = "missing"
    if kind in ("court", "trials", "trial"):
        kind = "courts"
    if kind in ("dial112", "calls", "112"):
        kind = "complaints"
    if kind not in REGISTRY_FIELDS:
        return {"error": f"unknown registry '{kind}'", "allowed": sorted(REGISTRY_FIELDS)}
    key, cols = REGISTRY_FIELDS[kind]
    df = _frame(cache, key)
    if df is None:
        return {"error": f"{kind} registry not loaded"}
    m = pd.Series(True, index=df.index)
    if query:
        q = re.sub(r"[\s-]", "", str(query).lower())
        hay = df[[c for c in cols if c in df]].astype(str).apply(lambda s: s.str.lower().str.replace(r"[\s-]", "", regex=True))
        m &= hay.apply(lambda s: s.str.contains(re.escape(q), na=False)).any(axis=1)
    if district:
        dcols = [c for c in ("district", "theft_district", "last_seen_district") if c in df]
        if dcols:
            m &= df[dcols[0]].astype(str).str.lower().str.contains(str(district).lower(), na=False)
    if status:
        scols = [c for c in ("recovery_status", "found_status", "conviction_status", "resolution_status") if c in df]
        if scols:
            col = df[scols[0]].astype(str).str.lower()
            st = str(status).lower()
            exact = col == st
            m &= exact if exact.any() else col.str.contains(st, na=False)
    sel = df[m]
    out = {"registry": kind, "query": query, "district": district, "status": status,
           "total_records": int(len(df)), "total_matching": int(len(sel))}
    for sc in ("recovery_status", "found_status", "conviction_status", "resolution_status", "emergency_type"):
        if sc in sel and len(sel):
            out[f"by_{sc}"] = sel[sc].astype(str).value_counts().head(6).to_dict()
    if kind == "cybercrime" and len(sel):
        out["total_fraud_rs"] = int(pd.to_numeric(sel["fraud_amount"], errors="coerce").fillna(0).sum())
        out["total_victims"] = int(pd.to_numeric(sel["victim_count"], errors="coerce").fillna(0).sum())
    if kind == "complaints" and len(sel):
        out["avg_response_min"] = round(float(pd.to_numeric(sel["response_time_min"], errors="coerce").mean()), 1)
    out["records"] = [{c: _clean(r.get(c)) for c in cols if c in r}
                      for r in sel.head(max(1, min(int(limit or 8), 20))).to_dict("records")]
    return out


def tool_network(cache, entity=None, target=None, **_):
    idx = _index(cache)
    G = _graph()
    if idx is None or G is None:
        return {"error": "link graph not loaded"}

    def _node(ref):
        if ref is None:
            return None
        s = str(ref)
        for cand in (resolve_suspect(s, idx) if re.search(r"sus", s, re.I) or not RE_CRIMENO.search(s) else None,
                     resolve_case(s, idx), resolve_officer(s, idx) if "kgid" in s.lower() else None):
            if cand and G.has_node(cand):
                return cand
        hits = [i for k, i in idx.names.get(_norm_key(s), []) if k == "offender"]
        if len(hits) == 1 and G.has_node(hits[0]):
            return hits[0]
        return s if G.has_node(s) else None

    def _label(n):
        d = G.nodes[n]
        t = d.get("type", "Node")
        if t == "Suspect":
            return {"id": n, "type": t, "name": idx.criminals.get(n, {}).get("name") or d.get("name")}
        if t == "Case":
            return {"id": n, "type": t, "crime_type": d.get("crime_type"), "district": d.get("district")}
        return {"id": n, "type": t, **{k: v for k, v in d.items() if k in ("name", "label")}}

    a = _node(entity)
    if not a:
        return {"error": f"'{entity}' is not a node in the link graph"}
    if target:
        b = _node(target)
        if not b:
            return {"error": f"'{target}' is not a node in the link graph"}
        import networkx as nx
        # An investigating officer is not a link between two people: one IO handles
        # dozens of unrelated files. The Network page leaves officers out of paths by
        # default (network_intel._paths); the copilot follows the same rule.
        H = nx.subgraph_view(G, filter_node=lambda n: n in (a, b) or G.nodes[n].get("type") != "Officer")
        try:
            path = nx.shortest_path(H, a, b)
        except nx.NetworkXNoPath:
            return {"from": a, "to": b, "connected": False,
                    "note": "No chain of shared cases, identifiers or associates links them."}
        except Exception as e:
            return {"error": str(e)}
        return {"from": a, "to": b, "connected": True, "hops": len(path) - 1,
                "path": [_label(n) for n in path[:12]]}
    nbrs = list(G.neighbors(a))
    by_type = {}
    for n in nbrs:
        by_type.setdefault(G.nodes[n].get("type", "Node"), []).append(n)
    out = {"entity": _label(a), "degree": len(nbrs),
           "neighbours_by_type": {t: len(v) for t, v in by_type.items()},
           "neighbours": {t: [_label(n) for n in v[:8]] for t, v in by_type.items()}}
    if G.nodes[a].get("type") == "Suspect":
        two = set()
        for c in by_type.get("Case", []):
            two.update(x for x in G.neighbors(c) if x != a and G.nodes[x].get("type") == "Suspect")
        out["second_degree_suspects"] = len(two)
    return out


def tool_insight(cache, topic="overview", district=None, crime_type=None, **_):
    topic = str(topic or "overview").lower()
    idx = _index(cache)
    d = detect_district(district, idx) if (district and idx) else None
    if topic in ("forecast", "prediction", "next_week"):
        fc = cache.get("forecast") or {}
        o = fc.get("overall") or {}
        out = {"model": "RidgeCV + Fourier seasonality (weekly counts)",
               "statewide": {k: o.get(k) for k in ("next_week", "trend", "change_pct", "avg_weekly")},
               "forecast_weeks": fc.get("future_labels"),
               "statewide_next_8_weeks": o.get("forecast"),
               "interval_coverage": fc.get("interval_coverage_realised")}
        bd = fc.get("by_district") or {}
        if d and d in bd:
            out["district"] = {d: {k: bd[d].get(k) for k in ("next_week", "trend", "change_pct", "avg_weekly")}}
        rising = sorted([(k, v.get("change_pct", 0)) for k, v in bd.items() if v.get("trend") == "rising"],
                        key=lambda x: -x[1])[:5]
        out["districts_rising"] = dict(rising)
        bc = fc.get("by_crime_type") or {}
        ct = detect_crime(crime_type, idx) if (crime_type and idx) else None
        if ct and ct in bc:
            out["crime_type"] = {ct: {k: bc[ct].get(k) for k in ("next_week", "trend", "change_pct", "avg_weekly")}}
        out["by_crime_type"] = {k: {"next_week": v.get("next_week"), "trend": v.get("trend")} for k, v in bc.items()}
        return out
    if topic in ("hotspots", "hotspot", "clusters", "zones"):
        cl = cache.get("crime_clusters") or []
        if d:
            cl = [c for c in cl if c.get("district") == d]
        return {"dbscan_zones": len(cache.get("crime_clusters") or []), "district_filter": d,
                "zones": [{k: c.get(k) for k in ("id", "district", "cases", "dominant_crime", "radius_km",
                                                 "open_cases", "peak_time", "avg_severity")} for c in cl[:6]],
                "kde_predicted_hotspots": [{k: h.get(k) for k in ("lat", "lng", "intensity")}
                                           for h in (cache.get("predicted_hotspots") or [])[:5]]}
    if topic in ("trends", "trend", "alerts", "spikes"):
        al = cache.get("trend_alerts") or []
        if d:
            al = [a for a in al if a.get("district") == d]
        return {"method": "last 30 days vs historical baseline", "alerts": al[:8]}
    if topic in ("anomalies", "anomaly"):
        an = cache.get("anomalies") or []
        return {"method": "Isolation Forest", "count": len(an), "anomalies": an[:8]}
    if topic in ("recidivism", "risk", "watchlist"):
        rec = cache.get("recidivism") or {}
        return {"model": "calibrated re-offence within 365 days", "bands": rec.get("bands"),
                "population": rec.get("population"), "index_meaning": rec.get("index_meaning"),
                "top_by_id": [{"criminal_id": t.get("criminal_id"), "risk_index": t.get("risk_index"),
                               "band": t.get("band"), "drivers": t.get("drivers")} for t in (rec.get("top") or [])[:8]],
                "policy": "names are withheld from ranked lists; open the registry for the gated view"}
    if topic in ("pendency", "backlog", "overdue"):
        p = cache.get("pendency") or {}
        bd = p.get("by_district") or []
        if d:
            bd = [x for x in bd if x.get("name") == d]
        cases = p.get("cases") or []
        if d:
            cases = [c for c in cases if c.get("district") == d]
        return {"summary": p.get("summary"), "by_district": bd[:8],
                "longest_expected_delay": [{k: c.get(k) for k in ("case_id", "district", "crime_type", "days_open",
                                                                  "expected_additional_days")}
                                           for c in sorted(cases, key=lambda c: -(c.get("expected_additional_days") or 0))[:5]]}
    if topic in ("dispatch", "patrol", "deployment", "resources"):
        rows = cache.get("dispatch") or []
        if d:
            rows = [r for r in rows if r.get("district") == d]
        return {"method": rows[0].get("risk_method") if rows else None,
                "districts": [{k: r.get(k) for k in ("district", "risk_score", "risk_level", "recommended_officers",
                                                     "open_cases", "recent_count", "high_severity_cases")} for r in rows[:8]],
                "total_recommended_officers": int(sum(r.get("recommended_officers", 0) for r in (cache.get("dispatch") or [])))}
    if topic in ("socioeconomic", "socio", "demographics", "census"):
        s = cache.get("socioeconomic") or {}
        out = {"insights": s.get("insights"), "correlations": s.get("correlations")}
        if d:
            out["district"] = next((x for x in (s.get("districts") or []) if x.get("district") == d), None)
        return out
    if topic in ("operational_index", "risk_index", "district_index"):
        oi = cache.get("operational_index") or {}
        rows = oi.get("districts") or []
        if d:
            rows = [r for r in rows if r.get("district") == d]
        return {"method": oi.get("method"), "caveat": oi.get("caveat"),
                "districts": [{k: r.get(k) for k in ("district", "index", "band", "raw")} for r in rows[:8]]}
    if topic in ("lifecycle", "funnel"):
        lc = cache.get("lifecycle") or {}
        return {"cohort": lc.get("cohort"), "note": lc.get("note")}
    if topic in ("predicted_links", "links", "alliances"):
        return {"method": "Adamic-Adar on the co-offender graph",
                "links": [{k: l.get(k) for k in ("a", "a_name", "b", "b_name", "score", "common_associates",
                                                 "same_gang", "gang")} for l in (cache.get("predicted_links") or [])[:8]]}
    if topic in ("syndicates", "gangs", "communities"):
        syn = sorted(cache.get("syndicate_communities") or [], key=lambda s: -(s.get("size") or 0))
        return {"method": "Louvain on repeat co-offending", "count": len(syn),
                "largest": [{"syndicate_id": s.get("syndicate_id"), "size": s.get("size"),
                             "risk_level": s.get("risk_level"),
                             "member_ids": [m.get("id") for m in (s.get("members") or [])][:10]} for s in syn[:6]]}
    if topic in ("cyber_rings", "cyber", "fraud_rings"):
        ext = cache.get("extended") or {}
        return {"cyber_cases": ext.get("cyber_cases"), "total_fraud_rs": ext.get("cyber_total_fraud"),
                "victims": ext.get("cyber_victims"),
                "rings": [{k: r.get(k) for k in ("identifier", "linked_cases", "total_fraud")} | {"sample_cases": (r.get("cases") or [])[:4]}
                          for r in (ext.get("cyber_rings") or [])[:6]]}
    if topic in ("courts", "conviction", "trials"):
        c = _frame(cache, "courts")
        if c is None:
            return {"error": "court registry not loaded"}
        vc = c["conviction_status"].value_counts().to_dict()
        dec = vc.get("Convicted", 0) + vc.get("Acquitted", 0)
        return {"court_records": int(len(c)), "by_outcome": vc,
                "conviction_rate_of_decided_pct": round(vc.get("Convicted", 0) / dec * 100, 1) if dec else None,
                "by_court": c["court_name"].value_counts().head(8).to_dict()}
    # overview
    f = idx.firs if idx else _frame(cache, "firs")
    ext = cache.get("extended") or {}
    out = {"total_firs": int(len(f)), "by_status": f["status"].value_counts().to_dict(),
           "top_crime_types": f["crime_type"].value_counts().head(5).to_dict(),
           "top_districts": f["district"].value_counts().head(5).to_dict(),
           "offenders": len(idx.criminals) if idx else None,
           "fatalities": int(f["fatalities"].fillna(0).sum()), "injured": int(f["injured"].fillna(0).sum()),
           "rowdy_sheeters": ext.get("rowdy_sheeters"), "history_sheeters": ext.get("history_sheeters"),
           "active_warrants": ext.get("active_warrants"), "stolen_vehicles": ext.get("vehicles_total"),
           "vehicle_recovery_rate_pct": ext.get("vehicle_recovery_rate"), "officers": ext.get("officers_total")}
    if idx is not None:
        ts = f["_ts"].dropna()
        if len(ts):
            out["date_range"] = [f"{ts.min():%Y-%m-%d}", f"{ts.max():%Y-%m-%d}"]
    return out


def tool_model_info(cache, model=None, **_):
    import agent_answers
    reg = agent_answers._registry() or {}
    models = reg.get("models") or {}
    if not models:
        return {"error": "model registry unavailable"}
    pick = None
    if model:
        q = str(model).lower().replace(" ", "_")
        for name in models:
            if name in q or q in name or name.split("_")[0] in q:
                pick = name
                break
    if not pick:
        return {"models": {n: {"headline": m.get("headline"),
                               "key_metrics": {k: (m.get("metrics") or {}).get(k) for k in
                                               ("accuracy", "roc_auc", "mase", "c_index", "silhouette", "f1_macro")
                                               if (m.get("metrics") or {}).get(k) is not None}}
                           for n, m in models.items()},
                "source": "artifacts/model_registry.json"}
    m = models[pick]
    mt = m.get("metrics") or {}
    return {"model": pick, "headline": m.get("headline"),
            "metrics": {k: v for k, v in mt.items() if isinstance(v, (int, float))},
            "baselines": (m.get("baselines") or [])[:5], "notes": (m.get("notes") or [])[:4],
            "card": m.get("card") or m.get("model_card"), "source": "artifacts/model_registry.json"}


def tool_project_knowledge(cache, topic="overview", **_):
    """Curated summary plus the best passages from the repository's own docs, code and pages."""
    matches = copilot_knowledge.search(topic, k=6)
    seen, uniq = set(), []
    for m in matches:
        if m["source"] != "curated" and m["title"] not in seen:
            seen.add(m["title"])
            uniq.append({"title": m["title"], "source": m["source"], "kind": m["kind"], "text": m["text"][:900]})
    return {"sections": copilot_knowledge.lookup(topic), "matches": uniq[:4],
            "best_is_curated": bool(matches) and matches[0]["source"] == "curated"}


def tool_describe_platform(cache, aspect="summary", **_):
    """Live facts about the running system: routes, loaded tables, pages, models, sources."""
    return copilot_knowledge.describe_live(aspect, cache)


def tool_situation(cache, district=None, **_):
    """What is happening now: recent volume and mix, alerts, forecast, live cameras."""
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    f = idx.firs
    d = detect_district(district, idx) if district else None
    sel = f[f["district"] == d] if d else f
    end = f["_ts"].max()
    day = pd.Timedelta(days=1)
    last7 = sel[sel["_ts"] > end - 7 * day]
    prev7 = sel[(sel["_ts"] <= end - 7 * day) & (sel["_ts"] > end - 14 * day)]
    last30 = sel[sel["_ts"] > end - 30 * day]
    out = {"scope": d or cache.get("_scope_label") or "Karnataka", "records_up_to": f"{end:%Y-%m-%d}",
           "last_7_days": int(len(last7)), "previous_7_days": int(len(prev7)),
           "last_30_days": int(len(last30)),
           "last_30_by_crime": last30["crime_type"].value_counts().head(5).to_dict(),
           "last_30_killed": int(last30["fatalities"].fillna(0).sum()),
           "last_30_injured": int(last30["injured"].fillna(0).sum()),
           "open_cases_total": int((sel["status"] == "Under Investigation").sum()),
           "latest_cases": [{"case_id": r["case_id"], "crime_type": r["crime_type"], "district": r["district"],
                             "station": r.get("police_station"), "registered": str(r["timestamp"])[:16],
                             "status": r["status"], "severity": _clean(r["severity_score"])}
                            for _, r in sel.sort_values("_ts", ascending=False).head(4).iterrows()]}
    if not d:
        out["busiest_districts_30d"] = last30["district"].value_counts().head(4).to_dict()
    else:
        out["top_stations_30d"] = last30["police_station"].value_counts().head(3).to_dict()
    al = [a for a in (cache.get("trend_alerts") or []) if not d or a.get("district") == d]
    out["trend_alerts"] = al[:4]
    fc = cache.get("forecast") or {}
    src = (fc.get("by_district") or {}).get(d) if d else fc.get("overall")
    if src:
        out["forecast_next_week"] = {k: src.get(k) for k in ("next_week", "trend", "change_pct")}
    disp = next((r for r in (cache.get("dispatch") or []) if r.get("district") == d), None) if d else None
    if disp:
        out["patrol"] = {k: disp.get(k) for k in ("risk_score", "risk_level", "recommended_officers")}
    try:
        import surveillance
        a = surveillance.alerts(cache, None, district=cache.get("_scope_district") or d, limit=4)
        cams = surveillance.cameras(cache, district=d)
        out["live_cameras"] = {"cameras": len(cams["cameras"]),
                               "online": sum(c["status"] == "online" for c in cams["cameras"]),
                               "alerts_24h": a["counts"], "latest_alerts": [
                                   {"time": x["time"][11:16] + " UTC", "camera": x["camera_name"], "priority": x["priority"],
                                    "what": (f"{x['face_match']['person']['name']} ({x['face_match']['suspect_id']}) — "
                                             f"{round(x['face_match']['similarity'] * 100)}% face match"
                                             if x.get("face_match") else f"plate {x.get('plate_read')} → stolen "
                                             f"{(x.get('plate_match') or {}).get('make_model')}"),
                                    "reasons": x["reasons"]} for x in a["alerts"][:3]]}
    except Exception as e:
        LOG.warning("situation: camera summary unavailable (%s)", e)
    return out


def tool_sightings(cache, person_ref=None, plate=None, district=None, **_):
    """CCTV / ANPR: where a person was last seen, reads of a plate, or live alerts in an area."""
    import surveillance
    idx = _index(cache)
    if person_ref:
        sid = resolve_suspect(person_ref, idx) if re.search(r"\d", str(person_ref)) else None
        if not sid:
            hits = [i for k, i in idx.names.get(_norm_key(person_ref), []) if k == "offender"]
            sid = hits[0] if len(hits) == 1 else None
        if not sid:
            return {"error": f"'{person_ref}' does not resolve to one registered offender"}
        t = surveillance.track(cache, None, sid)
        return {"suspect_id": sid, "name": (t.get("person") or {}).get("name"),
                "sightings_24h": len(t["sightings"]),
                "sightings": [{"time": x["time"], "camera": x["camera_name"], "district": x["district"],
                               "similarity": x["face_match"]["similarity"], "band": x["face_match"]["band"],
                               "status": x["status"]} for x in t["sightings"][-8:]],
                "note": t["note"]}
    if plate:
        r = surveillance.lookup_plate(cache, plate)
        return {"query": plate, "registry_match": r["registry_match"],
                "recent_reads": [{"time": x["time"], "camera": x["camera_name"], "read": x["plate_read"]}
                                 for x in r["recent_reads"]]}
    d = detect_district(district, idx) if district else None
    # A district or station officer's copilot reads their own district's alerts only.
    a = surveillance.alerts(cache, None, district=cache.get("_scope_district") or d, limit=6)
    return {"scope": d or cache.get("_scope_label") or "Karnataka", "counts_24h": a["counts"],
            "alerts": [{"time": x["time"], "camera": x["camera_name"], "district": x["district"],
                        "priority": x["priority"], "reasons": x["reasons"], "status": x["status"],
                        "person": (x.get("face_match") or {}).get("suspect_id"),
                        "name": ((x.get("face_match") or {}).get("person") or {}).get("name"),
                        "plate": x.get("plate_read")} for x in a["alerts"]],
            "policy": a["policy"]}


def tool_news(cache, district=None, crime_type=None, topic=None, query=None, lang="en", limit=6, **_):
    """Live News: public Karnataka police and public-safety headlines, each tied to our FIR counts.

    Headlines are public reporting, not records: the tool says which sources answered and
    never fills an empty feed. `news` is imported here, not at module level, because it
    imports this module for the district aliases and crime vocabulary it shares.
    """
    import news
    idx = _index(cache)
    d = None
    if district:
        d = (detect_district(district, idx) if idx is not None else None) or news.resolve_district(district)
        if not d:
            return {"error": f"'{district}' is not a Karnataka district I can match"}
    ct = None
    cat = news.resolve_category(topic) if topic else None
    if crime_type:
        ct = detect_crime(crime_type, idx) if idx is not None else None
        if not ct and idx is not None and crime_type in idx.crime_types:
            ct = crime_type
        if not ct:
            cat = cat or news.resolve_category(crime_type)     # "sexual assault" -> women & child safety
    return news.brief(cache, district=d, category=cat, crime_type=ct, q=query or None,
                      lang="kn" if lang == "kn" else "en", limit=limit)


def _bound(mod, cache):
    """trend_forecast / temporal read through a configured getter; make sure it is this cache."""
    if (mod._cache_getter() or {}).get("firs") is not cache.get("firs"):
        mod.configure(lambda: cache)
    return mod


def tool_annual_forecast(cache, district=None, crime_type=None, year=None, **_):
    """Annual FIR forecast for a district (or the state) and year, with its backtested accuracy."""
    import trend_forecast as T
    _bound(T, cache)
    idx = _index(cache)
    d = (detect_district(district, idx) if (district and idx is not None) else None) or district
    ct = (detect_crime(crime_type, idx) if (crime_type and idx is not None) else None) or crime_type
    try:
        f = T.forecast(d, year, ct)
        card = T.meta(ct)["model"]["backtest"].get("1", {})
    except T.ForecastError as e:
        return {"error": str(e)}
    return {
        "unit": f["unit"], "year": f["year"], "crime_type": f["crime_type"] or "all crime types",
        "kind": f["kind"], "predicted": f["predicted"], "interval_80": f["interval"],
        "last_year": f["last_year"], "last_count": f["last_count"], "change_vs_last_pct": f["change_vs_last_pct"],
        "change_exceeds_noise_band": f["change_exceeds_band"], "actual": f["actual"],
        "inside_interval": f["inside_interval"], "per_lakh": f["per_lakh"], "explanation": f["explanation"],
        "top_crime_types": (f.get("by_crime_type") or [])[:4],
        "backtest": {"mae": (card.get("model") or {}).get("mae"), "skill_vs_last_year": card.get("skill_vs_last_year"),
                     "verdict_vs_best_baseline": (card.get("significance_vs_best_baseline") or {}).get("verdict"),
                     "best_baseline": card.get("best_baseline")},
        "note": f["note"], "page": "/analytics#trend-forecast",
    }


def tool_time_pattern(cache, district=None, crime_type=None, **_):
    """When FIRs happen: hour x weekday peaks, with a test of whether the pattern beats chance."""
    import temporal as TM
    _bound(TM, cache)
    idx = _index(cache)
    d = (detect_district(district, idx) if (district and idx is not None) else None) or district
    ct = (detect_crime(crime_type, idx) if (crime_type and idx is not None) else None) or crime_type
    body, status = TM.temporal(d, ct)
    if status != 200:
        return {"error": body.get("error")}
    return {k: body[k] for k in ("filters", "firs", "peak_cell", "peak_window", "busiest_weekday", "busiest_hour",
                                 "hot_cells", "note")} | {
        "pattern_beyond_chance": any(t.get("pattern") for t in body["tests"].values() if t),
        "page": "/overview#time-pattern"}


def _inv(cache):
    """The investigation workbench, bound to this cache."""
    import investigation as INV
    if (INV._cache_getter() or {}).get("firs") is not cache.get("firs"):
        INV.configure(lambda: cache)
    return INV, INV.index()


def _cites(cs, n=3):
    return [c["id"] for c in (cs or [])[:n]]


def tool_investigate(cache, mode="case_check", case_ref=None, person_ref=None, series_id=None, district=None,
                     crime_type=None, station=None, act=None, section=None, date=None, **_):
    """Investigation workbench: record checks, series, geographic profile, statutes, under-reporting, IO choice."""
    import statutes as ST
    INV, ix = _inv(cache)
    idx = _index(cache)
    if ix is None or idx is None:
        return {"error": "corpus not loaded"}
    d = (detect_district(district, idx) if district else None) or district
    ct = (detect_crime(crime_type, idx) if crime_type else None) or crime_type
    if mode == "case_check":
        cid = resolve_case(case_ref, idx)
        v = INV.case_view(ix, cid) if cid else None
        if not v:
            return {"error": f"No case matching '{case_ref}' exists in the corpus."}
        return {"case_id": v["case_id"], "crime_type": v["crime_type"], "district": v["district"], "station": v["station"],
                "incident": v["incident"], "status": v["status"],
                "integrity": [{"code": f["code"], "severity": f["severity"], "detail": f["detail"], "cites": _cites(f["cites"])}
                              for f in v["integrity"][:8]],
                "questions_for_io": [q["question"] for q in v["questions"][:6]],
                "hypotheses": [{"id": h["id"], "name": h["name"], "named_on_fir": h["named"], "inconsistent": h["inconsistent"],
                                "consistent": h["consistent"],
                                "for": [c["note"] for c in h["cells"].values() if c["verdict"] == "C"][:3],
                                "against": [c["note"] for c in h["cells"].values() if c["verdict"] == "I"][:2]}
                               for h in v["hypotheses"]["hypotheses"][:5]],
                "hypothesis_method": v["hypotheses"]["method"], "excluded_factors": v["hypotheses"]["excluded_factors"],
                "statute": {"code_in_force": v["statute"]["code_in_force"],
                            "findings": [f["message"] for f in v["statute"]["findings"]]},
                "series": [{"id": x["id"], "kind": x["kind"], "size": x["size"], "stations": x["stations"]} for x in v["series"]],
                "page": f"/case?id={v['case_id']}"}
    if mode == "integrity":
        r = INV.integrity_summary(ix, district=d, limit=5)
        return {"district": d, "firs_checked": r["firs_checked"], "firs_flagged": r["firs_flagged"],
                "checks": [{"title": c["title"], "count": c["count"]} for c in r["checks"] if c["count"]],
                "worst_firs": [{"case_id": c["case_id"], "flags": [f["title"] for f in c["flags"]]} for c in r["cases"]],
                "page": "/investigate?tab=integrity"}
    if mode == "series":
        if series_id:
            sd = INV.series_detail(ix, series_id)
            if not sd:
                return {"error": f"No series {series_id}."}
            g = sd["geoprofile"]
            return {"id": sd["id"], "kind": sd["kind"], "crime_type": sd["crime_type"], "size": sd["size"],
                    "stations": sd["stations"], "districts": sd["districts"], "first": sd["first"], "last": sd["last"],
                    "signature": sd["signature"], "person": sd["person"],
                    "members": [{"case_id": m["case_id"], "date": m["date"], "station": m["station"]} for m in sd["members"][:8]],
                    "shared_accused": sd["shared_accused"][:5],
                    "geoprofile": {"applicable": g["applicable"], "peak": g.get("peak"), "search_area_km2": g.get("search_area_km2"),
                                   "reason": g.get("reason")}, "page": f"/investigate?series={sd['id']}"}
        r = INV.series_list(ix, kind=None, district=d, crime_type=ct, limit=6)
        return {"filters": {"district": d, "crime_type": ct}, "total": r["total"], "by_kind": r["by_kind"],
                "criteria": r["criteria"], "series": r["series"], "page": "/investigate?tab=series"}
    if mode == "geo_profile":
        if series_id:
            sd = INV.series_detail(ix, series_id)
            g = sd["geoprofile"] if sd else None
            subject = series_id
        else:
            ref = str(person_ref or "").strip()
            sid = resolve_suspect(ref, idx) if re.search(r"\d", ref) else None
            if not sid:
                offs = [i for k, i in idx.names.get(_norm_key(ref), []) if k == "offender"]
                sid = offs[0] if len(offs) == 1 else None
            t = INV.person_timeline(ix, sid) if sid else None
            g = t["geoprofile"] if t else None
            subject = sid
        if not g:
            return {"error": "Name a registry person (SUS-id) or a series id."}
        return {"subject": subject, **{k: g.get(k) for k in ("applicable", "reason", "peak", "anchors", "search_area_km2",
                                                              "buffer_km", "sites_used", "excluded", "note", "method")}}
    if mode == "statute":
        row = ST.lookup(act or "IPC", section)
        return {"act": act or "IPC", "section": section, "crosswalk": row,
                "code_in_force": ST.code_for(date) if date else None,
                "findings": [f["message"] for f in ST.check([(act or "IPC", section)], date)] if date and section else [],
                "commencement": ST.BNS_COMMENCEMENT.isoformat(), "source": ST.SOURCE}
    if mode == "underreporting":
        r = INV.underreporting(cache, ix)
        rows = [x for x in r["districts"] if not d or x["district"] == d]
        return {"model": r["model"], "state_cognizable_fir_rate": r["state_cognizable_fir_rate"],
                "flagged": [{"district": x["district"], "z": x["z"], "flags": x["flags"]} for x in rows if x["flags"]][:8],
                "district": rows[0] if d and rows else None, "note": r["note"], "page": "/investigate?tab=underreporting"}
    if mode == "assign_io":
        st = detect_station(station or "", idx) if station else None
        units = cache.get("Unit")
        uid = None
        if st is not None and units is not None:
            hit = units[units["UnitName"] == st]
            uid = int(hit["UnitID"].iloc[0]) if len(hit) else None
        r = INV.recommend_io(cache, ix, uid, ct)
        return {"station": st, "crime_type": ct, "wanted_specialisation": r["wanted_specialisation"],
                "officers": [{"name": o["name"], "kgid": o["kgid"], "rank": o["rank"], "score": o["score"], "reasons": o["reasons"]}
                             for o in r["officers"]], "excluded_factors": r["excluded_factors"], "note": r["note"]}
    return {"error": f"unknown mode {mode}"}


# Tools that read one specific record; an answer built from them outranks a generic one.
TIME_CUES = ("what time", "which time", "what hour", "which hour", "time of day", "times of day", "peak hour",
             "busiest hour", "busiest time", "busiest day", "which day", "what day of", "day of the week",
             "when do ", "when does ", "at night or", "hour of day", "weekday", "weekend")
ANNUAL_CUES = ("next year", "annual forecast", "yearly forecast", "forecast for 20", "prediction for 20",
               "crimes in 2027", "crimes in 2028", "trend forecast")

def is_analytic_question(text):
    """A when/forecast question about a crime type answers with numbers, never a case list."""
    low = " " + (text or "").lower() + " "
    if any(c in low for c in TIME_CUES) or any(c in low for c in ANNUAL_CUES):
        return True
    if re.search(r"\b(link|links|alliance|syndicate|gang|model|accura\w*|recidiv\w*)\b", low):
        return False
    return bool(re.search(r"\b(forecast|predict(ed|ion)?|projection|projected)\b", low))


ENTITY_TOOLS = {"get_case", "get_person", "get_officer", "network", "search_registry", "sightings"}

NAV_ACTIONS = {"open_case", "open_suspect", "show_heatmap", "show_network", "show_dispatch",
               "show_analytics", "show_models", "recent_cases", "show_registry", "show_anomalies",
               "show_surveillance"}


TOOLS = {
    "get_case": (tool_get_case, "Full dossier of ONE FIR/case: narrative, accused (with ids, priors, flags), victim, complainant, investigating officer, arrests, chargesheet, court stage, vehicles, cyber identifiers, CCTV, Dial-112, pendency model, similar cases.",
                 {"case_ref": {"type": "string", "description": "18-digit CrimeNo, FIR-<CaseMasterID>, CaseMasterID or CaseNo"}}, ["case_ref"]),
    "get_person": (tool_get_person, "Profile of ONE offender by SUS-id or full name: registry flags (rowdy-sheeter, history-sheet, warrant, gang), every case they are named in, crime types, districts, court outcomes, co-offenders, recidivism index and drivers, syndicates, predicted links. Also reports if the name matches officers, missing persons or victims.",
                   {"person_ref": {"type": "string", "description": "SUS-#### or a full name"}}, ["person_ref"]),
    "search_cases": (tool_search_cases, "Filter FIRs and return the exact count, status/crime/district breakdowns, casualty totals, date range and the top matching cases.",
                     {"district": {"type": "string"}, "crime_type": {"type": "string"},
                      "status": {"type": "string", "description": "Under Investigation | Charge Sheeted | Closed"},
                      "police_station": {"type": "string"}, "year": {"type": "integer"},
                      "date_from": {"type": "string", "description": "YYYY-MM-DD"}, "date_to": {"type": "string"},
                      "min_severity": {"type": "number"}, "has_fatalities": {"type": "boolean"},
                      "weapon": {"type": "string"}, "vehicle": {"type": "string"}, "time_of_day": {"type": "string"},
                      "target_type": {"type": "string"}, "keyword": {"type": "string", "description": "word to find in FIR narratives"},
                      "sort_by": {"type": "string", "enum": ["recent", "oldest", "severity", "fatalities"]},
                      "limit": {"type": "integer"}}, []),
    "aggregate": (tool_aggregate, "Group FIRs and rank: counts, fatalities, injured or average severity by district, crime_type, status, police_station, time_of_day, weapon_used, vehicle_used, target_type, year, month, weekday or victim_gender — with optional filters.",
                  {"group_by": {"type": "string"}, "metric": {"type": "string", "enum": ["count", "fatalities", "injured", "avg_severity"]},
                   "top_n": {"type": "integer"}, "order": {"type": "string", "enum": ["desc", "asc", "chronological"]},
                   "district": {"type": "string"}, "crime_type": {"type": "string"}, "status": {"type": "string"},
                   "year": {"type": "integer"}, "police_station": {"type": "string"}}, ["group_by"]),
    "search_people": (tool_search_people, "Filter the offender registry (9,000 people): by name fragment, gang, risk level, rowdy-sheeter, history-sheeter, active warrant, minimum priors, or by district/crime type of their cases. Returns counts, breakdowns and top people by priors.",
                      {"name": {"type": "string"}, "gang": {"type": "string"}, "risk_level": {"type": "string"},
                       "rowdy_sheeter": {"type": "boolean"}, "history_sheeter": {"type": "boolean"},
                       "active_warrant": {"type": "boolean"}, "min_priors": {"type": "integer"},
                       "district": {"type": "string"}, "crime_type": {"type": "string"}, "limit": {"type": "integer"}}, []),
    "get_officer": (tool_get_officer, "One police officer by KGID or name: rank, station, specialization, closure rate, commendations and the cases they investigate.",
                    {"officer_ref": {"type": "string"}}, ["officer_ref"]),
    "search_officers": (tool_search_officers, "List/rank officers by district, rank or specialization, sorted by closure_rate, cases_assigned or commendations. order=asc lists the WORST / lowest first (who is doing worst, weakest, underperforming); desc lists the best first. Results carry the state average closure rate for context.",
                        {"district": {"type": "string"}, "rank": {"type": "string"}, "specialization": {"type": "string"},
                         "sort_by": {"type": "string", "enum": ["closure_rate", "cases_assigned", "commendations"]},
                         "order": {"type": "string", "enum": ["desc", "asc"], "description": "asc = lowest/worst first"},
                         "min_cases": {"type": "integer", "description": "ignore officers with fewer cases (default 15 for worst-first)"},
                         "limit": {"type": "integer"}}, []),
    "search_registry": (tool_search_registry, "Search a side registry: vehicles (stolen vehicles), cybercrime (UPI/bank/phone/IP/URL/wallet identifiers), missing (missing persons), courts (trials), complaints (Dial-112 calls), cctv. Free-text query matches any field.",
                        {"kind": {"type": "string", "enum": sorted(REGISTRY_FIELDS)}, "query": {"type": "string"},
                         "district": {"type": "string"}, "status": {"type": "string"}, "limit": {"type": "integer"}}, ["kind"]),
    "network": (tool_network, "Link-graph query. With only `entity`: its neighbours (cases, co-offenders, identifiers) and degree. With `target` too: the shortest chain connecting the two.",
                {"entity": {"type": "string", "description": "SUS-id, case id or name"}, "target": {"type": "string"}}, ["entity"]),
    "insight": (tool_insight, "Platform analytics outputs: overview, forecast, hotspots, trends, anomalies, recidivism, pendency, dispatch, socioeconomic, operational_index, lifecycle, predicted_links, syndicates, cyber_rings, courts. Optional district / crime_type focus.",
                {"topic": {"type": "string"}, "district": {"type": "string"}, "crime_type": {"type": "string"}}, ["topic"]),
    "model_info": (tool_model_info, "Metrics, baselines, notes and model card for the ML models (narrative_classifier, crime_forecast, recidivism_risk, conviction_outcome, case_closure, case_pendency, hotspot_clusters). Omit model for all.",
                   {"model": {"type": "string"}}, []),
    "project_knowledge": (tool_project_knowledge, "Search the platform's own knowledge — every README/GOVERNANCE/DPIA/SECURITY section, backend module docs, API routes and web pages, regenerated from the repository whenever it changes — plus curated summaries and policing vocabulary. Use for ANY question about how IntelHub works, what a page/feature/model/route does, governance, privacy, deployment or terminology. Pass the question as topic.",
                          {"topic": {"type": "string"}}, ["topic"]),
    "describe_platform": (tool_describe_platform, "Read the RUNNING system: aspect=routes (every live API endpoint and the role it needs), tables (every loaded data table with row counts and columns), pages (every screen), models (ML registry), sources (files the knowledge index covers), summary.",
                          {"aspect": {"type": "string", "enum": ["summary", "routes", "tables", "pages", "models", "sources"]}}, ["aspect"]),
    "situation": (tool_situation, "What is going on right now in a district or statewide: last 7/30 days of FIRs and their mix, casualties, open cases, latest cases, trend spikes, next-week forecast, patrol risk and live CCTV/ANPR alerts. Use for open questions like 'what's happening in Bengaluru'.",
                  {"district": {"type": "string"}}, []),
    "sightings": (tool_sightings, "Live CCTV & ANPR network: where a registered person was last seen (face-match sightings, last 24h), reads of a number plate against the stolen-vehicle registry, or the current alert queue for a district.",
                  {"person_ref": {"type": "string"}, "plate": {"type": "string"}, "district": {"type": "string"}}, []),
    "news": (tool_news, "Live News: recent PUBLIC news headlines about policing and public safety in Karnataka (GDELT and Google News, plus NewsAPI/GNews when configured), tagged by category, district and crime type, with IntelHub's own FIR counts for the district/crime asked about. Use for 'latest news in Bengaluru', 'what's in the news about cyber fraud', 'any headlines on protests in Belagavi'. Headlines are public reporting, not verified by KSP and not records — never treat one as evidence about a case or person.",
             {"district": {"type": "string"}, "crime_type": {"type": "string"},
              "topic": {"type": "string", "description": "crime | cybercrime | narcotics | traffic | women_child | law_order | policing | courts"},
              "query": {"type": "string", "description": "words that must all appear in the headline or summary"},
              "lang": {"type": "string", "enum": ["en", "kn"]}, "limit": {"type": "integer"}}, []),
    "annual_forecast": (tool_annual_forecast, "Annual FIR forecast for a district or statewide for a year (default next year), optionally one crime type: point, 80% range, change vs last year, the trend used, and the model's backtested accuracy against 'same as last year'. A year already in the data is answered as a backtest (predicted from earlier years only, with the actual). Use for 'how many crimes next year in Belagavi', 'forecast chain snatching 2027'.",
                        {"district": {"type": "string"}, "crime_type": {"type": "string"}, "year": {"type": "integer"}}, []),
    "investigate": (tool_investigate, "Investigation workbench. mode=case_check (one FIR: record integrity and cross-source contradictions with cited record ids, questions for the IO, competing hypotheses ranked Heuer-style, IPC/BNS statute check, series it belongs to); integrity (statewide or district data-quality counts); series (cross-station crime series linked by MO or a shared accused; series_id for one); geo_profile (Rossmo search area for a person_ref or series_id); statute (IPC<->BNS equivalent for act+section, with date for the code in force); underreporting (districts recording far less than peers, Dial-112 calls not converted to FIRs); assign_io (rank officers for a station + crime_type).",
                    {"mode": {"type": "string", "enum": ["case_check", "integrity", "series", "geo_profile", "statute", "underreporting", "assign_io"]},
                     "case_ref": {"type": "string"}, "person_ref": {"type": "string"}, "series_id": {"type": "string"},
                     "district": {"type": "string"}, "crime_type": {"type": "string"}, "station": {"type": "string"},
                     "act": {"type": "string"}, "section": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD of the offence"}},
                    ["mode"]),
    "time_pattern": (tool_time_pattern, "When FIRs happen: hour-of-day x day-of-week counts for a district/crime type, the busiest cell, day, hour and 3-hour window, and whether the pattern is beyond chance (chi-square, Holm-corrected). Use for 'what time do chain snatchings happen', 'busiest day for burglary in Mysuru'.",
                     {"district": {"type": "string"}, "crime_type": {"type": "string"}}, []),
    "navigate": (None, "Move the officer's screen. Call at most once, only when showing a screen helps (e.g. after answering about a case, open it).",
                 {"action": {"type": "string", "enum": sorted(NAV_ACTIONS)},
                  "case_id": {"type": "string"}, "suspect_id": {"type": "string"}, "district": {"type": "string"},
                  "crime_type": {"type": "string"},
                  "kind": {"type": "string", "description": "registry tab: criminals|vehicles|missing|cybercrime|officers|courts"}},
                 ["action"]),
}


def _nullable(prop):
    """Models often say "no filter" as null. Groq validates tool calls against the schema
    server-side and rejects the whole turn on a null string, so optional fields allow it."""
    out = dict(prop)
    if isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    if "enum" in out:
        out["enum"] = list(out["enum"]) + [None]
    return out


def tool_specs():
    return [{"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "required": req,
                       "properties": {k: (v if k in req else _nullable(v)) for k, v in props.items()}}}}
        for name, (_fn, desc, props, req) in TOOLS.items()]


def run_tool(name, args, cache):
    spec = TOOLS.get(name)
    if not spec or spec[0] is None:
        return {"error": f"unknown tool {name}"}
    args = {k: v for k, v in (args or {}).items() if v is not None}
    try:
        return spec[0](cache, **args)
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:
        LOG.warning("copilot tool %s failed: %s", name, e)
        return {"error": f"{name} failed: {e}"}


# =============================================================================
# Privacy for egress
# =============================================================================
NAME_KEYS = {"name", "victim_name", "a_name", "b_name", "owner_name", "ComplainantName", "AccusedName"}
DROP_KEYS = {"owner_phone", "family_contact", "caller_phone", "photo_url"}


def mask_for_llm(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in DROP_KEYS:
                continue
            if k in NAME_KEYS and isinstance(v, str):
                out[k] = privacy.mask(v)
            else:
                out[k] = mask_for_llm(v)
        return out
    if isinstance(obj, list):
        return [mask_for_llm(v) for v in obj]
    return obj


def _dump(obj, limit=6000):
    s = json.dumps(obj, default=str, ensure_ascii=False, separators=(",", ":"))
    return s if len(s) <= limit else s[:limit] + "…(truncated)"


# =============================================================================
# Planner — which tools a question needs (used for prefetch and offline answers)
# =============================================================================
TOPIC_CUES = [
    ("forecast", ("forecast", "next week", "next month", "predict", "projection", "expected crime", "coming weeks")),
    ("trends", ("trend", "spike", "spiking", "rising", "surge", "emerging", "increase", "heating up", "going up")),
    ("hotspots", ("hotspot", "hot spot", "cluster", "dbscan", "zone", "danger area", "red zone")),
    ("anomalies", ("anomal", "unusual", "outlier", "isolation forest")),
    ("pendency", ("pendency", "backlog", "overdue", "delay", "90 day", "90-day", "pending chargesheet", "sitting")),
    ("dispatch", ("dispatch", "patrol", "deploy", "deployment", "how many police", "reinforce", "resource")),
    ("socioeconomic", ("socio", "unemploy", "literacy", "income", "poverty", "census", "demograph", "why crime")),
    ("operational_index", ("operational index", "operational risk", "district index", "risk index of district")),
    ("lifecycle", ("lifecycle", "funnel", "attrition", "conversion")),
    ("predicted_links", ("predicted link", "alliance", "adamic", "emerging link", "future link")),
    ("syndicates", ("syndicate", "louvain", "community", "communities", "organised", "organized crime", "gangs")),
    ("cyber_rings", ("cyber ring", "fraud ring", "mule", "rings")),
    ("courts", ("conviction", "acquit", "court", "trial", "verdict")),
    ("recidivism", ("recidiv", "reoffend", "re-offend", "repeat offender", "watchlist", "risk band")),
]


def _cue(low, words):
    return any(w in low for w in words)


SIGHT_CUES = ("last seen", "spotted", "sighting", "seen at", "seen in", "seen near", "whereabout", "where is",
              "where was", "cctv", "camera", "anpr", "plate read", "number plate", "live alert", "surveillance",
              "face match", "facial")
SITUATION_CUES = ("going on", "happening", "situation", "what's up", "whats up", "update", "brief me", "briefing",
                  "news", "right now", " rn", "currently", "today", "this week", "status of crime", "state of",
                  "how is", "how's", "overview", "summary", "pulse", "latest in", "what is new", "what's new")
# Questions about the papers rather than the records. Checked before SITUATION_CUES, which
# also lists "news": that cue reads our FIRs, and "latest news in Bengaluru" deserves the
# headlines — with our own counts beside them, which the news tool attaches.
NEWS_CUE = re.compile(r"\bnews\b|\bheadlines?\b|\bmedia reports?\b|\bin the media\b|\bpress reports?\b|"
                      r"\bnewspapers?\b|\bin the papers\b|ಸುದ್ದಿ")
NEWS_STOPWORDS = {"the", "a", "an", "any", "latest", "recent", "today", "todays", "in", "karnataka", "police",
                  "news", "on", "about", "of", "is", "are", "there", "what", "whats"}


def _news_args(text, low, filters):
    """Arguments for the news tool from a question already resolved to filters."""
    import news
    args = {k: v for k, v in (("district", filters.get("district")), ("crime_type", filters.get("crime_type"))) if v}
    topic = next((c for c in news.classify(low) if c not in ("crime", "policing")), None)
    if topic and news.CRIME_CATEGORY.get(args.get("crime_type")) != topic:
        args["topic"] = topic
    m = re.search(r"\b(?:news|headlines?|reports?)\s+(?:about|on|regarding|of|related to)\s+(.+)", low)
    if m and not args:
        words = [w for w in re.sub(r"[^\w\s-]", " ", m.group(1)).split() if w not in NEWS_STOPWORDS]
        if words:
            args["query"] = " ".join(words[:4])
    if re.search(r"[\u0C80-\u0CFF]", text or ""):
        args["lang"] = "kn"
    return args


def plan(text, idx, ents):
    """Ordered (tool, args) calls that a question needs."""
    low = _kannada_to_english(text or "").lower()
    text = _kannada_to_english(text or "")
    calls = []
    sight = _cue(low, SIGHT_CUES)
    if sight and (ents["suspects"] or ents["vehicles"]):
        return ([("sightings", {"person_ref": p}) for p in ents["suspects"][:2]]
                + [("sightings", {"plate": v}) for v in ents["vehicles"][:2]])
    statute = re.search(r"\b(ipc|bns)\s*(?:section\s*|s\.?\s*)?(\d+[a-z]?(?:\(\w+\))?)", low)
    if statute and _cue(low, ("bns", "equivalent", "replaced", "new section", "corresponding", "convert", "which section")):
        m = re.search(r"\b(20\d\d-\d\d-\d\d)\b", low)
        return [("investigate", {"mode": "statute", "act": statute.group(1).upper(), "section": statute.group(2),
                                 "date": m.group(1) if m else None})]
    if ents["cases"] and _cue(low, ("inconsisten", "contradict", "integrity", "error", "wrong", "mistake", "what should i ask",
                                    "right law", "correct law", "which law", "booked under", "right section",
                                    "correct section", "ipc", "bns",
                                    "what to ask", "questions", "who did", "who could", "who committed", "who else",
                                    "hypothes", "suspect for", "suspects for", "lead", "section", "check this", "audit")):
        return [("investigate", {"mode": "case_check", "case_ref": c}) for c in ents["cases"][:2]]
    if ents["suspects"] and _cue(low, ("geographic profile", "rossmo", "where does", "where do", "anchor", "base of",
                                       "where he lives", "where she lives", "hideout", "operates from", "home base")):
        return [("investigate", {"mode": "geo_profile", "person_ref": ents["suspects"][0]})]
    sid = re.search(r"\b((?:mo|of)-[0-9a-f]{8})\b", low)
    if sid:
        return [("investigate", {"mode": "series", "series_id": sid.group(1).upper()})]
    for c in ents["cases"][:3]:
        calls.append(("get_case", {"case_ref": c}))
    people = list(ents["suspects"])
    for n in ents["names"]:
        offs = [i for k, i in n["matches"] if k == "offender"]
        only_officer = all(k == "officer" for k, _ in n["matches"])
        if (not offs or len(offs) > 1) and not only_officer:
            calls.append(("get_person", {"person_ref": n["name"]}))
    for s in people[:3]:
        calls.append(("get_person", {"person_ref": s}))
    if len(people) >= 2 and _cue(low, ("connect", "link", "relat", "path", "between", "know each", "associat")):
        return [("network", {"entity": people[0], "target": people[1]})]
    for o in ents["officers"][:2]:
        calls.append(("get_officer", {"officer_ref": o}))
    for v in ents["vehicles"][:2]:
        calls.append(("search_registry", {"kind": "vehicles", "query": v}))
    for i in ents["identifiers"][:2]:
        calls.append(("search_registry", {"kind": "cybercrime", "query": i}))
    for mp in ents["missing"][:2]:
        calls.append(("search_registry", {"kind": "missing", "query": mp}))
    if calls:
        return calls

    filters = {k: ents[k] for k in ("district", "crime_type", "status", "year") if ents.get(k)}
    if ents.get("station"):
        filters["police_station"] = ents["station"]
    if _cue(low, ("series", "serial", "same mo", "same modus", "linked cases", "pattern across", "across stations",
                  "across districts", "cross-district", "cross district", "one offender")):
        return [("investigate", {"mode": "series", "district": filters.get("district"), "crime_type": filters.get("crime_type")})]
    if _cue(low, ("under-report", "underreport", "under report", "dark zone", "not registering", "not registered",
                  "burking", "refus", "fir conversion", "converted to fir")):
        return [("investigate", {"mode": "underreporting", "district": filters.get("district")})]
    if _cue(low, ("data quality", "integrity", "inconsisten", "record errors", "data errors", "contradiction")):
        return [("investigate", {"mode": "integrity", "district": filters.get("district")})]
    if _cue(low, ("who should investigate", "which io", "assign", "recommend an io", "recommend io", "best officer for",
                  "which officer should")) and (ents.get("station") or filters.get("crime_type")):
        return [("investigate", {"mode": "assign_io", "station": ents.get("station"), "crime_type": filters.get("crime_type")})]
    if _cue(low, ("death", "fatal", "killed", "died")):
        filters["has_fatalities"] = True

    if NEWS_CUE.search(low) and not copilot_knowledge.is_platform_question(text):
        return [("news", _news_args(text, low, filters))]
    if _cue(low, TIME_CUES) or (" when " in f" {low} " and re.search(r"\bhappen|\boccur|\bcommit", low)):
        return [("time_pattern", {"district": filters.get("district"), "crime_type": filters.get("crime_type")})]
    if _cue(low, ANNUAL_CUES) or (re.search(r"\b(forecast|predict(ed|ion)?|projection|projected)\b", low) and
                                  (re.search(r"\b20[2-3]\d\b|next year|this year|annual|per year|a year", low)
                                   or not _cue(low, ("week", "month", "hotspot", "risk", "recidiv", "repeat",
                                                     "link", "alliance", "syndicate", "gang", "model", "accura")))):
        yr = re.search(r"\b(20[2-3]\d)\b", low)
        return [("annual_forecast", {"district": filters.get("district"), "crime_type": filters.get("crime_type"),
                                     "year": int(yr.group(1)) if yr else None})]

    definitional = not filters and not _cue(low, ("situation", "status", "rate", "level", "how many", "count",
                                                  "number", "trend", "list", "which", "top"))
    live = next((a for a, words in (("routes", ("list the api", "all api", "all the api", "api routes", "list endpoints",
                                               "all endpoints", "how many endpoints", "how many api", "what endpoints")),
                                    ("tables", ("what tables", "which tables", "data tables", "columns", "schema")),
                                    ("pages", ("what pages", "which pages", "list the pages", "what screens", "which screens")))
                 if _cue(low, words)), None)
    if live:
        return [("describe_platform", {"aspect": live})]
    if sight and not filters.get("crime_type"):
        return [("sightings", {"district": filters.get("district")})]
    if _cue(low, SITUATION_CUES) and not _cue(low, ("how many", "count", "which", "list", "show me", "model",
                                                     "project", "platform", "intelhub", "page")) \
            and not copilot_knowledge.is_platform_question(text):
        return [("situation", {"district": filters.get("district")})]
    if copilot_knowledge.is_platform_question(text) or definitional and re.search(
            r"^\s*(what is|what's|what are|what does|define|meaning of|explain)\b.{0,12}\b(a |an |the )?"
            r"(rowdy|history.?sheet|fir|chargesheet|charge sheet|warrant|ipc|bns|cctns|mo\b|modus|io\b|"
            r"commissionerate|recidivism|pendency|kde|dbscan|adamic|louvain|isolation forest|severity)", low):
        return [("project_knowledge", {"topic": text})]
    if _cue(low, ("model", "accura", "auc", "precision", "recall", "f1", "how reliable", "how good is", "baseline")) \
            and not _cue(low, ("vehicle", "make model")):
        pick = next((m for k, m in (("recidiv", "recidivism_risk"), ("classif", "narrative_classifier"),
                                    ("narrative", "narrative_classifier"), ("forecast", "crime_forecast"),
                                    ("conviction", "conviction_outcome"), ("trial", "conviction_outcome"),
                                    ("closure", "case_closure"), ("progress", "case_closure"),
                                    ("pendency", "case_pendency"), ("survival", "case_pendency"),
                                    ("hotspot", "hotspot_clusters"), ("cluster", "hotspot_clusters")) if k in low), None)
        return [("model_info", {"model": pick})]

    # People-registry questions
    if _cue(low, ("rowdy", "sheeter", "warrant", "history sheet", "history-sheet", "gang", "offenders",
                  "criminals", "repeat offender")) and not _cue(low, ("recidiv",)):
        args = {}
        if "rowdy" in low:
            args["rowdy_sheeter"] = True
        if "warrant" in low:
            args["active_warrant"] = True
        if "history" in low:
            args["history_sheeter"] = True
        gm = re.search(r"([A-Z][\w-]+(?:\s[A-Z][\w-]+)?)\s+gang", text or "")
        if gm:
            args["gang"] = gm.group(1)
        if filters.get("district"):
            args["district"] = filters["district"]
        if filters.get("crime_type"):
            args["crime_type"] = filters["crime_type"]
        return [("search_people", args)]

    if _cue(low, ("officer", "inspector", "constable", "dysp", " si ", "io ")):
        worst = _cue(low, ("worst", "lowest", "least", "poor", "weak", "bad ", "badly", "underperform", "under-perform",
                           "bottom", "lagging", "struggling", "suspend", "disciplin", "action against", "sack", "fire "))
        args = {"district": filters.get("district"),
                "sort_by": "cases_assigned" if "most cases" in low else "closure_rate"}
        if worst:
            args["order"] = "asc"
        return [("search_officers", args)]
    if filters.get("crime_type", "").startswith("Cyber") and _cue(low, ("money", "lost", "amount", "rupee", " rs", "lakh",
                                                                        "crore", "upi", "bank", "victims", "ring")):
        return [("insight", {"topic": "cyber_rings"})] if "ring" in low else \
            [("search_registry", {"kind": "cybercrime", "district": filters.get("district")})]
    reg_status = next((st for st, words in (("not recovered", ("not recovered", "unrecovered", "still stolen")),
                                            ("recovered", ("recovered",)), ("missing", ("still missing", "not found", "untraced")),
                                            ("found", ("found", "traced")), ("deceased", ("dead", "deceased")),
                                            ("pending", ("pending",)), ("resolved", ("resolved",)))
                       if _cue(low, words)), None)
    for kind, words in (("vehicles", ("vehicle", "car", "bike", "stolen")),
                        ("missing", ("missing", "lapata", "found")),
                        ("complaints", ("112", "dial", "emergency call", "response time")),
                        ("cctv", ("cctv", "camera"))):
        if _cue(low, words) and not filters.get("crime_type"):
            st = reg_status
            if st and kind == "vehicles" and st not in ("recovered", "not recovered"):
                st = None
            if st and kind == "missing" and st not in ("missing", "found", "deceased"):
                st = None
            return [("search_registry", {"kind": kind, "district": filters.get("district"), "status": st})]

    for topic, words in TOPIC_CUES:
        if _cue(low, words):
            return [("insight", {"topic": topic, "district": filters.get("district"),
                                 "crime_type": filters.get("crime_type")})]

    m = re.search(r"\b(which|what|top|most|highest|lowest|least|rank|compare|breakdown|distribution|per|by)\b", low)
    group = None
    for g, words in (("police_station", ("station",)), ("district", ("district", "region", "area", "city", "where")),
                     ("crime_type", ("crime type", "type of crime", "kind of crime", "category", "crimes are", "crime is")),
                     ("time_of_day", ("time of day", "what time", "hour", "night", "when do")),
                     ("weapon_used", ("weapon",)), ("vehicle_used", ("vehicle used", "getaway")),
                     ("target_type", ("target",)), ("month", ("month", "monthly", "seasonal")),
                     ("year", ("year", "yearly", "annual")), ("weekday", ("weekday", "day of week", "which day")),
                     ("status", ("status",))):
        if _cue(low, words) and g not in filters:
            group = g
            break
    if m and group:
        metric = "fatalities" if _cue(low, ("death", "fatal", "killed", "murders")) and group != "crime_type" else "count"
        superlative = _cue(low, ("most", "highest", "lowest", "least", "fewest", "safest", "top", "worst", "peak", "busiest"))
        args = dict(filters, group_by=group, metric=metric,
                    order="asc" if _cue(low, ("lowest", "least", "fewest", "safest")) else
                    ("desc" if superlative or group not in ("year", "month") else "chronological"))
        args.pop("has_fatalities", None)
        return [("aggregate", args)]

    if filters or _cue(low, ("how many", "count", "number of", "total", "cases", "firs", "list")):
        sort = "severity" if _cue(low, ("severe", "serious", "worst", "dangerous")) else \
               "fatalities" if _cue(low, ("deadl", "fatal")) else "recent"
        if filters or _cue(low, ("how many", "count", "number of")):
            many = all_districts(text, idx) if filters.get("district") else []
            if len(many) > 1:
                return [("search_cases", dict(filters, district=d, sort_by=sort)) for d in many[:3]]
            return [("search_cases", dict(filters, sort_by=sort))]
    if _cue(low, ("summary", "overview", "situation", "status of crime", "brief me", "briefing", "statistics", "stats")):
        return [("insight", {"topic": "overview"})]
    if filters.get("district") and len(filters) == 1:
        return [("situation", {"district": filters["district"]})]
    return []


# =============================================================================
# Deterministic answer composition
# =============================================================================
def _kv(d, n=5):
    return ", ".join(f"{k} ({_fmt_int(v) if isinstance(v, (int, float)) else v})" for k, v in list((d or {}).items())[:n])


def _say_case(c):
    if c.get("error"):
        return c["error"]
    lines = [f"**FIR {c['case_id']}** — {c.get('crime_type')} at {c.get('police_station')}, "
             f"{c.get('district')} district, registered {str(c.get('registered'))[:16]}."]
    lines.append(f"- Status: **{c.get('status')}** · severity {c.get('severity_score')}/100 · "
                 f"{c.get('time_of_day')} · target: {c.get('target_type')}")
    mo = [x for x in (f"weapon {c.get('weapon_used')}" if c.get("weapon_used") not in (None, "None") else None,
                      f"vehicle {c.get('vehicle_used')}" if c.get("vehicle_used") not in (None, "None") else None,
                      c.get("ipc_bns_sections")) if x]
    if mo:
        lines.append("- MO / sections: " + " · ".join(mo))
    for f in c.get("statute_findings") or []:
        lines.append(f"- **Wrong code for the offence date:** {f}")
    if c.get("fatalities") or c.get("injured"):
        lines.append(f"- Casualties: {c.get('fatalities') or 0} killed, {c.get('injured') or 0} injured; "
                     f"{c.get('witnesses') or 0} witnesses cited")
    v = c.get("victim") or {}
    if v.get("name"):
        lines.append(f"- Victim: {v['name']}" + (f", {v.get('age')}" if v.get("age") else "")
                     + (f" ({v.get('gender')})" if v.get("gender") else "")
                     + (" — police personnel" if v.get("is_police_personnel") else ""))
    if c.get("complainant", {}).get("name"):
        lines.append(f"- Complainant: {c['complainant']['name']}")
    acc = c.get("accused") or []
    if acc:
        parts = []
        for a in acc[:6]:
            flags = [f for f, on in (("rowdy-sheeter", a.get("rowdy_sheeter")), ("active warrant", a.get("active_warrant")))
                     if on]
            parts.append(f"{a.get('name')} ({a['suspect_id']}, {a.get('relationship')}"
                         + (f", {a.get('prior_firs')} priors" if a.get("prior_firs") is not None else "")
                         + (", " + ", ".join(flags) if flags else "") + ")")
        lines.append(f"- Accused ({len(acc)}): " + "; ".join(parts))
    else:
        lines.append("- No accused are linked to this case.")
    io = c.get("investigating_officer") or {}
    if io:
        lines.append(f"- IO: {io.get('name') or ''} ({io.get('officer_id')}" + (f", {io.get('rank')}" if io.get("rank") else "") + ")")
    prog = [f"{c['arrests']['count']} arrest(s)" + (f" from {c['arrests']['first_date']}" if c['arrests'].get('first_date') else "")] if c.get("arrests") else []
    if c.get("chargesheet"):
        prog.append(f"chargesheet filed {c['chargesheet'].get('filed_on')}")
    if c.get("court"):
        ct = c["court"]
        prog.append(f"{ct.get('court_name')}: {ct.get('conviction_status')}"
                    + (f", next hearing {ct.get('next_hearing')}" if ct.get("conviction_status") == "Pending Trial" and ct.get("next_hearing") else ""))
    if prog:
        lines.append("- Progress: " + " → ".join(prog))
    if c.get("pendency_model"):
        p = c["pendency_model"]
        lines.append(f"- Pendency model: open {p.get('days_open')} days, ~{round(p.get('expected_additional_days') or 0)} more days expected before chargesheet")
    if c.get("stolen_vehicles"):
        lines.append("- Linked stolen vehicle: " + "; ".join(f"{x.get('vehicle_number')} {x.get('make_model')} ({x.get('recovery_status')})" for x in c["stolen_vehicles"]))
    if c.get("cyber_identifiers"):
        cy = c["cyber_identifiers"]
        lines.append(f"- Cyber trail: Rs {_fmt_int(cy.get('fraud_amount'))} from {cy.get('victim_count')} victims; "
                     f"UPI {cy.get('upi_ids')}; phones {cy.get('phone_numbers')}")
        for r in c.get("cyber_rings") or []:
            lines.append(f"- Part of fraud ring {r['identifier']} spanning {r['linked_cases']} cases (Rs {_fmt_int(r['total_fraud'])})")
    if c.get("cctv"):
        lines.append(f"- CCTV: {len(c['cctv'])} camera hit(s), e.g. {c['cctv'][0].get('camera_id')}")
    if c.get("anomaly_flag"):
        lines.append("- Flagged anomalous: " + ", ".join(c["anomaly_flag"]))
    if c.get("narrative"):
        lines.append(f"- Narrative: “{c['narrative'][:420]}{'…' if len(c['narrative']) > 420 else ''}”")
    if c.get("similar_cases"):
        lines.append("- Similar MO: " + "; ".join(f"{s['case_id']} ({s['district']}, {s['status']}; shares {', '.join(s['shared_mo']) or 'crime type'})" for s in c["similar_cases"]))
    return "\n".join(lines)


def _say_person(p):
    if p.get("error"):
        return p["error"]
    if p.get("ambiguous"):
        cands = p.get("candidates") or []
        return (f"{len(cands)}+ registry entries share the name “{p.get('query')}”. Which one do you mean?\n"
                + "\n".join(f"- {c['suspect_id']}: {c.get('risk_level')} risk, {c.get('prior_firs', 0)} priors"
                            + (f", {c.get('gang')}" if c.get("gang") else "") for c in cands[:6]))
    if "offender_record" in p:
        bits = []
        for o in p.get("other_matches") or []:
            bits.append(f"{o['kind']} {o['id']}")
        if p.get("victim_case_count"):
            bits.append(f"victim in {p['victim_case_count']} case(s), e.g. {', '.join(p['named_as_victim_in'][:3])}")
        return f"“{p.get('query')}” is not in the offender registry. Matches: " + "; ".join(bits) + "."
    flags = [f for f, on in (("rowdy-sheeter", p.get("rowdy_sheeter")), ("history-sheeter", p.get("history_sheeter")),
                             ("ACTIVE WARRANT", p.get("active_warrant"))) if on]
    head = (f"**{p.get('name')} ({p['suspect_id']})**" + (f", alias {p['alias']}" if p.get("alias") else "")
            + (f", age {p['age']}" if p.get("age") else "") + f" — {p.get('risk_level') or 'unrated'} risk"
            + (f", {p['gang']}" if p.get("gang") else ", no gang on file") + ".")
    lines = [head]
    lines.append(f"- Registry: {p.get('prior_firs', 0)} prior FIRs, {p.get('past_arrests', 0)} arrests"
                 + (" · " + ", ".join(flags) if flags else " · no rowdy/history sheet, no active warrant"))
    if p.get("cases_in_corpus"):
        lines.append(f"- Named in **{p['cases_in_corpus']} case(s)** between {p.get('first_seen')} and {p.get('last_seen')}: "
                     + _kv(p.get("crime_types"), 4))
        lines.append(f"- Districts: {_kv(p.get('districts'), 4)} · status: {_kv(p.get('case_status'), 3)}"
                     + (f" · court: {_kv(p.get('court_outcomes'), 3)}" if p.get("court_outcomes") else ""))
        lines.append("- Latest: " + "; ".join(f"{c['case_id']} {c['crime_type']} ({c['district']}, {c['registered']}, {c['role']}, {c['status']})" for c in p["cases"][:3]))
    if p.get("co_offenders"):
        lines.append("- Co-offenders: " + "; ".join(f"{c.get('name')} ({c['suspect_id']}, {c['shared_cases']} shared)" for c in p["co_offenders"][:5]))
    r = p.get("recidivism")
    if r:
        lines.append(f"- Recidivism index {r.get('index')}/100" + (f" ({r['band']})" if r.get("band") else "")
                     + (f" — drivers: {', '.join(r['drivers'])}" if r.get("drivers") else "")
                     + ". This orders attention; it is not evidence.")
    for s in p.get("syndicates") or []:
        lines.append(f"- Member of syndicate {s['syndicate_id']} ({s['size']} people, {s['risk_level']} risk)")
    if p.get("predicted_links"):
        lines.append("- Predicted emerging link(s): " + ", ".join(f"{l['with']} (score {l['score']})" for l in p["predicted_links"]))
    return "\n".join(lines)


def _say_cases(r):
    ap = r.get("filters_applied") or {}
    desc = " ".join(str(ap[k]) for k in ("crime_type", "status") if k in ap) or "FIRs"
    where = (f" in {ap['district']}" if ap.get("district") else "") + (f" at {ap['police_station']}" if ap.get("police_station") else "") \
        + (f" during {ap['year']}" if ap.get("year") else "") + (" involving deaths" if ap.get("has_fatalities") else "")
    n = r.get("total_matching", 0)
    if not n:
        return f"No {desc}{where} are recorded in the corpus."
    lines = [f"**{_fmt_int(n)} {desc}{where}** — {r.get('share_of_corpus_pct')}% of the corpus"
             + (f", {r['date_range'][0]} to {r['date_range'][1]}" if r.get("date_range") else "") + "."]
    lines.append(f"- Status: {_kv(r.get('by_status'))}")
    if r.get("by_crime_type"):
        lines.append(f"- Crime types: {_kv(r.get('by_crime_type'))}")
    if r.get("by_district"):
        lines.append(f"- Districts: {_kv(r.get('by_district'))}")
    lines.append(f"- {_fmt_int(r.get('fatalities'))} killed, {_fmt_int(r.get('injured'))} injured · average severity {r.get('avg_severity')}")
    label = {"severity": "Most severe", "fatalities": "Deadliest", "oldest": "Oldest"}.get(r.get("sorted_by"), "Most recent")
    lines.append(f"- {label}: " + "; ".join(f"{c['case_id']} ({c['crime_type']}, {c['district']}, {c['registered']}, {c['status']})" for c in (r.get("cases") or [])[:4]))
    return "\n".join(lines)


def _say_aggregate(r):
    g = r.get("group_by", "").replace("_", " ")
    ap = r.get("filters_applied") or {}
    scope = " ".join(str(v) for v in ap.values()) or "all FIRs"
    groups = r.get("groups") or {}
    if not groups:
        return f"No records match {scope}."
    metric = {"count": "cases", "fatalities": "deaths", "injured": "injured", "avg_severity": "avg severity"}[r.get("metric", "count")]
    items = list(groups.items())
    top = items[0]
    lines = [f"By {g} ({scope}, {_fmt_int(r.get('total_cases'))} cases): **{top[0]}** leads with {_fmt_int(top[1]) if metric != 'avg severity' else top[1]} {metric}."
             if not r.get("chronological") else f"{metric.capitalize()} by {g} ({scope}):"]
    lines.append("- " + "\n- ".join(f"{k}: {_fmt_int(v) if metric != 'avg severity' else v}" for k, v in items[:12]))
    if r.get("n_groups", 0) > 12 and not r.get("chronological"):
        lines.append(f"({r['n_groups']} {g} values in total)")
    if g == "district" and metric == "cases":
        lines.append("Recorded volume reflects reporting and enforcement as much as underlying crime.")
    return "\n".join(lines)


def _say_people(r):
    ap = r.get("filters_applied") or {}
    n = r.get("total_matching", 0)
    what = ", ".join(f"{k.replace('_', ' ')}={v}" for k, v in ap.items()) or "all registered offenders"
    if not n:
        return f"No offenders match {what}."
    lines = [f"**{_fmt_int(n)} offenders** match {what}."]
    lines.append(f"- Risk levels: {_kv(r.get('by_risk_level'))}")
    lines.append(f"- {_fmt_int(r.get('rowdy_sheeters'))} rowdy-sheeters, {_fmt_int(r.get('history_sheeters'))} history-sheeters, "
                 f"{_fmt_int(r.get('with_active_warrant'))} with active warrants")
    if r.get("top_gangs"):
        lines.append(f"- Gangs: {_kv(r.get('top_gangs'))}")
    if r.get("people"):
        lines.append("- Most priors: " + "; ".join(f"{p.get('name')} ({p['suspect_id']}, {p.get('prior_firs')} priors)" for p in r["people"][:5]))
    return "\n".join(lines)


def _say_officer(o):
    if o.get("error"):
        return o["error"]
    if o.get("ambiguous"):
        return "Several officers share that name: " + "; ".join(f"{c['officer_id']} {c.get('rank')} at {c.get('station')}" for c in o["candidates"])
    lines = [f"**{o.get('name')} ({o['officer_id']})** — {o.get('rank')}, {o.get('station')}, {o.get('district')}; "
             f"specialises in {o.get('specialization')}."]
    cr = o.get("closure_rate")
    lines.append(f"- {o.get('cases_assigned')} cases assigned, closure rate {round(cr * 100) if isinstance(cr, (int, float)) else cr}%, "
                 f"{o.get('commendations')} commendations; appointed {o.get('appointment_date')}")
    if o.get("cases_as_io_in_corpus"):
        lines.append(f"- IO on {o['cases_as_io_in_corpus']} FIRs in the corpus: {_kv(o.get('by_status'))}")
        lines.append("- Recent: " + "; ".join(f"{c['case_id']} {c['crime_type']} ({c['status']})" for c in o.get("recent_cases", [])[:3]))
    return "\n".join(lines)


def _say_officers(r):
    offs = r.get("officers") or []
    if not offs:
        return "No officers match."
    metric = r["sorted_by"].replace("_", " ")
    if r.get("order") == "asc":
        floor = f" with at least {r['min_cases']} cases" if r.get("min_cases") else ""
        head = f"**Lowest {metric} first** — {r['total_matching']} officers{floor}"
    else:
        head = f"**{r['total_matching']} officers**, highest {metric} first"
    avg = r.get("state_avg_closure_rate")
    if avg is not None:
        head += f" (state average {round(avg * 100)}% closure)"
    body = "\n".join(f"- {o.get('name')} ({o.get('officer_id')}), {o.get('rank')}, {o.get('station')}: "
                     f"{round((o.get('closure_rate') or 0) * 100)}% closure, {o.get('cases_assigned')} cases"
                     for o in offs[:8])
    tail = f"\n\n{r['caution']}" if r.get("caution") else ""
    return f"{head}:\n{body}{tail}"


def _say_registry(r):
    if r.get("error"):
        return r["error"]
    kind = r["registry"]
    n = r["total_matching"]
    label = {"vehicles": "stolen-vehicle", "cybercrime": "cyber-fraud", "missing": "missing-person",
             "courts": "court", "complaints": "Dial-112 call", "cctv": "CCTV"}.get(kind, kind)
    scope = ", ".join(str(v) for v in (r.get("query"), r.get("district"), r.get("status")) if v)
    if not n:
        return f"No {label} records match {scope or 'that'}."
    if kind == "cybercrime" and r.get("total_fraud_rs") is not None:
        head = (f"**Rs {_fmt_int(r['total_fraud_rs'])} lost** to {_fmt_int(r['total_victims'])} victims across "
                f"{_fmt_int(n)} cyber-fraud case(s)" + (f" matching {scope}" if scope else "") + ".")
    elif scope:
        head = f"**{_fmt_int(n)} of {_fmt_int(r['total_records'])} {label} records** match {scope}."
    else:
        head = f"The {label} registry holds **{_fmt_int(n)} records**."
    lines = [head]
    for k, v in r.items():
        if k.startswith("by_"):
            lines.append(f"- {k[3:].replace('_', ' ').capitalize()}: {_kv(v)}")
    if r.get("avg_response_min") is not None:
        lines.append(f"- Average response time {r['avg_response_min']} min")
    for rec in (r.get("records") or [])[:5 if r.get("query") else 3]:
        lines.append("- " + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in rec.items() if v not in (None, "") and k != "owner_name"))
    return "\n".join(lines)


def _say_network(r):
    if r.get("error"):
        return r["error"]
    if "connected" in r:
        if not r["connected"]:
            return f"{r['from']} and {r['to']} are not connected in the link graph. {r.get('note', '')}"
        hops = " → ".join(f"{n.get('name') or n['id']}" + (f" [{n['id']}]" if n.get("name") else f" ({n.get('crime_type') or n['type']})") for n in r["path"])
        return f"**Connected in {r['hops']} hop(s):** {hops}"
    e = r["entity"]
    return (f"**{e.get('name') or e['id']}** has {r['degree']} direct link(s): {_kv(r['neighbours_by_type'])}"
            + (f"; {r['second_degree_suspects']} suspects at second degree." if "second_degree_suspects" in r else "."))


def _say_insight(topic, r):
    if r.get("error"):
        return r["error"]
    if topic == "forecast":
        s = r.get("statewide") or {}
        out = [f"**Forecast:** about **{s.get('next_week')} incidents statewide next week** ({s.get('trend')}, "
               f"{s.get('change_pct'):+}% vs the prior 4 weeks; average {s.get('avg_weekly')}/week)." if s.get("change_pct") is not None else "Forecast unavailable."]
        if r.get("district"):
            for d, v in r["district"].items():
                out.append(f"- {d}: ~{v.get('next_week')} next week ({v.get('trend')}, {v.get('change_pct'):+}%)")
        if r.get("crime_type"):
            for d, v in r["crime_type"].items():
                out.append(f"- {d}: ~{v.get('next_week')} next week ({v.get('trend')}, {v.get('change_pct'):+}%)")
        if r.get("districts_rising"):
            out.append("- Rising: " + ", ".join(f"{d} ({c:+}%)" for d, c in r["districts_rising"].items()))
        out.append("The model beats naive baselines but not a 4-week moving average — read it as a range, not a number.")
        return "\n".join(out)
    if topic == "hotspots":
        z = r.get("zones") or []
        if r.get("district_filter"):
            if not z:
                return f"No DBSCAN hotspot zone is centred in {r['district_filter']}; incidents there are dispersed."
            return (f"**{len(z)} hotspot zone(s) in {r['district_filter']}** (of {r.get('dbscan_zones')} statewide):\n"
                    + "\n".join(f"- ~{c['radius_km']} km zone: {c['cases']} incidents, mostly {c['dominant_crime']}, "
                                f"{c['open_cases']} open, peak {c['peak_time']}, avg severity {c['avg_severity']}" for c in z[:5]))
        return (f"**{r.get('dbscan_zones')} DBSCAN hotspot zones** detected.\n"
                + "\n".join(f"- {c['district']}: {c['cases']} incidents within ~{c['radius_km']} km, mostly {c['dominant_crime']}, "
                            f"{c['open_cases']} open, peak {c['peak_time']}" for c in z[:5]))
    if topic == "trends":
        al = r.get("alerts") or []
        if not al:
            return "No crime category is currently spiking above its historical baseline."
        return ("**Emerging trend alerts** (last 30 days vs baseline):\n"
                + "\n".join(f"- {a['crime_type']} in {a['district']}: {a['recent_count']} cases vs {a['expected_count']} expected ({a['spike_ratio']}×)" for a in al[:6])
                + "\nA spike can reflect reporting changes as easily as offending.")
    if topic == "anomalies":
        an = r.get("anomalies") or []
        return f"**{r.get('count', 0)} anomalous incidents** flagged by Isolation Forest." + (
            "\n" + "\n".join(f"- {a.get('case_id')}: {a.get('crime_type')} in {a.get('district')} — {', '.join(a.get('reasons') or [])}" for a in an[:5]) if an else "")
    if topic == "recidivism":
        b = r.get("bands") or {}
        return (f"**Recidivism bands:** {_kv(b)}.\n"
                + "- Highest indices (ids only — names stay in the gated registry): "
                + "; ".join(f"{t['criminal_id']} {t['risk_index']}" for t in (r.get("top_by_id") or [])[:5])
                + "\nThe index is a percentile for ordering attention; it is not a prediction about any person.")
    if topic == "pendency":
        s = r.get("summary") or {}
        bd = r.get("by_district") or []
        if len(bd) == 1:
            d = bd[0]
            out = [f"**{d['name']}: {d['open_cases']} open FIRs** without chargesheet, {d['breached_90d']} of them past 90 days "
                   f"({round(d['breach_rate_90d'] * 100)}%), mean {d['mean_days_open']} days open. Statewide: "
                   f"{_fmt_int(s.get('open_cases'))} open, {_fmt_int(s.get('already_breached_90d'))} past 90 days."]
            for c in (r.get("longest_expected_delay") or [])[:3]:
                out.append(f"- Likely to drag: {c['case_id']} ({c['crime_type']}), ~{round(c['expected_additional_days'] or 0)} more days expected")
            return "\n".join(out)
        out = [f"**{_fmt_int(s.get('open_cases'))} open FIRs** without chargesheet; {_fmt_int(s.get('already_breached_90d'))} already past 90 days "
               f"(median open {s.get('median_days_open')} days). {_fmt_int(s.get('backlog'))} sit beyond the model's {s.get('grid_max_days')}-day grid and are reported as backlog, not scored."]
        for d in (r.get("by_district") or [])[:5]:
            out.append(f"- {d['name']}: {d['open_cases']} open, {d['breached_90d']} past 90 days")
        for c in (r.get("longest_expected_delay") or [])[:3]:
            out.append(f"- Likely to drag: {c['case_id']} ({c['crime_type']}, {c['district']}), ~{round(c['expected_additional_days'] or 0)} more days")
        return "\n".join(out)
    if topic == "dispatch":
        return (f"**Patrol plan:** {_fmt_int(r.get('total_recommended_officers'))} officers recommended statewide.\n"
                + "\n".join(f"- {d['district']}: risk {d['risk_score']} ({d['risk_level']}) → {d['recommended_officers']} officers; {d['open_cases']} open cases" for d in (r.get("districts") or [])[:6]))
    if topic == "socioeconomic":
        out = ["**Socio-economic correlation:**"] + [f"- {i}" for i in (r.get("insights") or [])[:5]]
        if r.get("district"):
            d = r["district"]
            out.append(f"- {d.get('district')}: " + ", ".join(f"{k} {v}" for k, v in d.items() if k != "district" and not isinstance(v, (dict, list)))[:300])
        out.append("These are correlations across 31 districts, not causes.")
        return "\n".join(out)
    if topic == "operational_index":
        return ("**District operational risk index:**\n"
                + "\n".join(f"- {d['district']}: {d['index']} ({d['band']})" for d in (r.get("districts") or [])[:8]))
    if topic == "lifecycle":
        st = ((r.get("cohort") or {}).get("stages")) or []
        return ("**Case lifecycle** (cohort " + str((r.get("cohort") or {}).get("label")) + "):\n"
                + "\n".join(f"- {s['stage']}: {_fmt_int(s['count'])} ({round(s['share'] * 100, 1)}%)" for s in st))
    if topic == "predicted_links":
        return ("**Predicted emerging alliances** (Adamic-Adar):\n"
                + "\n".join(f"- {l['a_name']} ({l['a']}) ↔ {l['b_name']} ({l['b']}): {l['common_associates']} shared associates, score {l['score']}" for l in (r.get("links") or [])[:6]))
    if topic == "syndicates":
        return (f"**{r.get('count')} syndicates** detected by repeat co-offending. Largest:\n"
                + "\n".join(f"- {s['syndicate_id']}: {s['size']} members, {s['risk_level']} risk ({', '.join(s['member_ids'][:5])}…)" for s in (r.get("largest") or [])[:5]))
    if topic == "cyber_rings":
        return (f"**Cyber fraud:** {_fmt_int(r.get('cyber_cases'))} cases, Rs {_fmt_int(r.get('total_fraud_rs'))} lost by {_fmt_int(r.get('victims'))} victims.\n"
                + "\n".join(f"- {x['identifier']} links {x['linked_cases']} cases (Rs {_fmt_int(x['total_fraud'])})" for x in (r.get("rings") or [])[:5]))
    if topic == "courts":
        return (f"**Courts:** {_fmt_int(r.get('court_records'))} trial records — {_kv(r.get('by_outcome'))}. "
                f"Conviction rate among decided trials: **{r.get('conviction_rate_of_decided_pct')}%**.\n"
                f"- Busiest courts: {_kv(r.get('by_court'), 4)}")
    # overview
    return (f"**Corpus overview:** {_fmt_int(r.get('total_firs'))} FIRs"
            + (f" ({r['date_range'][0]} to {r['date_range'][1]})" if r.get("date_range") else "")
            + f", {_fmt_int(r.get('offenders'))} offenders.\n"
            f"- Status: {_kv(r.get('by_status'))}\n- Top crimes: {_kv(r.get('top_crime_types'))}\n"
            f"- Top districts: {_kv(r.get('top_districts'))}\n"
            f"- {_fmt_int(r.get('fatalities'))} killed, {_fmt_int(r.get('injured'))} injured · "
            f"{_fmt_int(r.get('rowdy_sheeters'))} rowdy-sheeters, {_fmt_int(r.get('active_warrants'))} active warrants · "
            f"{_fmt_int(r.get('stolen_vehicles'))} stolen vehicles ({r.get('vehicle_recovery_rate_pct')}% recovered)")


def _say_model(r):
    if r.get("error"):
        return r["error"]
    if "models" in r:
        return ("**Model registry** — each scored on a later, held-out time window against a trivial baseline:\n"
                + "\n".join(f"- {n}: {m.get('headline') or ', '.join(f'{k} {v}' for k, v in m['key_metrics'].items())}"
                            for n, m in r["models"].items()))
    mt = r.get("metrics") or {}
    keys = [k for k in ("accuracy", "roc_auc", "pr_auc", "brier", "f1_macro", "mase", "c_index",
                        "integrated_brier", "silhouette", "n_test") if k in mt]
    out = [f"**{r['model']}** — {r.get('headline') or ''}".rstrip(" —"),
           "- Metrics: " + ", ".join(f"{k} {mt[k]}" for k in keys)]
    bl = r.get("baselines") or []
    if bl:
        out.append("- Baselines: " + "; ".join(
            f"{b.get('name') or b.get('strategy')}: " + ", ".join(f"{k} {v}" for k, v in b.items()
                                                                   if isinstance(v, (int, float)))[:120] for b in bl[:3]))
    for n in (r.get("notes") or [])[:2]:
        out.append(f"- Note: {n}")
    return "\n".join(out)


def _first_sentences(text, limit=650):
    text = re.sub(r"\s+", " ", re.sub(r"[|#>`*]+", " ", text or "")).strip()
    out = ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if len(out) + len(sent) > limit and out:
            break
        out = (out + " " + sent).strip()
    return out[:limit]


def _say_knowledge(r):
    secs = r.get("sections") or []
    matches = r.get("matches") or []
    if secs and secs[0].get("excerpt") and r.get("best_is_curated", True):
        see = [f"- See also: {m['title']} ({m['source']})" for m in matches[:2]]
        return secs[0]["excerpt"] + ("\n" + "\n".join(see) if see else "")
    if matches and not r.get("best_is_curated"):
        top = matches[0]
        out = f"**{top['title']}** — {_first_sentences(top['text'])}\n_(from {top['source']})_"
        more = [m for m in matches[1:3]]
        if more:
            out += "\n" + "\n".join(f"- See also: {m['title']} ({m['source']})" for m in more)
        return out
    return "\n\n".join(f"**{s['title']}** — {s['text']}" for s in secs[:1]) + (
        "\n" + "\n".join(f"- See also: {m['title']} ({m['source']})" for m in matches[:2]) if matches else "")


def _say_platform(r):
    if "routes" in r:
        rows = r["routes"]
        return (f"**{r.get('count', len(rows))} live API endpoints.**\n"
                + "\n".join(f"- {x['path']} ({', '.join(x.get('methods', []))}) — {x.get('role')}" for x in rows[:25])
                + (f"\n…and {len(rows) - 25} more" if len(rows) > 25 else ""))
    if "tables" in r:
        return (f"**{r['count']} data tables are loaded.**\n"
                + "\n".join(f"- {t['table']}: {_fmt_int(t['rows'])} rows — {', '.join(t['columns'][:8])}" for t in r["tables"][:30]))
    if "pages" in r:
        return ("**Screens in the web client:**\n"
                + "\n".join(f"- {p['url']}: {', '.join(p['headings'][:3]) or p['note'] or ', '.join(p['labels'][:3])}" for p in r["pages"]))
    if "models" in r:
        return f"**{r['count']} registered models:**\n" + "\n".join(f"- {n}: {h}" for n, h in r["models"].items())
    return ("**What I know about myself right now:** " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in r.items()) + ".")


def _say_situation(r):
    if r.get("error"):
        return r["error"]
    scope = r["scope"]
    d7, p7 = r["last_7_days"], r["previous_7_days"]
    delta = (f"{'up' if d7 > p7 else 'down' if d7 < p7 else 'flat'} from {p7} the week before" if p7 else "")
    lines = [f"**{scope} right now: {d7} FIRs in the last 7 days**" + (f" ({delta})" if delta else "")
             + f", {r['last_30_days']} in the last 30 (records up to {r['records_up_to']})."]
    if r.get("last_30_by_crime"):
        lines.append(f"- Mix (30 days): {_kv(r['last_30_by_crime'])} · {r['last_30_killed']} killed, {r['last_30_injured']} injured")
    if r.get("busiest_districts_30d"):
        lines.append(f"- Busiest districts: {_kv(r['busiest_districts_30d'], 4)}")
    if r.get("top_stations_30d"):
        lines.append(f"- Busiest stations: {_kv(r['top_stations_30d'], 3)}")
    for a in (r.get("trend_alerts") or [])[:2]:
        lines.append(f"- ⚠ Spike: {a['crime_type']} in {a['district']} — {a['recent_count']} vs {a['expected_count']} expected ({a['spike_ratio']}×)")
    fc = r.get("forecast_next_week")
    if fc and fc.get("next_week") is not None:
        lines.append(f"- Forecast next week: ~{fc['next_week']} incidents ({fc.get('trend')}"
                     + (f", {fc['change_pct']:+}%" if fc.get("change_pct") is not None else "") + ")")
    if r.get("patrol"):
        p = r["patrol"]
        lines.append(f"- Patrol risk {p['risk_score']} ({p['risk_level']}) → {p['recommended_officers']} officers recommended")
    cam = r.get("live_cameras")
    if cam:
        c = cam["alerts_24h"]
        lines.append(f"- Live cameras: {cam['online']}/{cam['cameras']} online · {c.get('P1', 0)} P1 and {c.get('P2', 0)} P2 alerts in 24h, "
                     f"{c.get('new', 0)} awaiting review")
        for a in cam["latest_alerts"][:2]:
            lines.append(f"  - {a['time']} {a['camera']}: {a['what']} ({a['priority']}; {', '.join(a['reasons'][:2])})")
    if r.get("latest_cases"):
        lines.append("- Latest FIRs: " + "; ".join(f"{c['case_id']} {c['crime_type']} ({c['station']}, {c['registered'][:10]})"
                                                   for c in r["latest_cases"][:3]))
    lines.append(f"- {_fmt_int(r['open_cases_total'])} cases still under investigation in {scope}.")
    return "\n".join(lines)


def _say_sightings(r):
    if r.get("error"):
        return r["error"]
    if "sightings_24h" in r:
        n = r["sightings_24h"]
        if not n:
            return f"{r.get('name')} ({r['suspect_id']}) has not been matched on any camera in the last 24 hours."
        last = r["sightings"][-1]
        lines = [f"**{r.get('name')} ({r['suspect_id']}) was last matched at {last['camera']}, {last['district']}** "
                 f"at {last['time'][11:16]} UTC ({round(last['similarity'] * 100)}% face similarity, {last['band']} band) — "
                 f"{n} sighting(s) in 24h."]
        lines += [f"- {x['time'][11:16]} {x['camera']} ({x['district']}) · {round(x['similarity'] * 100)}% · {x['status']}"
                  for x in r["sightings"][-6:-1][::-1]]
        lines.append("Each sighting is a lead until an officer verifies it on the CCTV page.")
        return "\n".join(lines)
    if "registry_match" in r:
        m = r["registry_match"]
        head = (f"**{r['query']} matches stolen vehicle {m['vehicle_number']}** ({m.get('make_model')}, stolen "
                f"{m.get('theft_date')} in {m.get('theft_district')}, {m.get('recovery_status')}; "
                f"{m['kind']} match)." if m else f"{r['query']} is not in the stolen-vehicle registry.")
        reads = r.get("recent_reads") or []
        return head + ("\n" + "\n".join(f"- Read at {x['camera']} {x['time'][11:16]} UTC as {x['read']}" for x in reads[-5:]) if reads
                       else "\n- No ANPR reads of it in the last 24 hours.")
    c = r.get("counts_24h") or {}
    lines = [f"**{r['scope']} camera network: {c.get('P1', 0)} P1 and {c.get('P2', 0)} P2 alerts in the last 24h**, "
             f"{c.get('new', 0)} awaiting review, {c.get('verified', 0)} verified, {c.get('rejected', 0)} rejected."]
    for a in r.get("alerts", [])[:5]:
        who = f"{a['name']} ({a['person']})" if a.get("person") else f"plate {a.get('plate')}"
        lines.append(f"- {a['time'][11:16]} {a['camera']}: {who} — {a['priority']}, {', '.join(a['reasons'][:2])} [{a['status']}]")
    lines.append(r.get("policy", ""))
    return "\n".join(lines)


def _say_news(r):
    if r.get("error"):
        return r["error"]
    scope = r.get("scope") or "Karnataka"
    about = " · ".join(x for x in (r.get("topic"), None if r.get("topic") else r.get("crime_type"),
                                   f"“{r['query']}”" if r.get("query") else None) if x)
    scope_txt = f"{scope} ({about})" if about else scope
    heads = r.get("headlines") or []
    down = [f"{s['source']}: {s['error']}" for s in r.get("sources") or []
            if s.get("configured") and not s.get("ok") and s.get("error")]
    if not heads and r.get("status") == "disabled":
        return ("**Live News is switched off on this deployment** (NEWS_FETCH=off), so there are no public "
                "headlines to read. Your IntelHub records are unaffected.")
    if not heads and r.get("status") == "unreachable":
        return ("**I couldn't reach the public news feeds just now**" + (f" — {'; '.join(down[:3])}" if down else "")
                + ". The Live News page retries on its own; your IntelHub records are unaffected.")
    if heads:
        n = r.get("total_matching") or len(heads)
        lines = [f"**{n} recent public headline{'s' if n != 1 else ''} on {scope_txt}**"
                 + (" — some feeds are unreachable, so part of this is the last good fetch" if r.get("stale") else "")
                 + ":"]
        lines += [f"- {h['title']} — {h['source']}, {h['when']} · {h['category']}" for h in heads[:5]]
    else:
        lines = [f"**No public headlines on {scope_txt} in the last 7 days.**"]
        lines += [f"- Latest statewide: {h['title']} — {h['source']}, {h['when']}" for h in r.get("latest_statewide") or []]
    rec = r.get("records")
    if rec:
        what = f"{rec['crime_type']} FIRs" if rec.get("crime_type") else "FIRs"
        where = f"in {rec['district']}" if rec.get("district") else "statewide"
        lines.append(f"- IntelHub records: **{_fmt_int(rec['cases_30d'])} {what} {where}** in the 30 days to "
                     f"{rec['as_of']} ({_fmt_int(rec['cases_total'])} in all).")
    lines.append("Headlines come from public sources and are not verified by KSP — open Live News for the links.")
    return "\n".join(lines)


def _say_annual(r):
    if r.get("error"):
        return r["error"]
    iv = r.get("interval_80") or {}
    what = r["crime_type"]
    lines = []
    if r["kind"] == "backtest":
        lines.append(f"**{r['unit']} {r['year']} — backtest, {what}:** the model, using only earlier years, would "
                     f"have predicted **{r['predicted']:,}** (80% range {iv.get('low', 0):,}–{iv.get('high', 0):,}); "
                     f"the corpus recorded **{r['actual']:,}** — {'inside' if r['inside_interval'] else 'outside'} the range.")
    else:
        ch = r.get("change_vs_last_pct")
        lines.append(f"**{r['unit']} {r['year']} forecast, {what}: {r['predicted']:,} FIRs** "
                     f"(80% range {iv.get('low', 0):,}–{iv.get('high', 0):,}), "
                     f"{'+' if (ch or 0) > 0 else ''}{ch}% vs {r['last_year']} ({r['last_count']:,})"
                     + ("" if r.get("change_exceeds_noise_band") else " — within normal year-to-year noise") + ".")
    lines.append(r["explanation"])
    if r.get("top_crime_types"):
        lines.append("By crime type: " + "; ".join(f"{t['crime_type']} {t['predicted']}" for t in r["top_crime_types"]) + ".")
    b = r.get("backtest") or {}
    if b.get("skill_vs_last_year") is not None:
        lines.append(f"Backtested next-year error ±{b['mae']} FIRs per district — {round(100 * b['skill_vs_last_year'])}% "
                     f"more accurate than 'same as last year' (vs best baseline: {b.get('verdict_vs_best_baseline')}).")
    lines.append(r["note"])
    return "\n".join(lines)


def _say_time(r):
    if r.get("error"):
        return r["error"]
    f = r.get("filters") or {}
    scope = " · ".join(x for x in (f.get("district"), f.get("crime_type")) if x) or "statewide, all crime types"
    pc, pw = r.get("peak_cell") or {}, r.get("peak_window") or {}
    lines = [f"**When FIRs happen ({scope}, {r['firs']:,} FIRs):** busiest day {r.get('busiest_weekday')}, "
             f"busiest hour {r.get('busiest_hour')}, busiest 3-hour window {pw.get('label')} ({pw.get('count')} FIRs); "
             f"single busiest slot {pc.get('label')} ({pc.get('count')} vs {pc.get('expected')} for an even spread)."]
    if r.get("hot_cells"):
        lines.append("Hot beyond chance: " + ", ".join(c["label"] for c in r["hot_cells"][:5]) + ".")
    lines.append(r["note"])
    return "\n".join(lines)


def _say_investigate(mode, r):
    if r.get("error"):
        return r["error"]
    if mode == "case_check":
        lines = [f"**FIR {r['case_id']}** — {r['crime_type']}, {r['station']} ({r['status']}): "
                 f"**{len(r['integrity'])} record issue(s)**."]
        lines += [f"- {f['detail']} _(cites {', '.join(f['cites'])})_" for f in r["integrity"][:5]]
        if r["questions_for_io"]:
            lines.append("\n**Questions for the IO**")
            lines += [f"{i}. {q}" for i, q in enumerate(r["questions_for_io"][:5], 1)]
        if r["hypotheses"]:
            lines.append("\n**Competing hypotheses** (fewest inconsistencies first; demographics never used)")
            lines += [f"- {h['id']}{' (named)' if h['named_on_fir'] else ''}: {h['consistent']} consistent, "
                      f"{h['inconsistent']} inconsistent" for h in r["hypotheses"][:4]]
        return "\n".join(lines)
    if mode == "integrity":
        lines = [f"**{r['firs_flagged']:,} of {r['firs_checked']:,} FIRs** carry at least one record issue"
                 + (f" in {r['district']}" if r.get("district") else "") + ":"]
        lines += [f"- {c['title']}: {c['count']:,}" for c in sorted(r["checks"], key=lambda c: -c["count"])[:8]]
        return "\n".join(lines)
    if mode == "series":
        if r.get("members"):
            who = f" — {r['person']['id']}" if r.get("person") else ""
            g = r.get("geoprofile") or {}
            geo = (f" Geographic profile: 50% of the search surface lies in {g['search_area_km2']['50']} km² around "
                   f"{g['peak']['lat']}, {g['peak']['lng']}." if g.get("applicable") else "")
            return (f"**Series {r['id']}**{who}: {r['size']} {r['crime_type']} FIRs across {r['stations']} stations in "
                    f"{', '.join(r['districts'])}, {r['first']} to {r['last']}.{geo}")
        lines = [f"**{r['total']:,} candidate series** ({', '.join(f'{v} {k}' for k, v in r['by_kind'].items())}):"]
        lines += [f"- {x['id']} — {x['size']} {x['crime_type']} FIRs, {x['stations']} stations, {len(x['districts'])} district(s), "
                  f"{x['open']} open" for x in r["series"][:5]]
        return "\n".join(lines)
    if mode == "geo_profile":
        if not r.get("applicable"):
            return f"No geographic profile for {r.get('subject')}: {r.get('reason')}"
        return (f"**Geographic profile of {r['subject']}** from {len(r['sites_used'])} sites: highest priority at "
                f"{r['peak']['lat']}, {r['peak']['lng']}; 50% of the surface in {r['search_area_km2']['50']} km², 80% in "
                f"{r['search_area_km2']['80']} km². {r['note']}")
    if mode == "statute":
        row = r.get("crosswalk")
        if not row:
            return f"{r['act']} {r['section']} is not in the cross-walk."
        head = (f"**{r['act']} {r['section']}** corresponds to "
                + (f"**BNS {row['bns']}**" if r["act"] == "IPC" else (f"**IPC {row['ipc']}**" if row.get("ipc") else "no IPC section"))
                + f" — {row['subject']}. {row['change']}")
        return head + ("\n" + "\n".join(r["findings"]) if r.get("findings") else "")
    if mode == "underreporting":
        lines = ["**Possible under-reporting**:"]
        lines += [f"- {x['district']}: " + " ".join(x["flags"]) for x in r["flagged"][:6]]
        return "\n".join(lines) + "\n" + r["note"]
    if mode == "assign_io":
        lines = [f"**Suggested IO** for {r.get('crime_type') or 'this case'}" + (f" at {r['station']}" if r.get("station") else "") + ":"]
        lines += [f"- {o['rank']} {o['name']} ({o['kgid']}) — {', '.join(o['reasons'])}" for o in r["officers"][:4]]
        return "\n".join(lines)
    return ""


def compose(calls_and_results):
    parts = []
    for (name, args), res in calls_and_results:
        if name == "investigate":
            parts.append(_say_investigate(args.get("mode", "case_check"), res))
            continue
        if name == "situation":
            parts.append(_say_situation(res))
            continue
        if name == "sightings":
            parts.append(_say_sightings(res))
            continue
        if name == "news":
            parts.append(_say_news(res))
            continue
        if name == "annual_forecast":
            parts.append(_say_annual(res))
            continue
        if name == "time_pattern":
            parts.append(_say_time(res))
            continue
        if name == "get_case":
            parts.append(_say_case(res))
        elif name == "get_person":
            parts.append(_say_person(res))
        elif name == "search_cases":
            parts.append(_say_cases(res))
        elif name == "aggregate":
            parts.append(_say_aggregate(res))
        elif name == "search_people":
            parts.append(_say_people(res))
        elif name == "get_officer":
            parts.append(_say_officer(res))
        elif name == "search_officers":
            parts.append(_say_officers(res))
        elif name == "search_registry":
            parts.append(_say_registry(res))
        elif name == "network":
            parts.append(_say_network(res))
        elif name == "insight":
            parts.append(_say_insight(args.get("topic"), res))
        elif name == "project_knowledge":
            parts.append(_say_knowledge(res))
        elif name == "describe_platform":
            parts.append(_say_platform(res))
        elif name == "model_info":
            parts.append(_say_model(res))
    return "\n\n".join(p for p in parts if p)


def _focused_answer(text, calls, results):
    """Pull the one fact a narrow question asks about out of a full dossier."""
    low = (text or "").lower()
    if len(results) != 1:
        return None
    (name, _args), res = results[0]
    if name == "get_case" and not res.get("error"):
        cid = res["case_id"]
        acc = res.get("accused") or []
        if _cue(low, ("accused", "suspect", "who did", "who committed", "culprit", "involved", "offender")):
            if not acc:
                return f"No accused are linked to FIR {cid} in the corpus."
            out = (f"FIR {cid} ({res.get('crime_type')}, {res.get('district')}) names {len(acc)} accused:\n"
                   + "\n".join(f"- {a.get('name')} ({a['suspect_id']}) — {a.get('relationship')}, {a.get('risk_level')} risk, "
                               f"{a.get('prior_firs', 0)} priors" + (", rowdy-sheeter" if a.get("rowdy_sheeter") else "")
                               + (", active warrant" if a.get("active_warrant") else "") for a in acc))
            if "warrant" in low:
                # Asked outright, so say it outright — silence reads as "not checked".
                wanted = [f"{a.get('name')} ({a['suspect_id']})" for a in acc if a.get("active_warrant")]
                verb = "has" if len(wanted) == 1 else "have"
                head = f"**{len(wanted)} of the {len(acc)} accused in FIR {cid} {verb} an active warrant**"
                out = head + (": " + ", ".join(wanted) + "." if wanted else ".") + "\n\n" + out
            return out
        if _cue(low, ("victim",)):
            v = res.get("victim") or {}
            return f"The victim in FIR {cid} is {v.get('name') or 'not recorded'}" + (f", aged {v.get('age')}" if v.get("age") else "") + (f" ({v.get('gender')})" if v.get("gender") else "") + "."
        if _cue(low, ("officer", "io ", "investigat", "who is handling", "handled")):
            io = res.get("investigating_officer") or {}
            return f"FIR {cid} is investigated by {io.get('name') or 'an officer'} ({io.get('officer_id')}, {io.get('rank') or 'rank n/a'}, {io.get('station') or res.get('police_station')})."
        if _cue(low, ("court", "trial", "hearing", "convict", "verdict")):
            c = res.get("court")
            if not c:
                return f"FIR {cid} has no court record yet — status is {res.get('status')}."
            return (f"FIR {cid} is in {c.get('court_name')} as {c.get('court_case_no')}: {c.get('conviction_status')}"
                    + (f", next hearing {c.get('next_hearing')}" if c.get("conviction_status") == "Pending Trial" else "") + ".")
        if _cue(low, ("status", "solved", "closed", "chargesheet", "progress")):
            cs = res.get("chargesheet")
            return (f"FIR {cid} is **{res.get('status')}**"
                    + (f"; chargesheet filed {cs.get('filed_on')}" if cs else "; no chargesheet recorded")
                    + f"; {res.get('arrests', {}).get('count', 0)} arrest(s).")
        if _cue(low, ("narrative", "what happened", "brief facts", "story", "describe")):
            return f"FIR {cid} narrative: “{res.get('narrative') or 'not recorded'}”"
        if _cue(low, ("similar", "related", "same mo", "pattern", "like this")):
            sc = res.get("similar_cases") or []
            return f"Cases with a similar MO to FIR {cid}:\n" + "\n".join(f"- {s['case_id']} ({s['district']}, {s['registered']}, {s['status']}) — shares {', '.join(s['shared_mo']) or 'crime type'}" for s in sc)
    if name == "get_person" and not res.get("error") and not res.get("ambiguous") and "offender_record" not in res:
        sid = res["suspect_id"]
        nm = res.get("name")
        if _cue(low, ("warrant",)):
            return f"{nm} ({sid}) {'HAS an active warrant' if res.get('active_warrant') else 'has no active warrant'} on file."
        if _cue(low, ("rowdy",)):
            return f"{nm} ({sid}) {'is' if res.get('rowdy_sheeter') else 'is not'} on the rowdy register."
        if _cue(low, ("associate", "co-offender", "partner", "accomplice", "works with", "network")):
            co = res.get("co_offenders") or []
            return (f"{nm} ({sid}) has {len(co)} co-offender(s) across shared cases:\n"
                    + "\n".join(f"- {c.get('name')} ({c['suspect_id']}): {c['shared_cases']} shared case(s), {c.get('risk_level')} risk" for c in co)
                    if co else f"{nm} ({sid}) shares no case with another offender.")
        if _cue(low, ("how many case", "cases", "history", "record", "crimes")) and res.get("cases"):
            return _say_person(res)
    return None


def offline_answer(text, cache, context=None, history=None):
    """Answer from tools alone. Returns dict or None when no plan applies."""
    idx = _index(cache)
    if idx is None:
        return None
    ents = extract_entities(text, idx, context, history)
    calls = plan(text, idx, ents)
    if not calls:
        return None
    results = [((n, a), run_tool(n, a, cache)) for n, a in calls]
    reply = _focused_answer(text, calls, results) or compose(results)
    if not reply:
        return None
    return {"reply": reply, "trace": [_trace(n, a, r) for (n, a), r in results],
            "entities": ents, "nav": _default_nav(results), "mode": "deterministic"}


def _default_nav(results):
    """A screen worth showing next to the answer, or None."""
    for (name, _a), res in results:
        if name == "get_case" and res.get("case_id"):
            return {"action": "open_case", "params": {"case_id": res["case_id"]}}
        if name == "get_person" and res.get("suspect_id"):
            return {"action": "open_suspect", "params": {"suspect_id": res["suspect_id"]}}
    return None


def _trace(name, args, res):
    label = {"get_case": "Read case dossier", "get_person": "Read person profile",
             "search_cases": "Searched FIRs", "aggregate": "Ranked FIRs", "search_people": "Searched offender registry",
             "get_officer": "Read officer record", "search_officers": "Searched officers",
             "search_registry": "Searched registry", "network": "Traversed link graph",
             "insight": "Read analytics", "model_info": "Read model registry",
             "project_knowledge": "Read platform docs", "describe_platform": "Inspected live system",
             "situation": "Read live situation", "sightings": "Checked CCTV / ANPR network",
             "news": "Read public headlines", "annual_forecast": "Forecast the year",
             "time_pattern": "Read the hour-by-weekday pattern", "investigate": "Ran the investigation workbench",
             "navigate": "Moved screen"}.get(name, name)
    detail = ", ".join(f"{v}" for k, v in (args or {}).items() if v not in (None, "", False) and k not in ("limit", "sort_by", "order"))
    n = None
    if isinstance(res, dict):
        n = res.get("total_matching", res.get("cases_in_corpus"))
    return {"tool": name, "label": label, "detail": detail[:80],
            "result": (f"{n:,} match" if isinstance(n, int) else ("error" if res.get("error") else "ok")) if isinstance(res, dict) else "ok"}


# =============================================================================
# LLM tool-calling loop
# =============================================================================
def _system_prompt(lang_directive, briefing, page, context):
    here = ""
    if context:
        if context.get("case_id"):
            here = f"\nThe officer is viewing case {context['case_id']}; 'this case', 'it' refer to it."
        elif context.get("suspect_id"):
            here = f"\nThe officer is viewing offender {context['suspect_id']}; 'he', 'she', 'this person' refer to them."
    try:
        pmap = copilot_knowledge.platform_map()
        live = copilot_knowledge.describe_live("summary")
    except Exception:
        pmap, live = "", {}
    tools = ", ".join(TOOLS)
    return (
        f"{lang_directive}\n\n"
        "You are Kavya — the AI chief of staff of KSP IntelHub, the crime-intelligence platform of the Karnataka State "
        "Police State Crime Records Bureau (SCRB). Think of yourself as the officer's JARVIS: you know every record, "
        "every screen, every model and every line of the platform's documentation, you reason in several steps, and "
        "you volunteer what the officer would want to know next. You are precise, calm, quick and candid about "
        "uncertainty.\n\n"
        "HOW YOU THINK (do this silently, show only the result)\n"
        "1. Decompose: split the request into the facts it needs. A multi-part question gets every part answered.\n"
        "2. Gather: call tools for every fact — several in one turn when they are independent. Never answer a "
        "question about a case, person, count, ranking, trend, page, route, model or policy from memory. "
        "Records the question names may already be PRE-FETCHED; use them.\n"
        "3. Chain: follow leads the results expose — accused in a case → their profiles → links between them; a district "
        "spike → its stations → its pendency; a model → its baselines and limits.\n"
        "4. Cross-check: if two results disagree or a number looks off, check again or say which source you trust.\n"
        "5. Synthesise: answer, then add ONE grounded insight the officer did not ask for but should see (a shared "
        "co-offender, a rising trend, an overdue chargesheet, a pattern across cases), only if the tools support it.\n\n"
        f"YOUR TOOLS: {tools}. project_knowledge searches the platform's own docs, code and pages, regenerated from the "
        "repository on every change — trust it over your general knowledge about IntelHub. describe_platform reads "
        "the running system (live routes, tables, pages, models).\n\n"
        "HOW YOU ANSWER\n"
        "- First sentence: the direct answer with the exact figure or fact, **bolded**. Then 2-6 tight bullets. "
        "~200 words unless the officer asks for depth; a briefing or comparison may use more.\n"
        "- Natural and confident; no filler, no restating the question, no 'As an AI'.\n"
        "- Ambiguity (a name shared by several people): say what you found and ask which one.\n"
        "- Person names appear as [PERSON_n] aliases — use them exactly; they become real names for the officer. "
        "Only state case numbers, SUS-ids and KGIDs that appear in tool results.\n"
        "- Call navigate when opening a screen helps (one case or one person → open it).\n"
        "- End with one line: FOLLOWUPS: <follow-up> | <follow-up> | <follow-up>\n\n"
        "LIMITS YOU KEEP\n"
        "- No output is evidence. Never say a person is guilty, will reoffend or should be arrested; a risk index orders "
        "attention and establishes nothing about a person. Ranked lists of people stay in the gated registry.\n"
        "- Personnel questions (who to suspend, transfer, punish or reward): answer the data question exactly as "
        "asked — 'worst' means lowest first (search_officers order=asc) — with each officer's caseload and the state "
        "average, name the confounders (case mix, workload, tenure, small samples), and say a suspension is decided by "
        "a departmental inquiry, not by a ranking. Point to the files to review; never name someone to suspend.\n"
        "- You search only the KSP IntelHub records. You do not look people up on the internet, in Aadhaar or in any "
        "outside database, and you say so if asked.\n"
        "- The corpus is simulated CCTNS-shaped data; say so if asked whether the records are real.\n"
        "- Outside policing and this platform, answer briefly and steer back.\n"
        f"{here}\n\nCURRENT PAGE: {page or '/'}\n\n"
        f"PLATFORM MAP (generated from the code):\n{pmap}\n"
        f"Knowledge index: {live.get('knowledge_chunks', '?')} passages from {live.get('documents', '?')} files, "
        f"{live.get('documented_routes', '?')} API routes.\n\n"
        f"LIVE BRIEFING (statewide aggregates):\n{briefing}"
    )


def _parse_followups(text):
    if not text:
        return text, []
    m = re.search(r"\n?\s*\**FOLLOW-?UPS?\**\s*:\s*(.+)$", text, re.I | re.S)
    if not m:
        return text.strip(), []
    ups = [u.strip(" -•*\"'") for u in re.split(r"\s*\|\s*|\n", m.group(1)) if u.strip(" -•*\"'")]
    return text[:m.start()].strip(), ups[:3]


def _models(p):
    """The provider's model, then its fallbacks. Groq meters tokens per minute per model,
    so a second model is a second budget rather than a second queue."""
    names = [os.environ.get(p["model_env"], p["model_default"])]
    if p.get("fallback_env"):
        names += os.environ.get(p["fallback_env"], p.get("fallback_default", "")).split(",")
    return list(dict.fromkeys(n.strip() for n in names if n and n.strip()))


def _retry_after(e):
    """Seconds a rate-limited model asked us to wait: the header, else Groq's message."""
    try:
        v = e.response.headers.get("retry-after")
        if v:
            return float(v)
    except Exception:
        pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", str(e))
    if m:
        return int(m.group(1) or 0) * 60 + float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
    return 20.0


def _chat(providers, messages, tools, deadline=None):
    """One completion against the first provider and model that answers before the
    deadline. Returns (message, provider)."""
    try:
        from openai import OpenAI
    except Exception as e:
        LOG.warning("openai client unavailable: %s", e)
        return None, None
    for p in providers:
        key = os.environ.get(p["key_env"], "")
        if not key or key.startswith(("nvapi-****", "gsk_****")):
            continue
        for model in _models(p):
            if _cooldown.get((p["name"], model), 0) > time.monotonic():
                continue
            left = (deadline - time.monotonic()) if deadline else LLM_TIMEOUT_S
            if left < 3:
                return None, None
            kwargs = dict(model=model, messages=messages, temperature=0.2, max_tokens=900)
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            if "gpt-oss" in model:
                # Reasoning tokens count against max_tokens and the per-minute budget; the
                # tools do the heavy lifting, so a short think is enough.
                kwargs["reasoning_effort"] = os.environ.get("COPILOT_REASONING_EFFORT", "low")
            # No client-side retries: on a 429 the client sleeps out the wait, and the
            # next model's budget is free now.
            client = OpenAI(base_url=p["base_url"], api_key=key, timeout=min(LLM_TIMEOUT_S, left), max_retries=0)
            for attempt in range(2):
                try:
                    resp = client.chat.completions.create(**kwargs)
                    return resp.choices[0].message, p["name"]
                except Exception as e:
                    # A tool call that fails the provider's schema check is a sampling slip, not
                    # an outage: one fresh sample usually gets it right.
                    if attempt == 0 and "tool_use_failed" in str(e):
                        LOG.info("copilot provider %s rejected a tool call; retrying once", p["name"])
                        continue
                    if getattr(e, "status_code", None) == 429 or "rate_limit" in str(e):
                        wait = _retry_after(e)
                        _cooldown[(p["name"], model)] = time.monotonic() + wait
                        LOG.info("copilot %s/%s rate-limited for %.1fs; trying the next model", p["name"], model, wait)
                    elif getattr(e, "status_code", None) == 404 or "decommissioned" in str(e):
                        _cooldown[(p["name"], model)] = time.monotonic() + 3600
                        LOG.warning("copilot %s/%s is not served (%s); skipping it for an hour", p["name"], model, e)
                    else:
                        LOG.warning("copilot provider %s/%s failed: %s", p["name"], model, e)
                    break
    return None, None


def llm_answer(text, cache, providers, history=None, lang_directive="", briefing="", page=None,
               context=None, extra_facts=None):
    """Tool-calling answer. Returns dict or None if no provider produced one."""
    idx = _index(cache)
    if idx is None:
        return None
    ents = extract_entities(text, idx, context, history)
    pre_calls = plan(text, idx, ents)[:4]
    pre = [((n, a), run_tool(n, a, cache)) for n, a in pre_calls]
    trace = [_trace(n, a, r) for (n, a), r in pre]

    user = text
    blocks = []
    if ents.get("resolved_from_context"):
        blocks.append(f"(pronoun in the question refers to {ents['resolved_from_context']})")
    if pre:
        blocks.append("PRE-FETCHED RECORDS:\n" + "\n".join(
            f"[{n}({_dump(a, 200)})] → {_dump(mask_for_llm(r), 5000)}" for (n, a), r in pre))
    if extra_facts:
        blocks.append("VERIFIED PLATFORM FACT: " + extra_facts)
    if blocks:
        user = f"{text}\n\n" + "\n\n".join(blocks)

    hist = []
    for m in (history or [])[-8:]:
        role, content = m.get("role"), (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            hist.append({"role": role, "content": privacy_mask_text(content, idx)[:1500]})
    messages = [{"role": "system", "content": _system_prompt(lang_directive, briefing, page, context)}] + hist + \
               [{"role": "user", "content": user}]
    tools = tool_specs()
    nav = None
    provider = None
    deadline = time.monotonic() + DEADLINE_S
    for step in range(MAX_TOOL_STEPS + 1):
        # Near the deadline, stop gathering and answer from what is already in hand.
        last = step >= MAX_TOOL_STEPS or (step > 0 and deadline - time.monotonic() < FINAL_RESERVE_S)
        msg, provider = _chat(providers, _flatten(messages) if last else messages, None if last else tools, deadline)
        if msg is None and step > 0 and not last:
            # A provider that rejects a malformed tool call mid-loop still has the
            # evidence gathered so far in context; ask once more for a plain answer.
            msg, provider = _chat(providers, _flatten(messages), None, deadline)
        if msg is None:
            return None
        calls = [] if last else (getattr(msg, "tool_calls", None) or [])
        if not calls:
            # Some fallback models think aloud in <think> tags; the officer gets the answer.
            content = re.sub(r"<think>.*?</think>", "", msg.content or "", flags=re.S).strip()
            if not content:
                return None
            reply, ups = _parse_followups(privacy.restore(content))
            return {"reply": reply, "followups": [privacy.restore(u) for u in ups], "trace": trace,
                    "nav": nav or _default_nav(pre), "entities": ents, "mode": f"llm:{provider}"}
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                                        for c in calls]})
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except ValueError:
                args = {}
            args = {k: privacy.restore(v) if isinstance(v, str) else v for k, v in (args or {}).items()}
            if c.function.name == "navigate":
                act = args.pop("action", None)
                if act in NAV_ACTIONS:
                    nav = {"action": act, "params": {k: v for k, v in args.items() if v}}
                result = {"ok": True}
            else:
                result = run_tool(c.function.name, args, cache)
            trace.append(_trace(c.function.name, args, result))
            messages.append({"role": "tool", "tool_call_id": c.id,
                             "content": _dump(mask_for_llm(result), 6000)})
    return None


def _flatten(messages):
    """The same conversation with tool turns folded into text, for a tool-less retry."""
    out, results = [], []
    for m in messages:
        if m.get("role") == "tool":
            results.append(m["content"])
        elif m.get("tool_calls"):
            continue
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    if results:
        out.append({"role": "user", "content": "TOOL RESULTS GATHERED SO FAR:\n" + "\n".join(results)[:12000]
                    + "\n\nAnswer the question above from these results."})
    return out


def privacy_mask_text(text, idx):
    """Alias registry names appearing in conversation history before it is re-sent."""
    if not text:
        return text
    for name, _hits in find_names(text, idx):
        text = re.sub(re.escape(name), privacy.mask(name), text, flags=re.I)
    return text


def speech_summary(reply, limit=260):
    """A short spoken version: the first sentence or two, without markup or long ids."""
    t = re.sub(r"\*\*|__|`|#+\s*", "", reply or "").replace("→", ", then").replace("·", ",")
    lines = [ln.strip(" -•") for ln in t.split("\n") if ln.strip(" -•")]
    t = lines[0] if lines else t.strip()
    if t.endswith(":") and len(lines) > 1:
        t = f"{t} {lines[1]}."
    sentences = re.split(r"(?<=[.!?])\s+", t)
    out = ""
    for s in sentences:
        if len(out) + len(s) > limit and out:
            break
        out = (out + " " + s).strip()
    return out[:limit]
