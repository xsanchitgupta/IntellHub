"""Registering a new FIR into the same CCTNS-shaped schema the corpus uses.

* ``GET  /serverless/api/fir/masters`` — every lookup the form needs, read from the
  master tables (districts, stations, case categories, gravity, crime heads and
  sub-heads, acts and sections, courts, officers, occupation/religion/caste) plus
  the section suggested for each crime type.
* ``POST /serverless/api/fir/register`` — validate, number, persist and publish.

What a registration writes
--------------------------
One ``CaseMaster`` row, one ``ComplainantDetails`` row, one ``Victim`` row per victim,
one ``Accused`` row per accused, one ``ActSectionAssociation`` row per act/section —
the same columns, ID conventions and code formats as ``convert_data.py``:

* ``CrimeNo = f"{category}{district:04d}{station:04d}{year}{serial:05d}"`` and
  ``CaseNo = f"{year}{serial:05d}"``, the serial running per (category, district,
  station, year) and continuing from the highest already issued.
* ``BriefFacts`` ends with the ``[METADATA: SEV=..;WPN=..;VEH=..;TGT=..;FAT=..;INJ=..;WIT=..]``
  block main.py parses into the FIR frame.
* A new FIR is "Under Investigation"; arrests, charge-sheets and court records come
  later in the case's life and are not invented at registration.

Every coded value is checked against its master table, so a registration can never
carry a station, section or occupation the rest of the platform cannot resolve. The penal
code is checked against the date of the offence too: the BNS replaced the IPC on
1 July 2024 (``statutes.py``), so an IPC section for a later offence — or a BNS
section for an earlier one — is refused with the corresponding section named. An
accused is linked to the offender registry only when the officer picks a registry
person; a free-text accused is recorded on the FIR without a guessed identity.

Who may register
----------------
``register_fir``, within jurisdiction: a station role at its own station, a district
role at any station in its district, a statewide role anywhere.

Persistence
-----------
Registrations are appended to ``FIR_REGISTRY_PATH`` (default
``appsail/registered_firs.jsonl``) — outside ``data/``, because the ML registry
fingerprints that directory and a registration must not invalidate trained models.
main.py replays the file onto the tables at every load.

Photographs
-----------
* ``POST /serverless/api/fir/photo`` — one JPEG of the complainant, a victim or an
  accused, uploaded before the FIR is registered; returns a ``photo_id`` the person
  then carries in the registration.
* ``GET  /serverless/api/fir/photo/<photo_id>`` — the image, to a ``view_cases`` role,
  and only once a registered FIR refers to it.

The server keeps the image data and nothing else: every APPn segment (EXIF, which
carries the camera's GPS fix and serial number, XMP, ICC) and every comment is
stripped, and anything after the end-of-image marker is cut. Photos live in
``FIR_PHOTO_DIR`` (default ``appsail/fir_photos/``), outside the CCTNS tables — the
schema has no photo column and is not given one. An upload no FIR claims within a day
is deleted. Both routes are audited.
"""
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from flask import Blueprint, jsonify, request, send_file

import audit
import karnataka
import statutes

bp = Blueprint("fir_registry", __name__)
API = "/serverless/api"

STORE_PATH = os.environ.get(
    "FIR_REGISTRY_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "registered_firs.jsonl"))

_cache_getter = lambda: {}          # noqa: E731
_on_registered = lambda bundle: None  # noqa: E731
_identity = None
_lock = threading.Lock()

# The vocabularies the corpus's METADATA block uses (convert_data.py).
WEAPONS = ["None", "Knife", "Crowbar", "Pistol", "Stick", "Iron Rod", "Sickle"]
VEHICLES = ["None", "Bajaj Pulsar", "Honda Activa", "TVS Apache", "Maruti Omni", "Hyundai Creta", "Royal Enfield"]
TARGETS = ["Solitary Elderly", "Residential", "Commercial Establishment", "Transit Hub", "Public Area"]
GENDERS = {1: "Male", 2: "Female", 3: "Transgender"}
UNDER_INVESTIGATION = 1
RELATIONSHIP = "Charged"
# The section each crime type is booked under in the corpus — a suggestion only.
SUGGESTED_SECTIONS = {
    "Chain Snatching": ("IPC", "379"), "Vehicle Theft": ("IPC", "379"),
    "House Break-in (Night)": ("IPC", "457"), "Extortion": ("IPC", "384"),
    "Narcotics Distribution": ("NDPS", "20(b)"), "Sandalwood Smuggling": ("Karnataka Forest Act", "86"),
    "Cyber Fraud (Jamtara style)": ("IT Act", "66C"), "Murder": ("IPC", "302"),
    "Assault": ("IPC", "323"), "Homicide": ("IPC", "304"),
}
# The same suggestion for an offence on or after 1 July 2024, when the BNS applies.
# Snatching became an offence of its own (BNS 304), so chain snatching is not theft there.
SUGGESTED_SECTIONS_BNS = {
    ct: ("BNS", "304(2)") if ct == "Chain Snatching"
    else (("BNS", statutes.CROSSWALK[sec][0]) if act == "IPC" and sec in statutes.CROSSWALK else (act, sec))
    for ct, (act, sec) in SUGGESTED_SECTIONS.items()
}
# Base severity per crime type for the METADATA SEV score; the corpus books
# Heinous when SEV > 75.
BASE_SEVERITY = {
    "Murder": 88, "Homicide": 82, "Extortion": 66, "Narcotics Distribution": 62, "Assault": 52,
    "Sandalwood Smuggling": 58, "House Break-in (Night)": 48, "Cyber Fraud (Jamtara style)": 46,
    "Chain Snatching": 44, "Vehicle Theft": 38,
}
LAT, LON = (11.5, 18.5), (74.0, 78.6)       # Karnataka bounding box
MAX_PEOPLE = 20
_SAFE = re.compile(r"^[^\[\];]*$")          # nothing that could break the METADATA block

PHOTO_DIR = os.environ.get(
    "FIR_PHOTO_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fir_photos"))
