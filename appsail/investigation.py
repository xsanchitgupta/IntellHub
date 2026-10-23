"""Investigation workbench: record integrity, crime series, competing hypotheses,
geographic profiling, under-reporting and investigating-officer assignment.

Everything here is arithmetic over the records — no model generates a number, a
flag or a lead. Every finding carries the record identifiers it rests on (FIR, charge-
sheet, court case, vehicle, camera, registry person), so an officer can open the record
and check it, and a finding without a citation is a bug.

* ``GET /investigate/case/<case_id>`` — one FIR: integrity and cross-source
  contradictions, the questions they raise for the IO, competing hypotheses about who
  committed it, the statute check, and any series the FIR belongs to.
* ``GET /investigate/integrity`` — the same checks statewide: counts by check and
  district, and the FIRs carrying the most serious ones.
* ``GET /investigate/series`` and ``/series/<id>`` — FIRs that look like one offender
  across police-station lines: linked by a shared accused, or by modus operandi.
* ``GET /investigate/geoprofile`` — a Rossmo geographic profile of a series or a person.
* ``GET /investigate/person/<id>/timeline`` — one person across every source, with
  identity conflicts (one fingerprint on two registry records; ages that do not add up).
* ``GET /investigate/underreporting`` — districts whose recorded crime is far below what
  comparable districts record, and Dial-112 calls that never became an FIR.
* ``GET /investigate/recommend-io`` — who should investigate a new FIR, with reasons.

What is deliberately left out
-----------------------------
* Demographics are never evidence. Religion, caste, gender, age, occupation and
  community do not enter a hypothesis, a series or a recommendation; the hypothesis
  matrix says so on every response.
* Registry status (rowdy-sheet, warrant, risk band) says who someone is, not what
  happened in this FIR, so it is not scored as evidence either.
* A hypothesis ranking is Heuer's: fewest inconsistencies first. It orders where to
  look; it identifies no one.
"""
import hashlib
import math
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from flask import Blueprint, jsonify, request

import audit
import auth
import statutes

bp = Blueprint("investigation", __name__)
API = "/serverless/api"

_cache_getter = lambda: {}      # noqa: E731 — replaced by configure()
_suggest = None                 # (case_id, k) -> [suggestion]; main.py wires analytics in
_lock = threading.Lock()
_state = {"key": None, "ix": None}
# The flag pass and the series pass are the two heaviest things a request can start.
# Two requests arriving together on a cold index each built their own copy, and in a
# 512 MB container that second copy is what tipped the worker over.
_build_lock = threading.RLock()

# Series: how alike two FIRs must be to read as one offender's work.
SERIES_WINDOW_DAYS = 90
SERIES_RADIUS_KM = 50.0
SERIES_MIN_MO_MATCH = 3          # of weapon, vehicle, target, time of day — on top of the same crime type
SERIES_MIN_CASES = 3
# Rossmo: Rossmo's own exponents; the buffer is set from the data.
ROSSMO_F = 1.2
ROSSMO_G = 1.2
ROSSMO_CLUSTER_KM = 40.0         # one hunting area; a statewide history is not one
ROSSMO_GRID = 60
AGE_SPREAD_YEARS = 5             # implied birth years further apart than this are not one person's
HEARING_GRACE_DAYS = 30
EXCLUDED_FACTORS = ["religion", "caste", "gender", "age", "occupation", "community"]


def configure(cache_getter, suggest=None):
    global _cache_getter, _suggest
    _cache_getter = cache_getter
    if suggest is not None:
        _suggest = suggest


def today():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def as_of(ix):
    """The date the records are judged against: the day of the extract, i.e. its latest
    registration, the same reference pendency and the trend alerts use. Against the
    wall clock the findings on a fixed corpus changed every day, and this simulated one,
    which runs to 2026-12-28, had its last ten weeks flagged as dated in the future."""
    last = ix["cm"]["CrimeRegisteredDate"].max()
    return last.date() if pd.notna(last) else today()


# =============================================================================
# Helpers
# =============================================================================
def _t(c, name):
    df = c.get(name)
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


def _clean(v):
    """JSON-safe: numpy scalars to Python, NaN/NaT to None, recursively."""
    if isinstance(v, Flag):
        return _clean(v.as_dict())
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (pd.Timestamp, datetime)):
        return None if pd.isna(v) else v.isoformat(sep=" ", timespec="minutes")
    if v is pd.NaT:
        return None
    return v


def _d(ts):
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).strftime("%Y-%m-%d")