MAX_PHOTO_BYTES = 2_000_000                 # the form sends ~100-300 KB; this is a ceiling, not a target
PHOTO_ORPHAN_S = 24 * 3600                  # an upload no FIR has claimed by then is deleted
_PHOTO_ID = re.compile(r"^[0-9a-f]{32}$")
_photos = None                              # crime_no -> [photo record], loaded lazily from the store


def configure(cache_getter, on_registered=None, identity=None):
    """cache_getter: the live data cache. on_registered(bundle): main.py rebuilds the
    derived frames (firs, links, reports, graph) and drops its memoised indexes.
    identity(): the caller's identity block (auth.current_identity)."""
    global _cache_getter, _on_registered, _identity
    _cache_getter = cache_getter
    if on_registered:
        _on_registered = on_registered
    _identity = identity


def ist_now():
    """Station-local time. Form times are IST wall-clock; the container runs on UTC."""
    return datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None, microsecond=0)


class FIRError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


# ------------------------------------------------------------------ masters
def _t(cache, name):
    df = cache.get(name)
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


def masters():
    c = _cache_getter() or {}
    dist = _t(c, "District")
    units = _t(c, "Unit")
    emp = _t(c, "Employee")
    rank = dict(zip(_t(c, "Rank")["RankID"], _t(c, "Rank")["RankName"])) if len(_t(c, "Rank")) else {}
    heads = _t(c, "CrimeHead")
    subs = _t(c, "CrimeSubHead")
    sections = _t(c, "Section")
    section_rows = [{"act": r.ActCode, "section": str(r.SectionCode), "description": r.SectionDescription}
                    for r in sections.itertuples() if int(getattr(r, "Active", 1)) == 1]
    have = {(x["act"], x["section"]) for x in section_rows}
    # The BNS successors of the IPC sections in the master, so an offence after 1 July
    # 2024 can be booked under the code in force (statutes.py cites the source table).
    for _ipc, (bns, subject, _change) in statutes.CROSSWALK.items():
        if ("BNS", bns) not in have:
            section_rows.append({"act": "BNS", "section": bns, "description": subject})
            have.add(("BNS", bns))
    return {
        "districts": [{"id": int(r.DistrictID), "name": r.DistrictName}
                      for r in dist.sort_values("DistrictName").itertuples()],
        "stations": [{"id": int(r.UnitID), "name": r.UnitName, "district_id": int(r.DistrictID)}
                     for r in units.sort_values("UnitName").itertuples() if int(getattr(r, "Active", 1)) == 1],
        "case_categories": [{"id": int(r.CaseCategoryID), "name": r.LookupValue}
                            for r in _t(c, "CaseCategory").itertuples()],
        "gravity": [{"id": int(r.GravityOffenceID), "name": r.LookupValue}
                    for r in _t(c, "GravityOffence").itertuples()],
        "crime_heads": [{"id": int(r.CrimeHeadID), "name": r.CrimeGroupName} for r in heads.itertuples()],
        "crime_types": [{"id": int(r.CrimeSubHeadID), "head_id": int(r.CrimeHeadID), "name": r.CrimeHeadName,
                         "suggested_section": dict(zip(("act", "section"), SUGGESTED_SECTIONS[r.CrimeHeadName]))
                         if r.CrimeHeadName in SUGGESTED_SECTIONS else None,
                         "suggested_section_bns": dict(zip(("act", "section"), SUGGESTED_SECTIONS_BNS[r.CrimeHeadName]))
                         if r.CrimeHeadName in SUGGESTED_SECTIONS_BNS else None,
                         "base_severity": BASE_SEVERITY.get(r.CrimeHeadName, 50)}
                        for r in subs.itertuples()],
        "acts": [{"code": r.ActCode, "name": r.ActDescription} for r in _t(c, "Act").itertuples()],
        "sections": section_rows,
        "bns_commencement": statutes.BNS_COMMENCEMENT.isoformat(),
        "courts": [{"id": int(r.CourtID), "name": r.CourtName} for r in _t(c, "Court").itertuples()],
        "officers": [{"id": int(r.EmployeeID), "name": r.FirstName, "kgid": r.KGID,
                      "rank": rank.get(r.RankID, ""), "unit_id": int(r.UnitID), "district_id": int(r.DistrictID)}
                     for r in emp.sort_values("FirstName").itertuples()],
        "occupations": [{"id": int(r.OccupationID), "name": r.OccupationName}
                        for r in _t(c, "OccupationMaster").itertuples()],
        "religions": [{"id": int(r.ReligionID), "name": r.ReligionName}
                      for r in _t(c, "ReligionMaster").itertuples()],
        "castes": [{"id": int(r.caste_master_id), "name": r.caste_master_name}
                   for r in _t(c, "CasteMaster").itertuples()],
        "genders": [{"id": k, "name": v} for k, v in GENDERS.items()],
        "weapons": WEAPONS, "vehicles": VEHICLES, "targets": TARGETS,
        "status_on_registration": "Under Investigation",
    }