def _km(lat1, lng1, lat2, lng2):
    """Haversine km; vectorised over numpy arrays."""
    lat1, lng1, lat2, lng2 = (np.radians(np.asarray(x, dtype=float)) for x in (lat1, lng1, lat2, lng2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _fir(case_id, label=None):
    return {"kind": "fir", "id": str(case_id), "label": label or f"FIR {case_id}"}


def _person(pid, name=None):
    return {"kind": "person", "id": str(pid), "label": f"{pid}" + (f" ({name})" if name else "")}


# =============================================================================
# Index — built once per corpus version, shared by every view
# =============================================================================
def _key(c):
    names = ("CaseMaster", "firs", "Accused", "ArrestSurrender", "ChargesheetDetails", "CourtCasesRegistry",
             "vehicles", "cctv", "ActSectionAssociation", "criminals")
    return tuple((id(c.get(n)), len(c.get(n)) if isinstance(c.get(n), pd.DataFrame) else -1) for n in names)


def index():
    """The shared index, rebuilt when any source frame is replaced (a resync or an FIR
    registration rebinds the frames, so identity and length are the version)."""
    c = _cache_getter() or {}
    if not isinstance(c.get("CaseMaster"), pd.DataFrame) or not isinstance(c.get("firs"), pd.DataFrame):
        return None
    key = _key(c)
    with _lock:
        if _state["key"] == key and _state["ix"] is not None:
            return _state["ix"]
        ix = _build(c)
        _state.update(key=key, ix=ix)
        return ix


def _build(c):
    cm = _t(c, "CaseMaster")
    cols = ["CaseMasterID", "CrimeNo", "PoliceStationID", "PolicePersonID", "CaseStatusID",
            "IncidentFromDate", "IncidentToDate", "InfoReceivedPSDate", "CrimeRegisteredDate"]
    cmx = cm[[x for x in cols if x in cm.columns]].copy()
    for col in ("IncidentFromDate", "IncidentToDate", "InfoReceivedPSDate", "CrimeRegisteredDate"):
        cmx[col] = pd.to_datetime(cmx[col], errors="coerce")
    cmx["case_id"] = cmx["CrimeNo"].astype(str)
    f = _t(c, "firs")
    geo_cols = ["CaseMasterID", "district", "police_station", "crime_type", "weapon_used", "vehicle_used",
                "target_type", "time_of_day", "latitude", "longitude", "status"]
    cmx = cmx.merge(f[[x for x in geo_cols if x in f.columns]], on="CaseMasterID", how="left")
    cmx["latitude"] = pd.to_numeric(cmx["latitude"], errors="coerce")
    cmx["longitude"] = pd.to_numeric(cmx["longitude"], errors="coerce")
    cmx = cmx.drop_duplicates("CaseMasterID")
    cmx.index = pd.Index(cmx["CaseMasterID"].to_numpy())      # unnamed: merges stay on the column
    by_case_id = dict(zip(cmx["case_id"], cmx["CaseMasterID"]))

    asa = _t(c, "ActSectionAssociation")
    sections = defaultdict(list)
    for cmid, act, sec in zip(asa.get("CaseMasterID", []), asa.get("ActID", []), asa.get("SectionID", [])):
        pair = (str(act), str(sec))
        if pair not in sections[cmid]:
            sections[cmid].append(pair)

    # Dates are parsed once per column: per-row parsing was most of the build time.
    cs = _t(c, "ChargesheetDetails")
    chargesheet = {}
    if len(cs):
        cs_dates = pd.to_datetime(cs["csdate"], errors="coerce")
        for cmid, csid, when in zip(cs["CaseMasterID"], cs["CSID"], cs_dates):
            if cmid not in chargesheet:
                chargesheet[cmid] = {"id": str(csid), "date": when}

    cc = _t(c, "CourtCasesRegistry")
    court = {}
    if len(cc):
        hearings = pd.to_datetime(cc["next_hearing"], errors="coerce")
        for cmid, no, name, verdict, nh in zip(cc["CaseMasterID"], cc["court_case_no"], cc["court_name"],
                                                cc["conviction_status"], hearings):
            court.setdefault(cmid, {"id": str(no), "court": name, "verdict": verdict, "next_hearing": nh})

    ar = _t(c, "ArrestSurrender").copy()
    if len(ar):
        ar["date"] = pd.to_datetime(ar["ArrestSurrenderDate"], errors="coerce")
    arrests_by_case = defaultdict(list)
    arrests_by_accused = defaultdict(list)
    for row in ar.itertuples(index=False):
        rec = {"id": str(row.ArrestSurrenderID), "date": row.date, "case": row.CaseMasterID,
               "accused": getattr(row, "AccusedMasterID", None)}
        arrests_by_case[row.CaseMasterID].append(rec)
        arrests_by_accused[rec["accused"]].append(rec)

    acc = _t(c, "Accused")
    A = acc[["AccusedMasterID", "CaseMasterID", "PersonID", "AgeYear"]].merge(
        cmx[["CaseMasterID", "case_id", "IncidentFromDate", "district", "police_station", "crime_type", "weapon_used",
             "vehicle_used", "target_type", "time_of_day", "latitude", "longitude", "status"]],
        on="CaseMasterID", how="inner")
    A["PersonID"] = A["PersonID"].astype(str)
    A = A.sort_values(["PersonID", "IncidentFromDate"]).reset_index(drop=True)
    by_person = {pid: idx for pid, idx in A.groupby("PersonID", sort=False).indices.items()}
    accused_by_case = defaultdict(list)
    for i, (cmid, pid) in enumerate(zip(A["CaseMasterID"], A["PersonID"])):
        accused_by_case[cmid].append(i)

    veh = _t(c, "vehicles").copy()
    vehicles_by_case = defaultdict(list)
    if len(veh):
        veh["theft"] = pd.to_datetime(veh["theft_date"], errors="coerce")
        for row in veh.itertuples(index=False):
            lc = str(getattr(row, "linked_case_id", "") or "")
            if len(lc) > 5:
                vehicles_by_case[lc].append({"id": str(row.vehicle_number), "make_model": getattr(row, "make_model", None),
                                             "theft": row.theft, "status": getattr(row, "recovery_status", None)})

    cams = _t(c, "cctv")
    sightings = defaultdict(list)
    cam_ts = pd.to_datetime(cams["timestamp"], errors="coerce") if len(cams) else []
    for row, ts in zip(cams.itertuples(index=False), cam_ts):
        faces = getattr(row, "detected_faces", None)
        if not isinstance(faces, str) or not faces.strip():
            continue
        for pid in faces.split(";"):
            pid = pid.strip()
            if pid:
                sightings[pid].append({"camera": str(row.camera_id), "ts": ts, "lat": float(row.lat), "lng": float(row.lng),
                                       "district": getattr(row, "district", None), "clip": getattr(row, "video_url", None),
                                       "case_link": str(getattr(row, "case_link", "") or "")})

    crim = _t(c, "criminals")
    people = {}
    by_fingerprint = defaultdict(list)
    if len(crim):
        idcol = "criminal_id" if "criminal_id" in crim.columns else "PersonID"
        for row in crim.drop_duplicates(idcol).to_dict("records"):
            pid = str(row[idcol])
            people[pid] = {"name": row.get("name"), "alias": row.get("alias"), "fingerprint": row.get("fingerprint_id")}
            fp = row.get("fingerprint_id")
            if isinstance(fp, str) and fp:
                by_fingerprint[fp].append(pid)
    shared_fp = {fp: ids for fp, ids in by_fingerprint.items() if len(ids) > 1}

    return {"cm": cmx, "by_case_id": by_case_id, "sections": sections, "chargesheet": chargesheet, "court": court,
            "arrests_by_case": arrests_by_case, "arrests_by_accused": arrests_by_accused, "A": A,
            "by_person": by_person, "accused_by_case": accused_by_case, "vehicles": vehicles_by_case,
            "sightings": sightings, "people": people, "shared_fp": shared_fp, "flags": {}, "series": None}


def _resolve(ix, case_ref):
    """CaseMasterID for a CrimeNo, a CaseMasterID, or FIR-<id>."""
    s = str(case_ref or "").strip()
    if s in ix["by_case_id"]:
        return ix["by_case_id"][s]
    if s.upper().startswith("FIR-"):
        s = s[4:]
    try:
        n = int(s)
    except ValueError:
        return None
    return n if n in ix["cm"].index else None


# =============================================================================
# Record integrity and cross-source contradictions
# =============================================================================
CHECKS = {
    "future_incident": ("high", "Incident dated in the future"),
    "future_registration": ("high", "Registration dated in the future"),
    "information_before_incident": ("medium", "Information reached the station before the incident began"),
    "registered_before_information": ("medium", "FIR registered before the information reached the station"),
    "incident_ends_before_start": ("medium", "Incident ends before it starts"),
    "registration_delay": ("low", "Delay in registering the FIR"),
    "arrest_before_incident": ("high", "Arrest dated before the incident"),
    "chargesheet_before_registration": ("high", "Charge-sheet dated before the FIR"),
    "future_chargesheet": ("high", "Charge-sheet dated in the future"),
    "chargesheet_status_lag": ("medium", "Charge-sheet filed, status still Under Investigation"),
    "status_without_chargesheet": ("medium", "Marked Charge Sheeted with no charge-sheet on record"),
    "verdict_status_lag": ("medium", "Court verdict recorded, status still Under Investigation"),
    "hearing_overdue": ("low", "Court record not updated since its last hearing date"),
    "repealed_code": ("medium", "Booked under the IPC after the BNS came into force"),
    "bns_before_commencement": ("medium", "Booked under the BNS before it came into force"),
    "vehicle_before_theft": ("medium", "Linked vehicle was stolen only after the incident"),
    "identity_age_conflict": ("medium", "Accused's recorded ages do not belong to one person"),
    "shared_fingerprint": ("high", "Accused's fingerprint is on another registry identity"),
}

QUESTIONS = {
    "future_incident": "Correct the date of occurrence: {when} is after the last day these records cover ({asof}).",
    "future_registration": "Correct the registration date: {when} is after the last day these records cover ({asof}).",
    "information_before_incident": "Check the timestamps: information is logged at {info}, before the incident began at {when}.",
    "registered_before_information": "Check the timestamps: the FIR is dated {reg}, before the information was received on {info}.",
    "incident_ends_before_start": "Correct the incident window: it ends ({until}) before it starts ({when}).",
    "registration_delay": "Record the reason for the {days}-day delay between information ({info}) and registration ({reg}).",
    "arrest_before_incident": "Verify arrest {ref}: it is dated {date}, before the incident on {when}.",
    "chargesheet_before_registration": "Verify charge-sheet {ref}: it is dated {date}, before the FIR was registered on {reg}.",
    "future_chargesheet": "Correct the date of charge-sheet {ref}: {date} is after the last day these records cover ({asof}).",
    "chargesheet_status_lag": "Update the case status: charge-sheet {ref} was filed on {date}.",
    "status_without_chargesheet": "Attach the charge-sheet, or correct the status: no charge-sheet is on record.",
    "verdict_status_lag": "Update the case status: court case {ref} records '{verdict}'.",
    "hearing_overdue": "Update court case {ref}: its next hearing ({date}) is past and the trial still shows pending.",
    "repealed_code": "Alter the section: {message}",
    "bns_before_commencement": "Alter the section: {message}",
    "vehicle_before_theft": "Confirm the vehicle link: {vehicle} was reported stolen on {theft}, {days} days after this incident ({when}).",
    "identity_age_conflict": "Verify {pid}'s identity documents: aged {age} here ({year}), but {other_age} in FIR {other} ({other_year}).",
    "shared_fingerprint": "Resolve the identity of {pid}: fingerprint {fp} is also recorded for {others}.",
}


class Flag:
    """One finding. Read like the dict it is served as (flag["code"], flag["cites"]).

    About 17,000 are held for the day, and as dicts — each with a list of citation
    dicts — they were the largest thing the app kept: 37 MB, in a 512 MB container that
    is replaced when it runs close to its limit. Slots and citation tuples hold the same
    content in a fraction of it; as_dict() is what leaves the module."""
    __slots__ = ("code", "detail", "_cites", "question", "suggestion")

    def __init__(self, code, detail, cites, question, suggestion=None):
        self.code, self.detail, self.question, self.suggestion = code, detail, question, suggestion
        self._cites = tuple((c["kind"], c["id"], c["label"]) for c in cites)

    severity = property(lambda self: CHECKS[self.code][0])
    title = property(lambda self: CHECKS[self.code][1])
    cites = property(lambda self: [{"kind": k, "id": i, "label": lbl} for k, i, lbl in self._cites])

    def __getitem__(self, key):
        if key not in ("code", "severity", "title", "detail", "cites", "question", "suggestion"):
            raise KeyError(key)
        return getattr(self, key)

    def get(self, key, default=None):
        try:
            v = self[key]
        except KeyError:
            return default
        return default if v is None else v

    def as_dict(self):
        out = {"code": self.code, "severity": self.severity, "title": self.title, "detail": self.detail,
               "cites": self.cites, "question": self.question}
        if self.suggestion is not None:
            out["suggestion"] = self.suggestion
        return out


def _flag(code, detail, cites, suggestion=None, **q):
    return Flag(code, detail, cites, QUESTIONS[code].format(**q), suggestion)


def integrity_flags(ix, on=None):
    """{CaseMasterID: [flag]} for every FIR, computed once per day per corpus version."""
    on = on or as_of(ix)
    cached = ix["flags"].get(on)
    if cached is not None:
        return cached
    with _build_lock:
        cached = ix["flags"].get(on)
        if cached is not None:
            return cached
        return _integrity_flags(ix, on)


def _integrity_flags(ix, on):
    cm = ix["cm"]
    out = defaultdict(list)
    day = pd.Timestamp(on)

    def each(mask):
        return cm[mask.fillna(False)].itertuples(index=False)

    for r in each(cm["IncidentFromDate"].dt.normalize() > day):
        out[r.CaseMasterID].append(_flag("future_incident", f"Incident is dated {_d(r.IncidentFromDate)}; the records run to {on}.",
                                         [_fir(r.case_id)], when=_d(r.IncidentFromDate), asof=on))
    for r in each(cm["CrimeRegisteredDate"].dt.normalize() > day):
        out[r.CaseMasterID].append(_flag("future_registration", f"Registered {_d(r.CrimeRegisteredDate)}; the records run to {on}.",
                                         [_fir(r.case_id)], when=_d(r.CrimeRegisteredDate), asof=on))
    for r in each(cm["InfoReceivedPSDate"] < cm["IncidentFromDate"]):
        out[r.CaseMasterID].append(_flag(
            "information_before_incident", f"Information {r.InfoReceivedPSDate:%Y-%m-%d %H:%M}, incident {r.IncidentFromDate:%Y-%m-%d %H:%M}.",
            [_fir(r.case_id)], info=f"{r.InfoReceivedPSDate:%Y-%m-%d %H:%M}", when=f"{r.IncidentFromDate:%Y-%m-%d %H:%M}"))
    for r in each(cm["CrimeRegisteredDate"].dt.normalize() < cm["InfoReceivedPSDate"].dt.normalize()):
        out[r.CaseMasterID].append(_flag(
            "registered_before_information", f"Registered {_d(r.CrimeRegisteredDate)}, information {_d(r.InfoReceivedPSDate)}.",
            [_fir(r.case_id)], reg=_d(r.CrimeRegisteredDate), info=_d(r.InfoReceivedPSDate)))
    for r in each(cm["IncidentToDate"] < cm["IncidentFromDate"]):
        out[r.CaseMasterID].append(_flag(
            "incident_ends_before_start", f"From {r.IncidentFromDate:%Y-%m-%d %H:%M} to {r.IncidentToDate:%Y-%m-%d %H:%M}.",
            [_fir(r.case_id)], when=f"{r.IncidentFromDate:%Y-%m-%d %H:%M}", until=f"{r.IncidentToDate:%Y-%m-%d %H:%M}"))
    delay = (cm["CrimeRegisteredDate"].dt.normalize() - cm["InfoReceivedPSDate"].dt.normalize()).dt.days
    late = cm.assign(delay_days=delay)[delay.fillna(0) > 1]
    for r in late.itertuples(index=False):
        out[r.CaseMasterID].append(_flag(
            "registration_delay", f"Registered {int(r.delay_days)} days after the information reached the station.",
            [_fir(r.case_id)], days=int(r.delay_days), info=_d(r.InfoReceivedPSDate), reg=_d(r.CrimeRegisteredDate)))

    for r in cm.itertuples(index=False):
        cmid = r.CaseMasterID
        csr = ix["chargesheet"].get(cmid)
        if csr is not None:
            cs_cite = {"kind": "chargesheet", "id": csr["id"], "label": f"Charge-sheet {csr['id']}"}
            if pd.notna(csr["date"]) and csr["date"].normalize() > day:
                out[cmid].append(_flag("future_chargesheet", f"Charge-sheet {csr['id']} is dated {_d(csr['date'])}; the records run to {on}.",
                                       [_fir(r.case_id), cs_cite], ref=csr["id"], date=_d(csr["date"]), asof=on))
            if r.CaseStatusID == 1:
                out[cmid].append(_flag("chargesheet_status_lag", f"Charge-sheet {csr['id']} dated {_d(csr['date'])}.",
                                       [_fir(r.case_id), {"kind": "chargesheet", "id": csr["id"], "label": f"Charge-sheet {csr['id']}"}],
                                       ref=csr["id"], date=_d(csr["date"])))
            if pd.notna(csr["date"]) and pd.notna(r.CrimeRegisteredDate) and csr["date"] < r.CrimeRegisteredDate.normalize():
                out[cmid].append(_flag("chargesheet_before_registration",
                                       f"Charge-sheet {_d(csr['date'])}, FIR {_d(r.CrimeRegisteredDate)}.",
                                       [_fir(r.case_id), {"kind": "chargesheet", "id": csr["id"], "label": f"Charge-sheet {csr['id']}"}],
                                       ref=csr["id"], date=_d(csr["date"]), reg=_d(r.CrimeRegisteredDate)))
        elif r.CaseStatusID == 2:
            out[cmid].append(_flag("status_without_chargesheet", "Status is Charge Sheeted; the charge-sheet register has no entry.",
                                   [_fir(r.case_id)]))
        crt = ix["court"].get(cmid)
        if crt is not None:
            cite = {"kind": "court", "id": crt["id"], "label": f"Court case {crt['id']}"}
            if crt["verdict"] in ("Convicted", "Acquitted") and r.CaseStatusID == 1:
                out[cmid].append(_flag("verdict_status_lag", f"{crt['court']}: {crt['verdict']}.", [_fir(r.case_id), cite],
                                       ref=crt["id"], verdict=crt["verdict"]))
            nh = crt["next_hearing"]
            if crt["verdict"] == "Pending Trial" and pd.notna(nh) and nh < day - pd.Timedelta(days=HEARING_GRACE_DAYS):
                out[cmid].append(_flag("hearing_overdue", f"{crt['court']}: next hearing {_d(nh)}, still pending.",
                                       [_fir(r.case_id), cite], ref=crt["id"], date=_d(nh)))
        for a in ix["arrests_by_case"].get(cmid, ()):
            if pd.notna(a["date"]) and pd.notna(r.IncidentFromDate) and a["date"] < r.IncidentFromDate.normalize():
                out[cmid].append(_flag("arrest_before_incident", f"Arrest {a['id']} on {_d(a['date'])}.",
                                       [_fir(r.case_id), {"kind": "arrest", "id": a["id"], "label": f"Arrest {a['id']}"}],
                                       ref=a["id"], date=_d(a["date"]), when=_d(r.IncidentFromDate)))
        for s in statutes.check(ix["sections"].get(cmid, ()), r.IncidentFromDate):
            out[cmid].append(_flag(s["code"], s["message"], [_fir(r.case_id)], suggestion=s["suggestion"], message=s["message"]))
        for v in ix["vehicles"].get(r.case_id, ()):
            if pd.notna(v["theft"]) and pd.notna(r.IncidentFromDate) and v["theft"] > r.IncidentFromDate.normalize():
                days = int((v["theft"] - r.IncidentFromDate.normalize()).days)
                out[cmid].append(_flag(
                    "vehicle_before_theft",
                    f"{v['id']} ({v['make_model']}) reported stolen {_d(v['theft'])}; incident {_d(r.IncidentFromDate)}.",
                    [_fir(r.case_id), {"kind": "vehicle", "id": v["id"], "label": f"Stolen-vehicle record {v['id']}"}],
                    vehicle=v["id"], theft=_d(v["theft"]), days=days, when=_d(r.IncidentFromDate)))

    # Identity: implied birth years across a person's FIRs, and fingerprints shared by two records.
    A = ix["A"]
    birth = A["IncidentFromDate"].dt.year - pd.to_numeric(A["AgeYear"], errors="coerce")
    for pid, rows in ix["by_person"].items():
        if len(rows) < 2:
            continue
        b = birth.iloc[rows]
        if b.isna().all() or b.max() - b.min() <= AGE_SPREAD_YEARS:
            continue
        med = b.median()
        for i in rows:
            if pd.isna(birth.iloc[i]) or abs(birth.iloc[i] - med) <= 3:
                continue
            far = max(rows, key=lambda j: abs(birth.iloc[j] - birth.iloc[i]) if pd.notna(birth.iloc[j]) else -1)
            me, other = A.iloc[i], A.iloc[far]
            out[me["CaseMasterID"]].append(_flag(
                "identity_age_conflict",
                f"{pid} is recorded aged {int(me['AgeYear'])} in {me['IncidentFromDate']:%Y} here and {int(other['AgeYear'])} "
                f"in {other['IncidentFromDate']:%Y} in FIR {other['case_id']} — birth years {int(birth.iloc[i])} and "
                f"{int(birth.iloc[far])}.",
                [_fir(me["case_id"]), _fir(other["case_id"]), _person(pid, ix["people"].get(pid, {}).get("name"))],
                pid=pid, age=int(me["AgeYear"]), year=f"{me['IncidentFromDate']:%Y}", other_age=int(other["AgeYear"]),
                other=other["case_id"], other_year=f"{other['IncidentFromDate']:%Y}"))
    for fp, ids in ix["shared_fp"].items():
        for pid in ids:
            others = [o for o in ids if o != pid]
            label = ", ".join(f"{o} ({ix['people'].get(o, {}).get('name')})" for o in others)
            for i in ix["by_person"].get(pid, ()):
                me = A.iloc[i]
                out[me["CaseMasterID"]].append(_flag(
                    "shared_fingerprint", f"Fingerprint {fp} of {pid} is also on {label}.",
                    [_fir(me["case_id"]), _person(pid, ix["people"].get(pid, {}).get("name"))] + [_person(o, ix["people"].get(o, {}).get("name")) for o in others],
                    pid=pid, fp=fp, others=label))

    order = {"high": 0, "medium": 1, "low": 2}
    for flags in out.values():
        flags.sort(key=lambda fl: order[fl["severity"]])
    out = dict(out)
    ix["flags"] = {on: out}          # one day kept; yesterday's "future" is today's past
    return out


def integrity_summary(ix, district=None, code=None, limit=50, on=None):
    flags = integrity_flags(ix, on)
    cm = ix["cm"]
    by_code, by_district = Counter(), defaultdict(Counter)
    rows = []
    for cmid, fl in flags.items():
        if cmid not in cm.index:
            continue
        r = cm.loc[cmid]
        if district and r["district"] != district:
            continue
        sel = [x for x in fl if not code or x["code"] == code]
        if not sel:
            continue
        for x in sel:
            by_code[x["code"]] += 1
            by_district[r["district"]][x["code"]] += 1
        rows.append((sum({"high": 9, "medium": 3, "low": 1}[x["severity"]] for x in sel), r["case_id"], r, sel))
    rows.sort(key=lambda t: (-t[0], t[1]))
    total = int(len(cm) if not district else (cm["district"] == district).sum())
    return {
        "as_of": str(on or as_of(ix)), "firs_checked": total,
        "firs_flagged": len(rows),
        "checks": [{"code": k, "severity": CHECKS[k][0], "title": CHECKS[k][1], "count": int(by_code.get(k, 0))}
                   for k in CHECKS],
        "by_district": sorted(({"district": d, "total": int(sum(cnt.values())), **{k: int(v) for k, v in cnt.items()}}
                               for d, cnt in by_district.items()), key=lambda x: -x["total"]),
        "cases": [{"case_id": cid, "district": r["district"], "station": r["police_station"], "crime_type": r["crime_type"],
                   "status": r["status"], "flags": [{"code": x["code"], "severity": x["severity"], "title": x["title"]} for x in sel]}
                  for _, cid, r, sel in rows[:limit]],
    }


# =============================================================================
# Crime series — one offender's work across police-station lines
# =============================================================================
MO_FIELDS = ("weapon_used", "vehicle_used", "target_type", "time_of_day")


def _series_id(kind, members):
    h = hashlib.sha1(",".join(sorted(str(m) for m in members)).encode()).hexdigest()[:8].upper()
    return f"{'MO' if kind == 'mo' else 'OF'}-{h}"


def series_index(ix):
    """Both kinds of series, built once per corpus version.

    * ``mo``: FIRs of the same crime type that agree on at least SERIES_MIN_MO_MATCH of
      weapon, vehicle, target and time of day, within SERIES_WINDOW_DAYS and
      SERIES_RADIUS_KM of each other, chained; kept at SERIES_MIN_CASES or more across
      two or more police stations.
    * ``offender``: one registry person named in SERIES_MIN_CASES or more FIRs of the
      same crime type at two or more stations — the repeat pattern a district line hides.
    """
    if ix["series"] is not None:
        return ix["series"]
    with _build_lock:
        if ix["series"] is None:
            ix["series"] = _series_index(ix)
        return ix["series"]


def _series_index(ix):
    cm = ix["cm"].dropna(subset=["IncidentFromDate", "latitude", "longitude"])
    parent = {}

    def find(x):
        root = x
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(x, x) != root:
            parent[x], x = root, parent[x]
        return root

    for _ct, g in cm.groupby("crime_type", sort=False):
        g = g.sort_values("IncidentFromDate")
        ids = g["CaseMasterID"].to_numpy()
        t = g["IncidentFromDate"].to_numpy().astype("datetime64[s]").astype(np.int64) / 86400.0
        W = g[list(MO_FIELDS)].astype(str).to_numpy()
        la, lo = g["latitude"].to_numpy(float), g["longitude"].to_numpy(float)
        for i in range(len(g)):
            hi = int(np.searchsorted(t, t[i] + SERIES_WINDOW_DAYS, side="right"))
            if hi <= i + 1:
                continue
            match = (W[i + 1:hi] == W[i]).sum(axis=1) >= SERIES_MIN_MO_MATCH
            if not match.any():
                continue
            cand = np.arange(i + 1, hi)[match]
            near = cand[_km(la[i], lo[i], la[cand], lo[cand]) <= SERIES_RADIUS_KM]
            for j in near:
                ra, rb = find(ids[i]), find(ids[j])
                if ra != rb:
                    parent[ra] = rb
    groups = defaultdict(list)
    # Roots appear only as values until they are linked under another root, so the
    # node set is keys and values together — keys alone drop every component's root.
    for x in set(parent) | set(parent.values()):
        groups[find(x)].append(x)

    out, member_of = {}, defaultdict(list)

    def add(kind, members, extra):
        sub = ix["cm"].loc[members].sort_values("IncidentFromDate")
        if len(sub) < SERIES_MIN_CASES or sub["police_station"].nunique() < 2:
            return
        sid = _series_id(kind, sub["case_id"])
        out[sid] = {"id": sid, "kind": kind, "members": sub["CaseMasterID"].tolist(), "crime_type": sub["crime_type"].iloc[0],
                    "size": int(len(sub)), "stations": int(sub["police_station"].nunique()),
                    "districts": sorted(sub["district"].dropna().unique().tolist()),
                    "first": _d(sub["IncidentFromDate"].iloc[0]), "last": _d(sub["IncidentFromDate"].iloc[-1]),
                    "open": int((sub["CaseStatusID"] == 1).sum()), **extra}
        for m in out[sid]["members"]:
            member_of[m].append(sid)

    for members in groups.values():
        if len(members) >= SERIES_MIN_CASES:
            sub = ix["cm"].loc[members]
            sig = {f: Counter(sub[f].astype(str)).most_common(1)[0][0] for f in MO_FIELDS}
            add("mo", members, {"signature": sig, "person": None})
    A = ix["A"]
    for (pid, _ct), idx in A.groupby(["PersonID", "crime_type"], sort=False).indices.items():
        if len(idx) >= SERIES_MIN_CASES:
            members = list(dict.fromkeys(A["CaseMasterID"].iloc[idx]))
            add("offender", members, {"signature": None,
                                      "person": {"id": pid, "name": ix["people"].get(pid, {}).get("name")}})
    return {"series": out, "member_of": dict(member_of)}


def prewarm():
    """Build the index, today's flags and the series at start-up, off the request path:
    on a cold container the first /investigate view otherwise pays ~20 s for them and
    the gateway gives up at 30."""
    ix = index()
    if ix is None:
        return False
    integrity_flags(ix)
    series_index(ix)
    return True


def _series_card(ix, s):
    return {k: s[k] for k in ("id", "kind", "crime_type", "size", "stations", "districts", "first", "last", "open",
                              "signature", "person")}


def series_list(ix, kind=None, district=None, crime_type=None, limit=40, offset=0):
    si = series_index(ix)["series"]
    rows = [s for s in si.values()
            if (not kind or s["kind"] == kind) and (not crime_type or s["crime_type"] == crime_type)
            and (not district or district in s["districts"])]
    # Open series first, then the widest spread, then the largest.
    rows.sort(key=lambda s: (-s["open"], -len(s["districts"]), -s["size"], s["id"]))
    return {"total": len(rows), "by_kind": dict(Counter(s["kind"] for s in rows)),
            "criteria": {"window_days": SERIES_WINDOW_DAYS, "radius_km": SERIES_RADIUS_KM,
                         "mo_fields": list(MO_FIELDS), "min_mo_match": SERIES_MIN_MO_MATCH, "min_cases": SERIES_MIN_CASES},
            "series": [_series_card(ix, s) for s in rows[offset:offset + limit]]}


def series_detail(ix, sid):
    s = series_index(ix)["series"].get(sid)
    if s is None:
        return None
    cm = ix["cm"]
    A = ix["A"]
    people = Counter()
    for m in s["members"]:
        for pid in {A["PersonID"].iloc[i] for i in ix["accused_by_case"].get(m, ())}:
            people[pid] += 1
    members = []
    for m in s["members"]:
        r = cm.loc[m]
        members.append({"case_id": r["case_id"], "date": _d(r["IncidentFromDate"]), "station": r["police_station"],
                        "district": r["district"], "status": r["status"], "lat": r["latitude"], "lng": r["longitude"],
                        **{f: r[f] for f in MO_FIELDS}})
    pts = [{"lat": x["lat"], "lng": x["lng"], "case_id": x["case_id"]} for x in members]
    return {**_series_card(ix, s), "members": members,
            "shared_accused": [{"id": p, "name": ix["people"].get(p, {}).get("name"), "firs": n}
                               for p, n in people.most_common() if n >= 2][:12],
            "geoprofile": geoprofile(pts),
            "note": ("A series is a lead: these FIRs share an offender or a way of offending. It does not say they "
                     "are one offender's work until the cases are compared.")}


# =============================================================================
# Rossmo geographic profile
# =============================================================================
def geoprofile(points):
    """Rossmo's criminal geographic targeting over crime sites.

    Score(cell) = sum_i [ phi / d^f + (1-phi) B^(g-f) / (2B - d)^g ], d the Manhattan
    distance from the cell to crime site i, phi = 1 outside the buffer B and 0 inside.
    Peaks mark where an offender who travels to offend is most likely to be based; the
    50% and 80% areas are how much ground the highest-scoring cells cover before they
    hold that share of the surface. The method assumes one hunting area, so the densest
    cluster of sites within ROSSMO_CLUSTER_KM is profiled and the rest are excluded.
    """
    pts = [p for p in points if p.get("lat") is not None and p.get("lng") is not None
           and np.isfinite(p["lat"]) and np.isfinite(p["lng"])]
    base = {"method": "Rossmo criminal geographic targeting", "f": ROSSMO_F, "g": ROSSMO_G}
    if len(pts) < 3:
        return {**base, "applicable": False, "reason": "A geographic profile needs at least three crime sites."}
    pts = pts[:300]
    lat = np.array([p["lat"] for p in pts])
    lng = np.array([p["lng"] for p in pts])
    D = _km(lat[:, None], lng[:, None], lat[None, :], lng[None, :])
    centre = int(np.argmax((D <= ROSSMO_CLUSTER_KM).sum(axis=1)))
    keep = D[centre] <= ROSSMO_CLUSTER_KM
    used = [p for p, k in zip(pts, keep) if k]
    excluded = [p.get("case_id") for p, k in zip(pts, keep) if not k]
    if len(used) < 3:
        return {**base, "applicable": False, "excluded": excluded,
                "reason": (f"No three of these {len(pts)} sites lie within {ROSSMO_CLUSTER_KM:.0f} km of each other — "
                           "they do not form one hunting area, which the method assumes.")}
    ulat, ulng = lat[keep], lng[keep]
    lat0, lng0 = float(ulat.mean()), float(ulng.mean())
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.57
    x, y = (ulng - lng0) * kx, (ulat - lat0) * ky
    M = np.abs(x[:, None] - x[None, :]) + np.abs(y[:, None] - y[None, :])
    np.fill_diagonal(M, np.inf)
    B = float(np.clip(0.5 * np.mean(M.min(axis=1)), 0.3, 8.0))
    span = max(x.max() - x.min(), y.max() - y.min())
    pad = max(2 * B, 0.25 * span, 2.0)
    xs = np.linspace(x.min() - pad, x.max() + pad, ROSSMO_GRID)
    ys = np.linspace(y.min() - pad, y.max() + pad, ROSSMO_GRID)
    X, Y = np.meshgrid(xs, ys)
    P = np.zeros_like(X)
    for xi, yi in zip(x, y):
        d = np.abs(X - xi) + np.abs(Y - yi)
        far = 1.0 / np.power(np.maximum(d, 1e-9), ROSSMO_F)
        near = (B ** (ROSSMO_G - ROSSMO_F)) / np.power(2 * B - np.minimum(d, B), ROSSMO_G)
        P += np.where(d > B, far, near)
    P /= P.sum()
    cell = float((xs[1] - xs[0]) * (ys[1] - ys[0]))
    order = np.argsort(P, axis=None)[::-1]
    cum = np.cumsum(P.flat[order])

    def to_ll(flat):
        r, cidx = np.unravel_index(flat, P.shape)
        return lat0 + ys[r] / ky, lng0 + xs[cidx] / kx

    anchors = []
    for flat in order[:400]:
        a_lat, a_lng = to_ll(flat)
        if all(float(_km(a_lat, a_lng, a["lat"], a["lng"])) > max(2 * B, 1.5) for a in anchors):
            anchors.append({"lat": round(float(a_lat), 5), "lng": round(float(a_lng), 5),
                            "share": round(float(P.flat[flat] / P.max()), 3)})
        if len(anchors) == 3:
            break
    peak = float(P.max())
    heat = []
    for flat in order[:900]:
        v = float(P.flat[flat])
        if v < 0.2 * peak:
            break
        h_lat, h_lng = to_ll(flat)
        heat.append([round(float(h_lat), 5), round(float(h_lng), 5), round(v / peak, 3)])
    return {**base, "applicable": True, "buffer_km": round(B, 2), "cluster_km": ROSSMO_CLUSTER_KM,
            "sites_used": [p.get("case_id") for p in used], "excluded": excluded,
            "sites": [{"lat": round(float(p["lat"]), 5), "lng": round(float(p["lng"]), 5), "case_id": p.get("case_id")}
                      for p in used],
            "peak": anchors[0] if anchors else None, "anchors": anchors,
            "search_area_km2": {"50": round(cell * (int(np.searchsorted(cum, 0.5)) + 1), 1),
                                "80": round(cell * (int(np.searchsorted(cum, 0.8)) + 1), 1),
                                "profiled": round(cell * P.size, 1)},
            "heat": heat,
            "note": ("A search priority, not a location: the highest cells are where to look first for a base the "
                     "offender travels from. It assumes the sites are one offender's and that they travel to offend.")}


# =============================================================================
# Competing hypotheses (Heuer) over who committed one FIR
# =============================================================================
def _past(ix, pid, cmid):
    A = ix["A"]
    rows = [i for i in ix["by_person"].get(pid, ()) if A["CaseMasterID"].iloc[i] != cmid]
    return A.iloc[rows] if rows else A.iloc[0:0]


def _v(verdict, note, cites=()):
    return {"verdict": verdict, "note": note, "cites": list(cites)}


def _has(v):
    return isinstance(v, str) and bool(v) and v not in ("None", "Unknown", "nan")


def hypotheses(ix, cmid, k=4):
    cm, A = ix["cm"], ix["A"]
    r = cm.loc[cmid]
    named = list(dict.fromkeys(A["PersonID"].iloc[i] for i in ix["accused_by_case"].get(cmid, ())))
    cands = list(named)
    if _suggest is not None:
        try:
            for s in _suggest(r["case_id"], k + len(named)) or []:
                if s.get("suspect_id") not in cands and len(cands) < len(named) + k:
                    cands.append(s["suspect_id"])
        except Exception:      # the suggester is a convenience; the matrix stands without it
            pass
    cands = cands[:8]
    when = r["IncidentFromDate"]

    rows = [("named", "Named as accused in this FIR"), ("crime_type", f"Prior FIRs for {r['crime_type']}")]
    labels = {"weapon_used": "Has used a {} before", "vehicle_used": "Has used a {} before",
              "target_type": "Has targeted {} before", "time_of_day": "Has offended at {}"}
    for f in MO_FIELDS:
        if _has(r[f]):
            rows.append((f, labels[f].format(r[f])))
    rows += [("proximity", "A prior offence within 10 km of this scene"),
             ("active", f"Offending within a year of {_d(when)}"),
             ("association", "Co-accused elsewhere with someone named here"),
             ("camera", "Camera sighting near this scene and time")]
    keys = {key for key, _ in rows}

    matrix = {}
    for pid in cands:
        p = _past(ix, pid, cmid)
        cell = {"named": _v("C", "Named on this FIR", [_fir(r["case_id"])]) if pid in named
                else _v("N", "Not named on this FIR")}
        same = p[p["crime_type"] == r["crime_type"]]
        if len(same):
            cell["crime_type"] = _v("C", f"{len(same)} prior {r['crime_type']} FIR(s)", [_fir(x) for x in same["case_id"].head(3)])
        elif len(p) >= 2:
            cell["crime_type"] = _v("I", f"{len(p)} prior FIRs, none for {r['crime_type']}", [_fir(x) for x in p["case_id"].head(2)])
        else:
            cell["crime_type"] = _v("N", "Too little history to say")
        for f in MO_FIELDS:
            if f not in keys:
                continue
            word = f.split("_")[0]
            used = p[p[f].astype(str) == str(r[f])]
            known = p[p[f].astype(str).map(_has)]
            if len(used):
                cell[f] = _v("C", f"Same {word} in {len(used)} prior FIR(s)", [_fir(x) for x in used["case_id"].head(3)])
            elif len(known) >= 3:
                cell[f] = _v("I", f"{len(known)} prior FIRs record a different {word}", [_fir(x) for x in known["case_id"].head(2)])
            else:
                cell[f] = _v("N", "Not enough recorded")
        if len(p) and pd.notna(r["latitude"]):
            d = _km(r["latitude"], r["longitude"], p["latitude"].to_numpy(float), p["longitude"].to_numpy(float))
            j = int(np.nanargmin(d))
            near = p.iloc[j]
            if d[j] <= 10:
                cell["proximity"] = _v("C", f"FIR {near['case_id']} was {d[j]:.1f} km away", [_fir(near["case_id"])])
            elif d[j] > 100 and len(p) >= 2:
                cell["proximity"] = _v("I", f"Nearest prior offence is {d[j]:.0f} km away", [_fir(near["case_id"])])
            else:
                cell["proximity"] = _v("N", f"Nearest prior offence {d[j]:.0f} km away", [_fir(near["case_id"])])
        else:
            cell["proximity"] = _v("N", "No located prior offences")
        if len(p) and pd.notna(when):
            gap = (p["IncidentFromDate"] - when).abs().dt.days
            j = int(gap.to_numpy().argmin())
            g = int(gap.iloc[j])
            cid = p["case_id"].iloc[j]
            if g <= 365:
                cell["active"] = _v("C", f"FIR {cid} is {g} days apart", [_fir(cid)])
            elif g > 3 * 365:
                cell["active"] = _v("I", f"Closest FIR in time is {g // 365} years apart", [_fir(cid)])
            else:
                cell["active"] = _v("N", f"Closest FIR {g} days apart", [_fir(cid)])
        else:
            cell["active"] = _v("N", "No dated prior FIRs")
        partners = set(named) - {pid}
        link = None
        if partners:
            mine = set(p["CaseMasterID"])
            for q in sorted(partners):
                theirs = {A["CaseMasterID"].iloc[i] for i in ix["by_person"].get(q, ())} - {cmid}
                both = mine & theirs
                if both:
                    link = (q, cm.loc[sorted(both)[0]]["case_id"])
                    break
        if link:
            cell["association"] = _v("C", f"Co-accused with {link[0]} in FIR {link[1]}", [_fir(link[1]), _person(link[0])])
        else:
            cell["association"] = _v("N", "No shared FIR with the others named" if partners else "No one else named")
        cam = None
        for s in ix["sightings"].get(pid, ()):
            if pd.isna(s["ts"]) or pd.isna(when) or pd.isna(r["latitude"]):
                continue
            hours = abs((s["ts"] - when).total_seconds()) / 3600
            km = float(_km(r["latitude"], r["longitude"], s["lat"], s["lng"]))
            cite = [{"kind": "camera", "id": s["camera"], "label": f"Camera {s['camera']}"}]
            if hours <= 48 and km <= 10:
                cam = _v("C", f"Seen at {s['camera']} {km:.1f} km away, {hours:.0f} h from the incident", cite)
                break
            if hours <= 3 and km >= 50:
                cam = _v("I", f"Seen at {s['camera']}, {km:.0f} km away, {hours:.1f} h from the incident", cite)
        cell["camera"] = cam or _v("N", "No sighting near the time")
        matrix[pid] = cell

    def tally(q, v):
        return sum(c["verdict"] == v for c in matrix[q].values())

    ranked = sorted(cands, key=lambda q: (tally(q, "I"), -tally(q, "C"), q))
    return {
        "method": ("Analysis of Competing Hypotheses (Heuer): each candidate is tested against every item of "
                   "evidence; candidates are ranked by fewest inconsistencies, then most consistencies."),
        "excluded_factors": EXCLUDED_FACTORS + ["registry status (rowdy-sheet, warrant, risk band)"],
        "evidence": [{"key": key, "label": label, "diagnostic": len({matrix[q][key]["verdict"] for q in cands}) > 1}
                     for key, label in rows],
        "hypotheses": [{"id": q, "name": ix["people"].get(q, {}).get("name"), "named": q in named,
                        "inconsistent": tally(q, "I"), "consistent": tally(q, "C"), "cells": matrix[q]} for q in ranked],
        "note": ("A ranking of where to look, not an identification. It never uses who a person is — only what "
                 "the records say they did."),
    }


# =============================================================================
# One FIR, assembled
# =============================================================================
def case_view(ix, case_ref):
    cmid = _resolve(ix, case_ref)
    if cmid is None:
        return None
    cm = ix["cm"]
    r = cm.loc[cmid]
    flags = integrity_flags(ix).get(cmid, [])
    hyp = hypotheses(ix, cmid)
    secs = ix["sections"].get(cmid, [])
    si = series_index(ix)
    series = [_series_card(ix, si["series"][s]) for s in si["member_of"].get(cmid, ())]

    questions, seen = [], set()
    for fl in flags:
        if fl["question"] not in seen:
            seen.add(fl["question"])
            questions.append({"question": fl["question"], "why": fl["title"], "cites": fl["cites"], "source": "integrity"})
    leads = 0
    for h in hyp["hypotheses"]:
        bad = [c for c in h["cells"].values() if c["verdict"] == "I"]
        good = [c for c in h["cells"].values() if c["verdict"] == "C"]
        if h["named"] and bad:
            questions.append({"question": f"Resolve the evidence against naming {h['id']}: "
                                          + "; ".join(c["note"] for c in bad[:2]) + ".",
                              "why": "Named accused with inconsistent evidence",
                              "cites": [x for c in bad[:2] for x in c["cites"]], "source": "hypotheses"})
        elif not h["named"] and len(good) >= 3 and not bad and leads < 2:
            leads += 1
            questions.append({"question": f"Establish where {h['id']} was on {_d(r['IncidentFromDate'])} "
                                          f"({r['time_of_day']}): " + "; ".join(c["note"] for c in good[:2]) + ".",
                              "why": "Unnamed candidate consistent with the evidence",
                              "cites": [x for c in good[:2] for x in c["cites"]], "source": "hypotheses"})
    for s in series:
        others = [m for m in si["series"][s["id"]]["members"] if m != cmid][:3]
        questions.append({"question": f"Compare this FIR with series {s['id']} — {s['size']} {s['crime_type']} FIRs "
                                      f"across {s['stations']} stations — and consult those IOs.",
                          "why": "Part of a cross-station series",
                          "cites": [_fir(cm.loc[m]["case_id"]) for m in others], "source": "series"})
    return {
        "case_id": r["case_id"], "district": r["district"], "station": r["police_station"], "crime_type": r["crime_type"],
        "status": r["status"], "incident": _d(r["IncidentFromDate"]),
        "integrity": [f.as_dict() for f in flags], "questions": questions[:12], "hypotheses": hyp, "series": series,
        "statute": {"code_in_force": statutes.code_for(r["IncidentFromDate"]),
                    "commencement": statutes.BNS_COMMENCEMENT.isoformat(),
                    "sections": [{"act": a, "section": s, "crosswalk": statutes.lookup(a, s)} for a, s in secs],
                    "findings": statutes.check(secs, r["IncidentFromDate"]), "source": statutes.SOURCE},
    }


# =============================================================================
# Under-reporting
# =============================================================================
SOCIO_FEATURES = ["literacy", "unemployment", "per_capita_income_k", "urbanization", "police_per_100k", "youth_pct", "slum_pct"]
COGNIZABLE_CALLS = {"Assault", "Theft", "Cyber Fraud"}
DISTRICT_FIX = {"Mangaluru": "Dakshina Kannada", "Hubballi-Dharwad": "Dharwad"}


def _wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def underreporting(c, ix):
    cm = ix["cm"].dropna(subset=["IncidentFromDate"])
    socio = _t(c, "socio")
    years = max((cm["IncidentFromDate"].max() - cm["IncidentFromDate"].min()).days / 365.25, 1.0)
    firs = cm.groupby("district").size()
    model = None
    rate, expected, zs, pop = {}, {}, {}, {}
    if len(socio) and set(SOCIO_FEATURES) <= set(socio.columns):
        s = socio.drop_duplicates("district").set_index("district")
        s = s[s.index.isin(firs.index)]
        y = (firs.reindex(s.index) / s["population_lakh"] / years).to_numpy(float)
        X = s[SOCIO_FEATURES].to_numpy(float)
        pred = np.zeros_like(y)
        for i in range(len(y)):          # leave-one-out ridge: a district is never its own benchmark
            m = np.ones(len(y), bool)
            m[i] = False
            mu, sd = X[m].mean(0), X[m].std(0) + 1e-9
            Z = (X[m] - mu) / sd
            w = np.linalg.solve(Z.T @ Z + 3.0 * np.eye(Z.shape[1]), Z.T @ (y[m] - y[m].mean()))
            pred[i] = y[m].mean() + ((X[i] - mu) / sd) @ w
        resid = y - pred
        z = resid / (resid.std(ddof=1) or 1.0)
        model = {"features": SOCIO_FEATURES, "years": round(years, 1),
                 "method": "Leave-one-out ridge regression of FIRs per lakh per year on socio-economic indicators"}
        rate, expected, zs = dict(zip(s.index, y)), dict(zip(s.index, pred)), dict(zip(s.index, z))
        pop = s["population_lakh"].to_dict()
    calls = _t(c, "complaints").copy()
    conv, state = {}, None
    if len(calls):
        calls["district"] = calls["district"].replace(DISTRICT_FIX)
        calls["fir"] = calls["linked_case_id"].astype(str).str.len() > 5
        cog = calls[calls["emergency_type"].isin(COGNIZABLE_CALLS)]
        state = float(cog["fir"].mean()) if len(cog) else None
        for d, g in cog.groupby("district"):
            lo, hi = _wilson(int(g["fir"].sum()), len(g))
            conv[d] = {"calls": int(len(g)), "with_fir": int(g["fir"].sum()), "rate": float(g["fir"].mean()),
                       "ci": [round(lo, 3), round(hi, 3)]}
    out = []
    for d in sorted(firs.index):
        flags = []
        if d in zs and zs[d] <= -1.5:
            flags.append(f"Recorded crime is {abs(zs[d]):.1f} SD below what comparable districts record.")
        cv = conv.get(d)
        if cv and state is not None and cv["ci"][1] < state:
            flags.append(f"Only {cv['with_fir']} of {cv['calls']} Dial-112 calls reporting assault, theft or cyber fraud "
                         f"became an FIR — below the state rate of {state:.0%} even at the top of its 95% interval.")
        out.append({"district": d, "firs": int(firs[d]), "population_lakh": pop.get(d),
                    "rate_per_lakh_year": round(float(rate[d]), 1) if d in rate else None,
                    "expected": round(float(expected[d]), 1) if d in expected else None,
                    "z": round(float(zs[d]), 2) if d in zs else None, "calls": cv, "flags": flags})
    out.sort(key=lambda x: (-len(x["flags"]), x["z"] if x["z"] is not None else 0))
    return {"model": model, "state_cognizable_fir_rate": state, "cognizable_call_types": sorted(COGNIZABLE_CALLS),
            "districts": out,
            "note": ("Signals for a supervisor to look into, not findings: a district can record less crime because it "
                     "has less, and a call can rightly end without an FIR.")}


# =============================================================================
# Who should investigate
# =============================================================================
SPECIALISATION = {"Murder": "Homicide", "Homicide": "Homicide", "Assault": "Homicide",
                  "Chain Snatching": "Property Crime", "House Break-in (Night)": "Property Crime",
                  "Vehicle Theft": "Property Crime", "Extortion": "Organized Crime",
                  "Narcotics Distribution": "Narcotics", "Sandalwood Smuggling": "Forest Offences",
                  "Cyber Fraud (Jamtara style)": "Cybercrime"}


def recommend_io(c, ix, station_id=None, crime_type=None, k=5):
    emp = _t(c, "Employee")
    if not len(emp):
        return {"officers": []}
    reg = _t(c, "OfficersRegistry")
    if not len(reg):        # main.py keeps the registry merged into its `officers` frame
        off = _t(c, "officers")
        if len(off) and "employee_id" in off.columns:
            reg = off.rename(columns={"employee_id": "EmployeeID"})
    units = _t(c, "Unit")
    rk = _t(c, "Rank")
    ranks = dict(zip(rk["RankID"], rk["RankName"])) if len(rk) else {}
    try:
        sid = int(station_id) if station_id not in (None, "") else None
    except (TypeError, ValueError):
        sid = None
    st = units[units["UnitID"] == sid] if sid is not None and len(units) else units.iloc[0:0]
    st_district = int(st["DistrictID"].iloc[0]) if len(st) else None
    want = SPECIALISATION.get(crime_type)
    cm = ix["cm"]
    open_load = cm[cm["CaseStatusID"] == 1].groupby("PolicePersonID").size()
    if len(reg) and "specialization" in reg.columns:
        df = emp.merge(reg[["EmployeeID", "specialization", "closure_rate"]], on="EmployeeID", how="left")
    else:
        df = emp.assign(specialization=None, closure_rate=None)
    loads = open_load.reindex(df["EmployeeID"]).fillna(0).to_numpy()
    lo, hi = float(loads.min()), float(loads.max())
    out = []
    for r, load in zip(df.to_dict("records"), loads):
        parts, reasons = {}, []
        if want and r.get("specialization") == want:
            parts["specialisation"] = 40.0
            reasons.append(f"Specialises in {want}")
        if sid is not None and int(r["UnitID"]) == sid:
            parts["station"] = 25.0
            reasons.append("Posted at this station")
        elif st_district is not None and int(r["DistrictID"]) == st_district:
            parts["district"] = 12.0
            reasons.append("Posted in this district")
        cr = r.get("closure_rate")
        if cr is not None and not pd.isna(cr):
            parts["closure"] = round(20.0 * float(cr), 1)
            reasons.append(f"{float(cr):.0%} closure rate")
        parts["capacity"] = round(15.0 * (1 - (load - lo) / ((hi - lo) or 1)), 1)
        reasons.append(f"{int(load)} open cases")
        out.append({"officer_id": int(r["EmployeeID"]), "kgid": r.get("KGID"), "name": r.get("FirstName"),
                    "rank": ranks.get(r.get("RankID"), ""), "unit_id": int(r["UnitID"]),
                    "specialization": r.get("specialization"), "open_cases": int(load),
                    "score": round(sum(parts.values()), 1), "components": parts, "reasons": reasons})
    out.sort(key=lambda x: (-x["score"], x["open_cases"], x["officer_id"]))
    return {"crime_type": crime_type, "wanted_specialisation": want, "officers": out[:k],
            "excluded_factors": ["gender", "date of birth", "blood group", "disability"],
            "note": "A shortlist with its reasons; the supervising officer assigns."}


# =============================================================================
# One person across every source
# =============================================================================
def person_timeline(ix, pid):
    pid = str(pid)
    rows = ix["by_person"].get(pid)
    person = ix["people"].get(pid)
    if rows is None and person is None:
        return None
    A = ix["A"]
    events, births = [], []
    for i in rows if rows is not None else ():
        a = A.iloc[i]
        cmid = a["CaseMasterID"]
        events.append({"ts": a["IncidentFromDate"], "source": "FIR", "kind": "named",
                       "title": f"Named accused — {a['crime_type']}",
                       "detail": f"{a['police_station']}, {a['district']} · {a['status']}", "cites": [_fir(a["case_id"])]})
        if pd.notna(a["AgeYear"]) and pd.notna(a["IncidentFromDate"]):
            births.append((int(a["IncidentFromDate"].year - a["AgeYear"]), a["case_id"], int(a["AgeYear"])))
        for ar in ix["arrests_by_accused"].get(a["AccusedMasterID"], ()):
            events.append({"ts": ar["date"], "source": "Arrest register", "kind": "arrest", "title": "Arrested",
                           "detail": f"In FIR {a['case_id']}",
                           "cites": [{"kind": "arrest", "id": ar["id"], "label": f"Arrest {ar['id']}"}, _fir(a["case_id"])]})
        csr = ix["chargesheet"].get(cmid)
        if csr is not None:
            events.append({"ts": csr["date"], "source": "Charge-sheet register", "kind": "chargesheet",
                           "title": "Charge-sheeted", "detail": f"In FIR {a['case_id']}",
                           "cites": [{"kind": "chargesheet", "id": csr["id"], "label": f"Charge-sheet {csr['id']}"},
                                     _fir(a["case_id"])]})
        crt = ix["court"].get(cmid)
        if crt is not None:
            events.append({"ts": crt["next_hearing"], "source": "Court register", "kind": "court",
                           "title": f"Court: {crt['verdict']}", "detail": f"{crt['court']} · FIR {a['case_id']}",
                           "cites": [{"kind": "court", "id": crt["id"], "label": f"Court case {crt['id']}"}]})
        for v in ix["vehicles"].get(a["case_id"], ()):
            events.append({"ts": v["theft"], "source": "Stolen-vehicle register", "kind": "vehicle",
                           "title": f"Vehicle {v['id']} reported stolen",
                           "detail": f"{v['make_model']} · linked to FIR {a['case_id']}",
                           "cites": [{"kind": "vehicle", "id": v["id"], "label": f"Vehicle {v['id']}"}]})
    for s in ix["sightings"].get(pid, ()):
        events.append({"ts": s["ts"], "source": "CCTV", "kind": "camera", "title": f"Face detected at {s['camera']}",
                       "detail": s.get("district") or "",
                       "cites": [{"kind": "camera", "id": s["camera"], "label": f"Camera {s['camera']}"}]})
    events.sort(key=lambda e: (pd.Timestamp.max if pd.isna(e["ts"]) else e["ts"]))
    conflicts = []
    if births:
        lo, hi = min(births), max(births)
        if hi[0] - lo[0] > AGE_SPREAD_YEARS:
            conflicts.append({"code": "identity_age_conflict",
                              "detail": f"Recorded ages imply birth years from {lo[0]} (FIR {lo[1]}, aged {lo[2]}) to "
                                        f"{hi[0]} (FIR {hi[1]}, aged {hi[2]}) — more than one person, or wrong ages.",
                              "cites": [_fir(lo[1]), _fir(hi[1])]})
    fp = (person or {}).get("fingerprint")
    if fp in ix["shared_fp"]:
        others = [o for o in ix["shared_fp"][fp] if o != pid]
        conflicts.append({"code": "shared_fingerprint",
                          "detail": f"Fingerprint {fp} is also recorded for "
                                    + ", ".join(f"{o} ({ix['people'].get(o, {}).get('name')})" for o in others) + ".",
                          "cites": [_person(o, ix["people"].get(o, {}).get("name")) for o in others]})
    sub = A.iloc[rows] if rows is not None else A.iloc[0:0]
    return {"person": {"id": pid, **(person or {})},
            "summary": {"firs": int(sub["case_id"].nunique()), "districts": sorted(sub["district"].dropna().unique().tolist()),
                        "stations": int(sub["police_station"].nunique()), "crime_types": dict(Counter(sub["crime_type"])),
                        "first": _d(sub["IncidentFromDate"].min()) if len(sub) else None,
                        "last": _d(sub["IncidentFromDate"].max()) if len(sub) else None},
            "sources": sorted({e["source"] for e in events}), "events": events, "identity_conflicts": conflicts,
            "geoprofile": geoprofile([{"lat": float(x), "lng": float(y), "case_id": cid}
                                      for x, y, cid in zip(sub["latitude"], sub["longitude"], sub["case_id"])])}


# =============================================================================
# Routes
# =============================================================================
def _need():
    ix = index()
    if ix is None:
        return None, (jsonify({"error": "corpus not loaded"}), 503)
    return ix, None


def _int_arg(name, default, lo, hi):
    try:
        return max(lo, min(int(request.args.get(name, default) or default), hi))
    except (TypeError, ValueError):
        return default


@bp.route(f"{API}/investigate/case/<case_id>", methods=["GET"])
@audit.audited("read_case_investigation", subject_type="case", subject_arg="case_id")
@auth.require_permission("view_cases")
def route_case(case_id):
    ix, err = _need()
    if err:
        return err
    out = case_view(ix, case_id)
    return (jsonify(_clean(out)), 200) if out else (jsonify({"error": "not_found"}), 404)


@bp.route(f"{API}/investigate/integrity", methods=["GET"])
@audit.audited("read_integrity_audit")
@auth.require_permission("view_cases")
def route_integrity():
    ix, err = _need()
    if err:
        return err
    a = request.args
    return jsonify(_clean(integrity_summary(ix, district=a.get("district") or None, code=a.get("code") or None,
                                            limit=_int_arg("limit", 50, 1, 200))))


@bp.route(f"{API}/investigate/series", methods=["GET"])
@audit.audited("read_crime_series")
@auth.require_permission("view_cases")
def route_series():
    ix, err = _need()
    if err:
        return err
    a = request.args
    return jsonify(_clean(series_list(ix, kind=a.get("kind") or None, district=a.get("district") or None,
                                      crime_type=a.get("crime_type") or None, limit=_int_arg("limit", 40, 1, 200),
                                      offset=_int_arg("offset", 0, 0, 100000))))


@bp.route(f"{API}/investigate/series/<series_id>", methods=["GET"])
@audit.audited("read_crime_series", subject_type="series", subject_arg="series_id")
@auth.require_permission("view_cases")
def route_series_detail(series_id):
    ix, err = _need()
    if err:
        return err
    out = series_detail(ix, series_id)
    return (jsonify(_clean(out)), 200) if out else (jsonify({"error": "not_found"}), 404)


@bp.route(f"{API}/investigate/person/<person_id>/timeline", methods=["GET"])
@audit.audited("read_person_timeline", subject_type="person", subject_arg="person_id")
@auth.require_permission("view_dossiers")
def route_timeline(person_id):
    ix, err = _need()
    if err:
        return err
    out = person_timeline(ix, person_id)
    return (jsonify(_clean(out)), 200) if out else (jsonify({"error": "not_found"}), 404)


@bp.route(f"{API}/investigate/underreporting", methods=["GET"])
def route_underreporting():
    ix, err = _need()
    if err:
        return err
    return jsonify(_clean(underreporting(_cache_getter() or {}, ix)))


@bp.route(f"{API}/investigate/recommend-io", methods=["GET"])
@auth.require_permission("view_cases")
def route_recommend_io():
    ix, err = _need()
    if err:
        return err
    a = request.args
    return jsonify(_clean(recommend_io(_cache_getter() or {}, ix, a.get("station_id"), a.get("crime_type") or None)))


@bp.route(f"{API}/statutes/crosswalk", methods=["GET"])
def route_crosswalk():
    a = request.args
    if a.get("section"):
        act = a.get("act", "IPC")
        return jsonify({"row": statutes.lookup(act, a.get("section")),
                        "code_in_force": statutes.code_for(a.get("date")) if a.get("date") else None,
                        "findings": statutes.check([(act, a.get("section"))], a.get("date")) if a.get("date") else []})
    return jsonify(statutes.table())