# ------------------------------------------------------------------ photographs
class PhotoError(ValueError):
    pass


def clean_jpeg(data):
    """The image data of a baseline/progressive JPEG with every metadata segment removed.

    Walks the marker segments up to the start of scan, keeping only what decoding needs
    (APP0/JFIF, quantisation and Huffman tables, frame header, restart interval) and
    dropping APP1-APP15 and comments. The entropy-coded scan is copied through to the
    last end-of-image marker; trailing bytes are discarded, so a file cannot smuggle a
    second payload behind the picture.
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) < 64 or data[:2] != b"\xff\xd8":
        raise PhotoError("Send the photograph as a JPEG.")
    end = data.rfind(b"\xff\xd9")
    if end < 0:
        raise PhotoError("The JPEG is incomplete.")
    out = bytearray(b"\xff\xd8")
    i, n, size = 2, len(data), None
    while i < n:
        if data[i] != 0xFF:
            raise PhotoError("The JPEG is malformed.")
        while i < n and data[i] == 0xFF:          # fill bytes
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out += bytes((0xFF, marker))
            continue
        if marker == 0xD9:
            break
        if i + 2 > n:
            raise PhotoError("The JPEG is truncated.")
        length = int.from_bytes(data[i:i + 2], "big")
        if length < 2 or i + length > n:
            raise PhotoError("The JPEG is malformed.")
        segment = bytes(data[i:i + length])
        i += length
        if marker in (0xC0, 0xC1, 0xC2) and length >= 7:
            size = (int.from_bytes(segment[5:7], "big"), int.from_bytes(segment[3:5], "big"))
        if marker == 0xDA:                        # start of scan: image data runs to EOI
            if size is None:
                raise PhotoError("The JPEG has no frame header.")
            if i > end:
                raise PhotoError("The JPEG is truncated.")
            out += b"\xff\xda" + segment + data[i:end + 2]
            w, h = size
            if not (32 <= w <= 8000 and 32 <= h <= 8000):
                raise PhotoError("The photograph must be between 32 and 8000 pixels on each side.")
            return bytes(out)
        if 0xE1 <= marker <= 0xEF or marker == 0xFE:
            continue                              # EXIF / XMP / ICC / vendor data / comments
        out += bytes((0xFF, marker)) + segment
    raise PhotoError("The JPEG has no image data.")


def photo_path(photo_id):
    return os.path.join(PHOTO_DIR, f"{photo_id}.jpg")


def photo_exists(photo_id):
    return bool(photo_id) and bool(_PHOTO_ID.match(str(photo_id))) and os.path.isfile(photo_path(photo_id))


def _photo_index():
    global _photos
    if _photos is None:
        _photos = {str(b["crime_no"]): b["photos"] for b in stored_bundles() if b.get("photos")}
    return _photos


def reset_photo_index():
    """Forget the cached index (tests that swap the store underneath)."""
    global _photos
    _photos = None


def photos_for(crime_no):
    """Photographs on a registered FIR: [{role, name, record_id, person_id, photo_id}]."""
    return [dict(p) for p in _photo_index().get(str(crime_no), [])]


def _sweep_orphans(now=None):
    """Delete uploads that no registered FIR refers to and that are older than a day."""
    if not os.path.isdir(PHOTO_DIR):
        return 0
    now = now or time.time()
    claimed = {p["photo_id"] for ps in _photo_index().values() for p in ps}
    gone = 0
    for fn in os.listdir(PHOTO_DIR):
        pid, ext = os.path.splitext(fn)
        if ext != ".jpg" or pid in claimed:
            continue
        path = os.path.join(PHOTO_DIR, fn)
        try:
            if now - os.path.getmtime(path) > PHOTO_ORPHAN_S:
                os.remove(path)
                gone += 1
        except OSError:
            continue
    return gone


def save_photo(data):
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoError(f"The photograph is larger than {MAX_PHOTO_BYTES // 1_000_000} MB.")
    clean = clean_jpeg(data)
    os.makedirs(PHOTO_DIR, exist_ok=True)
    photo_id = secrets.token_hex(16)
    tmp = photo_path(photo_id) + ".part"
    with open(tmp, "wb") as fh:
        fh.write(clean)
    os.replace(tmp, photo_path(photo_id))
    _sweep_orphans()
    return photo_id, len(clean)


# ------------------------------------------------------------------ validation
def _int(v):
    try:
        if isinstance(v, bool):
            return None
        i = int(str(v).strip())
        return i
    except (TypeError, ValueError):
        return None


def _dt(v):
    if not v:
        return None
    try:
        return pd.Timestamp(str(v).replace("T", " ")).to_pydatetime().replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _text(v, limit):
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    return s[:limit]


def _person(p, errors, key, need_name=True, ages=(0, 110)):
    p = p if isinstance(p, dict) else {}
    name = _text(p.get("name"), 80)
    if need_name and len(name) < 2:
        errors[f"{key}.name"] = "Name is required."
    elif name and not _SAFE.match(name):
        errors[f"{key}.name"] = "Name may not contain [ ] or ;."
    age = _int(p.get("age"))
    if p.get("age") not in (None, "") and (age is None or not ages[0] <= age <= ages[1]):
        errors[f"{key}.age"] = f"Age must be {ages[0]}–{ages[1]}."
    gender = _int(p.get("gender_id"))
    if gender not in GENDERS:
        errors[f"{key}.gender_id"] = "Choose a gender."
    photo = str(p.get("photo_id") or "").strip() or None
    if photo and not photo_exists(photo):
        errors[f"{key}.photo_id"] = "That photograph is no longer on the server — add it again."
    return {**{k: v for k, v in p.items() if k not in ("name", "age", "gender_id", "photo_id")},
            "name": name, "age": age, "gender_id": gender, "photo_id": photo}


def validate(payload, now=None):
    """Returns a normalised registration or raises FIRError({field: message})."""
    c = _cache_getter() or {}
    now = now or ist_now()
    p = payload if isinstance(payload, dict) else {}
    e = {}

    units = _t(c, "Unit").set_index("UnitID")
    station_id = _int(p.get("station_id"))
    if station_id not in units.index:
        e["station_id"] = "Choose a police station."
        district_id = None
    else:
        district_id = int(units.loc[station_id, "DistrictID"])
    dist_names = dict(zip(_t(c, "District")["DistrictID"], _t(c, "District")["DistrictName"]))

    def _in(table, col, v, key, msg):
        if v is None or v not in set(_t(c, table)[col].astype(int)):
            e[key] = msg
    category_id = _int(p.get("case_category_id"))
    _in("CaseCategory", "CaseCategoryID", category_id, "case_category_id", "Choose a case category.")
    gravity_id = _int(p.get("gravity_id"))
    _in("GravityOffence", "GravityOffenceID", gravity_id, "gravity_id", "Choose the gravity of the offence.")

    subs = _t(c, "CrimeSubHead").set_index("CrimeSubHeadID")
    sub_id = _int(p.get("crime_type_id"))
    if sub_id not in subs.index:
        e["crime_type_id"] = "Choose a crime type."
        head_id, crime_type = None, None
    else:
        head_id, crime_type = int(subs.loc[sub_id, "CrimeHeadID"]), subs.loc[sub_id, "CrimeHeadName"]

    known_sections = {(r.ActCode, str(r.SectionCode)) for r in _t(c, "Section").itertuples()}
    known_sections |= {("BNS", bns) for bns, _s, _c in statutes.CROSSWALK.values()}
    sections = []
    for i, s in enumerate(p.get("sections") or []):
        pair = (str((s or {}).get("act", "")).strip(), str((s or {}).get("section", "")).strip())
        if pair not in known_sections:
            e[f"sections.{i}"] = f"{pair[0]} {pair[1]} is not in the Section master."
        elif pair not in sections:
            sections.append(pair)
    if not sections and "sections" not in str(e):
        e["sections"] = "Add at least one act and section."

    officer_id = _int(p.get("officer_id"))
    emp = _t(c, "Employee")
    if officer_id is None or officer_id not in set(emp["EmployeeID"].astype(int)):
        e["officer_id"] = "Choose the investigating officer."
    court_id = _int(p.get("court_id")) if p.get("court_id") not in (None, "") else None
    if court_id is not None:
        _in("Court", "CourtID", court_id, "court_id", "Unknown court.")

    t_from, t_to, t_info = _dt(p.get("incident_from")), _dt(p.get("incident_to") or p.get("incident_from")), _dt(p.get("info_received"))
    if not t_from:
        e["incident_from"] = "When did the incident start?"
    if not t_info:
        e["info_received"] = "When was the information received at the station?"
    if t_from and t_to and t_to < t_from:
        e["incident_to"] = "The incident cannot end before it starts."
    if t_from and t_info and t_info < t_from:
        e["info_received"] = "Information cannot reach the station before the incident started."
    for k, v in (("incident_from", t_from), ("incident_to", t_to), ("info_received", t_info)):
        if v and v > now:
            e[k] = "Cannot be in the future."
        if v and v.year < 1990:
            e[k] = "Date is implausibly old."
    # The date of the offence decides the code: an FIR booked under the wrong one is a
    # defect the charge-sheet inherits, so it is refused here with the right section named.
    if t_from and sections and "sections" not in e:
        wrong = statutes.check(sections, t_from)
        if wrong:
            e["sections"] = " ".join(
                f["message"] + (f" Book it as {f['suggestion']['act']} {f['suggestion']['section']}." if f.get("suggestion") else "")
                for f in wrong)

    lat, lon = p.get("latitude"), p.get("longitude")
    if lat in (None, "") or lon in (None, ""):
        if district_id is not None:
            lat, lon = karnataka.centre(dist_names.get(district_id, ""))
    try:
        lat, lon = round(float(lat), 5), round(float(lon), 5)
        if not (LAT[0] <= lat <= LAT[1] and LON[0] <= lon <= LON[1]):
            e["latitude"] = "The place of occurrence must be inside Karnataka."
    except (TypeError, ValueError):
        e["latitude"] = "Give the place of occurrence as latitude and longitude."
        lat = lon = None

    facts = _text(p.get("brief_facts"), 4000)
    if len(facts) < 30:
        e["brief_facts"] = "Write the brief facts of the case (at least 30 characters)."
    elif "[METADATA" in facts.upper():
        e["brief_facts"] = "Brief facts may not contain a METADATA block."
    place = _text(p.get("place_of_occurrence"), 200)

    weapon = p.get("weapon") or "None"
    vehicle = p.get("vehicle") or "None"
    target = p.get("target") or ""
    if weapon not in WEAPONS:
        e["weapon"] = "Unknown weapon."
    if vehicle not in VEHICLES:
        e["vehicle"] = "Unknown vehicle."
    if target not in TARGETS:
        e["target"] = "Choose the target type."
    counts = {}
    for k in ("fatalities", "injured", "witnesses"):
        v = _int(p.get(k) if p.get(k) not in (None, "") else 0)
        if v is None or not 0 <= v <= 500:
            e[k] = "Must be a whole number 0–500."
        counts[k] = v or 0

    comp = _person(p.get("complainant"), e, "complainant", ages=(5, 110))
    for k, table, col in (("occupation_id", "OccupationMaster", "OccupationID"),
                          ("religion_id", "ReligionMaster", "ReligionID"),
                          ("caste_id", "CasteMaster", "caste_master_id")):
        v = _int(comp.get(k))
        if v is None or v not in set(_t(c, table)[col].astype(int)):
            e[f"complainant.{k}"] = "Choose a value."
        comp[k] = v
    if comp.get("age") is None and "complainant.age" not in e:
        e["complainant.age"] = "Age is required."

    victims_in = p.get("victims") or []
    if not isinstance(victims_in, list) or not 1 <= len(victims_in) <= MAX_PEOPLE:
        e["victims"] = f"Add between 1 and {MAX_PEOPLE} victims."
        victims_in = []
    victims = []
    for i, v in enumerate(victims_in):
        pv = _person(v, e, f"victims.{i}")
        pv["is_police"] = bool((v or {}).get("is_police"))
        victims.append(pv)

    accused_in = p.get("accused") or []
    if not isinstance(accused_in, list) or len(accused_in) > MAX_PEOPLE:
        e["accused"] = f"At most {MAX_PEOPLE} accused."
        accused_in = []
    registry = _t(c, "criminals")
    reg_ids = set(registry["criminal_id"].astype(str)) if "criminal_id" in registry else set()
    reg_names = dict(zip(registry.get("criminal_id", pd.Series(dtype=str)).astype(str),
                         registry.get("name", pd.Series(dtype=str)))) if len(registry) else {}
    accused = []
    for i, a in enumerate(accused_in):
        a = a if isinstance(a, dict) else {}
        pid = str(a.get("person_id") or "").strip()
        if pid:
            if pid not in reg_ids:
                e[f"accused.{i}.person_id"] = f"{pid} is not in the offender registry."
            elif not _text(a.get("name"), 80):
                a = {**a, "name": reg_names.get(pid, "")}
        pa = _person(a, e, f"accused.{i}", ages=(7, 110))
        pa["person_id"] = pid or None
        accused.append(pa)

    if e:
        raise FIRError(e)
    sev = min(98, max(20, BASE_SEVERITY.get(crime_type, 50) + 6 * counts["fatalities"]
                      + 2 * min(counts["injured"], 5) + (6 if weapon == "Pistol" else 3 if weapon != "None" else 0)))
    return {
        "station_id": station_id, "district_id": district_id, "district": dist_names.get(district_id),
        "station": units.loc[station_id, "UnitName"], "case_category_id": category_id, "gravity_id": gravity_id,
        "crime_head_id": head_id, "crime_type_id": sub_id, "crime_type": crime_type, "sections": sections,
        "officer_id": officer_id, "court_id": court_id, "incident_from": t_from, "incident_to": t_to or t_from,
        "info_received": t_info, "latitude": lat, "longitude": lon, "brief_facts": facts, "place": place,
        "weapon": weapon, "vehicle": vehicle, "target": target, "severity": int(sev), **counts,
        "complainant": comp, "victims": victims, "accused": accused,
    }


def _check_jurisdiction(rec, ident):
    if ident is None:
        return
    if "register_fir" not in ident.get("permissions", ()):
        raise PermissionError("register_fir")
    scope = ident.get("scope") or {}
    level = scope.get("level")
    if level == "district" and scope.get("district") != rec["district"]:
        raise PermissionError(f"A district officer may register FIRs only in {scope.get('district')}.")
    if level == "station" and (scope.get("district") != rec["district"] or scope.get("station") != rec["station"]):
        raise PermissionError(f"A station officer may register FIRs only at {scope.get('station')}.")


# ------------------------------------------------------------------ rows
def _next_id(c, table, col, extra=0):
    df = _t(c, table)
    return int(pd.to_numeric(df[col], errors="coerce").max() or 0) + 1 + extra if len(df) else 1 + extra


def _serial(c, prefix):
    cm = _t(c, "CaseMaster")
    nos = cm["CrimeNo"].astype(str) if len(cm) else pd.Series(dtype=str)
    hit = nos[nos.str.startswith(prefix) & (nos.str.len() == len(prefix) + 5)]
    return (int(hit.str[-5:].astype(int).max()) + 1) if len(hit) else 1


def build_rows(rec, now=None):
    """The table rows for one registration, numbered against the live tables."""
    c = _cache_getter() or {}
    now = now or ist_now()
    year = now.year
    cm_id = _next_id(c, "CaseMaster", "CaseMasterID")
    prefix = f"{rec['case_category_id']}{rec['district_id']:04d}{rec['station_id']:04d}{year}"
    serial = _serial(c, prefix)
    if serial > 99999:
        raise FIRError({"station_id": "This station has exhausted its serial range for the year."})
    crime_no, case_no = f"{prefix}{serial:05d}", f"{year}{serial:05d}"
    fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S")   # noqa: E731
    facts = rec["brief_facts"]
    if rec["place"]:
        facts = f"Place of occurrence: {rec['place']}. {facts}"
    meta = (f"[METADATA: SEV={rec['severity']};WPN={rec['weapon']};VEH={rec['vehicle']};TGT={rec['target']};"
            f"FAT={rec['fatalities']};INJ={rec['injured']};WIT={rec['witnesses']}]")
    case = {
        "CaseMasterID": cm_id, "CrimeNo": crime_no, "CaseNo": case_no,
        "CrimeRegisteredDate": now.strftime("%Y-%m-%d"), "PolicePersonID": rec["officer_id"],
        "PoliceStationID": rec["station_id"], "CaseCategoryID": rec["case_category_id"],
        "GravityOffenceID": rec["gravity_id"], "CrimeMajorHeadID": rec["crime_head_id"],
        "CrimeMinorHeadID": rec["crime_type_id"], "CaseStatusID": UNDER_INVESTIGATION,
        "CourtID": rec["court_id"], "IncidentFromDate": fmt(rec["incident_from"]),
        "IncidentToDate": fmt(rec["incident_to"]), "InfoReceivedPSDate": fmt(rec["info_received"]),
        "latitude": rec["latitude"], "longitude": rec["longitude"], "BriefFacts": f"{facts} {meta}",
    }
    comp = rec["complainant"]
    complainant = {"ComplainantID": _next_id(c, "ComplainantDetails", "ComplainantID"), "CaseMasterID": cm_id,
                   "ComplainantName": comp["name"], "AgeYear": comp["age"], "OccupationID": comp["occupation_id"],
                   "ReligionID": comp["religion_id"], "CasteID": comp["caste_id"], "GenderID": comp["gender_id"]}
    v0 = _next_id(c, "Victim", "VictimMasterID")
    victims = [{"VictimMasterID": v0 + i, "CaseMasterID": cm_id, "VictimName": v["name"], "AgeYear": v["age"],
                "GenderID": v["gender_id"], "VictimPolice": int(v["is_police"])} for i, v in enumerate(rec["victims"])]
    a0 = _next_id(c, "Accused", "AccusedMasterID")
    accused = [{"AccusedMasterID": a0 + i, "CaseMasterID": cm_id, "AccusedName": a["name"], "AgeYear": a["age"],
                "GenderID": a["gender_id"], "PersonID": a["person_id"] or ""} for i, a in enumerate(rec["accused"])]
    asa = [{"CaseMasterID": cm_id, "ActID": act, "SectionID": sec,
            "ActOrderID": [x[0] for x in rec["sections"]].index(act) + 1, "SectionOrderID": i + 1}
           for i, (act, sec) in enumerate(rec["sections"])]
    kgid = _t(c, "Employee").set_index("EmployeeID")["KGID"].get(rec["officer_id"], f"OFF-{rec['officer_id']}")
    # Which row each photograph belongs to; the CCTNS rows themselves stay schema-true.
    photos = []
    if comp.get("photo_id"):
        photos.append({"role": "complainant", "name": comp["name"], "record_id": complainant["ComplainantID"],
                       "person_id": None, "photo_id": comp["photo_id"]})
    for row, v in zip(victims, rec["victims"]):
        if v.get("photo_id"):
            photos.append({"role": "victim", "name": v["name"], "record_id": row["VictimMasterID"],
                           "person_id": None, "photo_id": v["photo_id"]})
    for row, a in zip(accused, rec["accused"]):
        if a.get("photo_id"):
            photos.append({"role": "accused", "name": a["name"], "record_id": row["AccusedMasterID"],
                           "person_id": a["person_id"], "photo_id": a["photo_id"]})
    return {
        "photos": photos,
        "tables": {"CaseMaster": [case], "ComplainantDetails": [complainant], "Victim": victims,
                   "Accused": accused, "ActSectionAssociation": asa},
        "links": [{"case_id": crime_no, "suspect_id": a["PersonID"], "relationship": RELATIONSHIP}
                  for a in accused if a["PersonID"]],
        "reports": [{"case_id": crime_no, "report_text": facts, "officer_id": kgid}],
        "crime_no": crime_no, "case_no": case_no, "case_master_id": cm_id,
    }


def append_rows(target, bundle):
    """Append a bundle's rows onto `target` (a dict of DataFrames), rebinding each key."""
    for name, rows in bundle["tables"].items():
        if rows:
            base = target.get(name)
            add = pd.DataFrame(rows)
            target[name] = add if base is None or base.empty else pd.concat([base, add], ignore_index=True)


# ------------------------------------------------------------------ store
def stored_bundles(path=None):
    path = path or STORE_PATH
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue   # a torn last line from a crash loses that one registration only
    return out


def register(payload, ident=None, now=None):
    now = now or ist_now()
    rec = validate(payload, now)
    _check_jurisdiction(rec, ident)
    with _lock:
        index = _photo_index()          # loaded before the append, so the new FIR is added once
        bundle = build_rows(rec, now)
        bundle["registered_by"] = (ident or {}).get("user")
        bundle["registered_at"] = now.isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(STORE_PATH) or ".", exist_ok=True)
        with open(STORE_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(bundle, default=str) + "\n")
        if bundle["photos"]:
            index[str(bundle["crime_no"])] = bundle["photos"]
        cache = _cache_getter()
        append_rows(cache, bundle)
        _on_registered(bundle)
    return {"crime_no": bundle["crime_no"], "case_no": bundle["case_no"],
            "case_master_id": bundle["case_master_id"], "district": rec["district"], "station": rec["station"],
            "crime_type": rec["crime_type"], "status": "Under Investigation", "severity": rec["severity"],
            "linked_accused": len(bundle["links"]), "photos": len(bundle["photos"]),
            "registered_at": bundle["registered_at"]}


# ------------------------------------------------------------------ routes
@bp.route(f"{API}/fir/masters", methods=["GET", "OPTIONS"])
def route_masters():
    if request.method == "OPTIONS":
        return "", 204
    return jsonify(masters())


@bp.route(f"{API}/fir/person-search", methods=["GET", "OPTIONS"])
def route_person_search():
    """Offender-registry lookup for the accused step. Person-level, so gated."""
    if request.method == "OPTIONS":
        return "", 204
    ident = _identity() if _identity else None
    if ident is not None and "register_fir" not in ident.get("permissions", ()):
        return jsonify({"error": "insufficient_permission", "required": "register_fir"}), 403
    return jsonify({"results": person_search(request.args.get("q", ""))})


def person_search(q, limit=10):
    q = _text(q, 60).lower()
    reg = _t(_cache_getter() or {}, "criminals")
    if len(q) < 2 or not len(reg):
        return []
    ids = reg["criminal_id"].astype(str)
    names = reg["name"].astype(str)
    hit = reg[ids.str.lower().str.contains(re.escape(q)) | names.str.lower().str.contains(re.escape(q))]
    out = []
    for r in hit.head(limit).to_dict("records"):
        out.append({"person_id": str(r["criminal_id"]), "name": r.get("name"),
                    "alias": r.get("alias"), "risk_level": r.get("risk_level"),
                    "rowdy_sheeter": str(r.get("rowdy_sheeter")).lower() in ("1", "true", "yes"),
                    "active_warrant": str(r.get("active_warrant")).lower() in ("1", "true", "yes")})
    return out


@bp.route(f"{API}/fir/register", methods=["POST", "OPTIONS"])
def route_register():
    if request.method == "OPTIONS":
        return "", 204
    ident = _identity() if _identity else None
    try:
        return jsonify(register(request.get_json(silent=True) or {}, ident)), 201
    except FIRError as ex:
        return jsonify({"error": "validation_failed", "fields": ex.errors}), 400
    except PermissionError as ex:
        msg = str(ex)
        return jsonify({"error": "insufficient_permission" if msg == "register_fir" else "outside_jurisdiction",
                        "required": "register_fir", "message": msg}), 403


def _require(permission):
    """The caller's identity, or a 403 body if it lacks `permission` (None when unwired)."""
    ident = _identity() if _identity else None
    if ident is not None and permission not in ident.get("permissions", ()):
        return jsonify({"error": "insufficient_permission", "required": permission}), 403
    return None


@bp.route(f"{API}/fir/photo", methods=["POST", "OPTIONS"])
@audit.audited("upload_person_photo")
def route_photo_upload():
    if request.method == "OPTIONS":
        return "", 204
    refused = _require("register_fir")
    if refused:
        return refused
    # Refuse an oversized body before Flask spools it.
    if (request.content_length or 0) > MAX_PHOTO_BYTES + 64_000:
        return jsonify({"error": "too_large", "message": f"Photographs are limited to {MAX_PHOTO_BYTES // 1_000_000} MB."}), 413
    f = request.files.get("photo")
    data = f.read(MAX_PHOTO_BYTES + 1) if f else b""
    if not data:
        return jsonify({"error": "no_photo", "message": "Attach the photograph as the 'photo' field."}), 400
    try:
        photo_id, size = save_photo(data)
    except PhotoError as ex:
        status = 413 if len(data) > MAX_PHOTO_BYTES else 415
        return jsonify({"error": "bad_photo", "message": str(ex)}), status
    return jsonify({"photo_id": photo_id, "bytes": size}), 201


@bp.route(f"{API}/fir/photo/<photo_id>", methods=["GET"])
@audit.audited("read_person_photo", subject_type="photo", subject_arg="photo_id")
def route_photo(photo_id):
    refused = _require("view_cases")
    if refused:
        return refused
    claimed = {p["photo_id"] for ps in _photo_index().values() for p in ps}
    # Only photographs on a registered FIR are served: an upload nobody registered is
    # not a record, and its id is not a way to read it.
    if not _PHOTO_ID.match(photo_id or "") or photo_id not in claimed or not os.path.isfile(photo_path(photo_id)):
        return jsonify({"error": "not_found"}), 404
    resp = send_file(photo_path(photo_id), mimetype="image/jpeg", max_age=0)
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
