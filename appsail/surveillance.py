"""CCTV & ANPR surveillance: a camera network, 1:N face matching against the offender
registry, OCR-aware plate matching against the stolen-vehicle registry, and an alert
queue that a person works.

What is real here and what is simulated
---------------------------------------
* **Camera positions** are placed at named public landmarks across Karnataka —
  transit hubs, junctions, toll plazas, markets — with coordinates accurate to a few
  hundred metres, plus the 500 cameras already in `cctv.csv`. They are a simulated
  network, not the KSP Safe City inventory, and every response says so.
* **Faces.** There is no video and no face model. Each offender gets a fixed 128-d
  unit "embedding"; a sighting is that embedding plus noise scaled by camera quality
  and light. Strangers are drawn too, some of them deliberately close to a registered
  face (look-alikes), so the matcher produces false positives the way a real one
  does. The *matching* is genuine: cosine similarity of every probe against the full
  9,000-person gallery, top-k, a calibrated threshold and a margin test. Its error
  rates are measured, not asserted (`evaluate()`).
* **Plates.** Reads are drawn from the stolen-vehicle registry or generated, then
  corrupted the way OCR corrupts them (0/O, 1/I, 8/B, 5/S …). The matcher is a real
  confusion-weighted edit distance.

The rules it holds to
---------------------
* **A match is a lead, never an identification.** Every alert waits for a person to
  verify or reject it; nothing here triggers an action (GOVERNANCE.md, constraint 3).
* **Non-matches are not retained.** A face that does not match the registry is
  reported as a count and discarded — the feed never carries a stranger's identity.
* **Every decision is logged** with who made it and why, append-only.

The stream is deterministic by time block, so every worker process and every viewer
sees the same events at the same moment without shared state.
"""
import hashlib
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

LOG = logging.getLogger("ksp.surveillance")

EMBED_DIM = 128
BLOCK_S = 6 * 3600            # events are generated in deterministic 6-hour blocks
HISTORY_S = 24 * 3600         # how far back the feed and alert queue reach
MEAN_GAP_S = 22               # one detection every ~22 s network-wide
CONFIRMED_SIM = 0.80          # chosen for <0.5% false positives on look-alikes (evaluate())
POSSIBLE_SIM = 0.68
MIN_MARGIN = 0.04
SEED = 20260418

DECISION_LOG_PATH = os.environ.get(
    "SURVEILLANCE_LOG_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "surveillance_decisions.jsonl"))

# --------------------------------------------------------------------------- sites
# (name, area, district, lat, lng, type). Types: FACE (face analytics), ANPR (plate
# reader), PTZ (pan-tilt-zoom with face analytics), CCTV (fixed, mixed analytics).
SITES = [
    # Bengaluru — transit hubs
    ("KSR Bengaluru City Railway Station", "Majestic", "Bengaluru Urban", 12.9781, 77.5697, "FACE"),
    ("Kempegowda Bus Station Platform 3", "Majestic", "Bengaluru Urban", 12.9767, 77.5713, "FACE"),
    ("MG Road Metro Concourse", "MG Road", "Bengaluru Urban", 12.9755, 77.6066, "FACE"),
    ("Shivajinagar Bus Station", "Shivajinagar", "Bengaluru Urban", 12.9857, 77.6045, "FACE"),
    ("Yeshwanthpur Junction Entry", "Yeshwanthpur", "Bengaluru Urban", 13.0233, 77.5498, "FACE"),
    ("Kempegowda International Airport Arrivals", "Devanahalli", "Bengaluru Rural", 13.1989, 77.7068, "FACE"),
    ("Satellite Bus Stand, Mysore Road", "Mysore Road", "Bengaluru Urban", 12.9534, 77.5440, "FACE"),
    ("Banashankari TTMC", "Banashankari", "Bengaluru Urban", 12.9180, 77.5737, "FACE"),
    ("Baiyappanahalli Metro", "Baiyappanahalli", "Bengaluru Urban", 12.9907, 77.6525, "FACE"),
    # Bengaluru — markets and public spaces
    ("Commercial Street Pedestrian Arcade", "Shivajinagar", "Bengaluru Urban", 12.9822, 77.6083, "PTZ"),
    ("Brigade Road – Church Street", "Central Business District", "Bengaluru Urban", 12.9719, 77.6070, "PTZ"),
    ("KR Market Flyover Underpass", "City Market", "Bengaluru Urban", 12.9647, 77.5776, "PTZ"),
    ("Chickpet Main Road Checkpoint", "Chickpet", "Bengaluru Urban", 12.9700, 77.5780, "PTZ"),
    ("Town Hall Circle", "SJP Road", "Bengaluru Urban", 12.9634, 77.5855, "CCTV"),
    ("Vidhana Soudha East Gate", "Ambedkar Veedhi", "Bengaluru Urban", 12.9794, 77.5907, "CCTV"),
    ("Cubbon Park Bandstand", "Cubbon Park", "Bengaluru Urban", 12.9763, 77.5929, "CCTV"),
    ("Lalbagh West Gate", "Basavanagudi", "Bengaluru Urban", 12.9507, 77.5848, "CCTV"),
    ("Bull Temple Road", "Basavanagudi", "Bengaluru Urban", 12.9426, 77.5683, "CCTV"),
    ("Malleshwaram 8th Cross Market", "Malleshwaram", "Bengaluru Urban", 13.0030, 77.5705, "PTZ"),
    ("Jayanagar 4th Block Complex", "Jayanagar", "Bengaluru Urban", 12.9250, 77.5838, "PTZ"),
    ("Koramangala Forum Junction", "Koramangala", "Bengaluru Urban", 12.9346, 77.6115, "PTZ"),
    ("Indiranagar 100 Ft Road – 12th Main", "Indiranagar", "Bengaluru Urban", 12.9719, 77.6412, "PTZ"),
    ("Ulsoor Lake Promenade", "Halasuru", "Bengaluru Urban", 12.9830, 77.6198, "CCTV"),
    ("Frazer Town Mosque Road", "Frazer Town", "Bengaluru Urban", 12.9984, 77.6161, "CCTV"),
    ("Koramangala 5th Block Executive Portico", "Koramangala", "Bengaluru Urban", 12.9352, 77.6195, "FACE"),
    ("Whitefield ITPL Main Gate", "Whitefield", "Bengaluru Urban", 12.9857, 77.7365, "FACE"),
    ("Manyata Tech Park Gate 1", "Nagavara", "Bengaluru Urban", 13.0475, 77.6206, "FACE"),
    ("Electronic City Phase 1 Infosys Gate", "Electronic City", "Bengaluru Urban", 12.8456, 77.6603, "FACE"),
    ("Bellandur Ecospace Junction", "Bellandur", "Bengaluru Urban", 12.9260, 77.6762, "CCTV"),
    ("Peenya Industrial Area 2nd Stage", "Peenya", "Bengaluru Urban", 13.0285, 77.5197, "CCTV"),
    # Bengaluru — junctions, flyovers, tolls (plate readers)
    ("Silk Board Junction", "BTM Layout", "Bengaluru Urban", 12.9172, 77.6229, "ANPR"),
    ("Hebbal Flyover Loop", "Hebbal", "Bengaluru Urban", 13.0358, 77.5970, "ANPR"),
    ("Mekhri Circle Underpass", "Sadashivanagar", "Bengaluru Urban", 13.0141, 77.5838, "ANPR"),
    ("KR Puram Hanging Bridge", "KR Puram", "Bengaluru Urban", 13.0050, 77.6960, "ANPR"),
    ("Tin Factory Junction", "Old Madras Road", "Bengaluru Urban", 12.9968, 77.6697, "ANPR"),
    ("Marathahalli Bridge", "Marathahalli", "Bengaluru Urban", 12.9569, 77.7011, "ANPR"),
    ("Electronic City Toll Plaza", "Hosur Road", "Bengaluru Urban", 12.8452, 77.6602, "ANPR"),
    ("Domlur Flyover", "Domlur", "Bengaluru Urban", 12.9610, 77.6387, "ANPR"),
    ("Old Airport Road – HAL Junction", "HAL", "Bengaluru Urban", 12.9592, 77.6627, "ANPR"),
    ("Jalahalli Cross, Tumakuru Road", "Jalahalli", "Bengaluru Urban", 13.0451, 77.5180, "ANPR"),
    ("Nayandahalli Junction", "Mysore Road", "Bengaluru Urban", 12.9420, 77.5240, "ANPR"),
    ("Kengeri Satellite Town Junction", "Kengeri", "Bengaluru Urban", 12.9081, 77.4855, "ANPR"),
    ("Bannerghatta Road – Meenakshi Junction", "Bannerghatta Road", "Bengaluru Urban", 12.8760, 77.5958, "ANPR"),
    ("Sarjapur Road – Wipro Junction", "Sarjapur Road", "Bengaluru Urban", 12.9010, 77.6870, "ANPR"),
    ("Yelahanka New Town Circle", "Yelahanka", "Bengaluru Urban", 13.1007, 77.5963, "ANPR"),
    ("Hennur Main Road Junction", "Hennur", "Bengaluru Urban", 13.0330, 77.6400, "ANPR"),
    ("RT Nagar Main Road", "RT Nagar", "Bengaluru Urban", 13.0213, 77.5946, "ANPR"),
    ("Vijayanagar Toll Gate Circle", "Vijayanagar", "Bengaluru Urban", 12.9719, 77.5333, "ANPR"),
    ("Outer Ring Road Night Barricade", "Outer Ring Road", "Bengaluru Urban", 12.9279, 77.6271, "ANPR"),
    ("Hosur Road – Bommanahalli", "Bommanahalli", "Bengaluru Urban", 12.9010, 77.6240, "ANPR"),
    ("Navale Gate, NH-44 Devanahalli", "Devanahalli", "Bengaluru Rural", 13.2457, 77.7120, "ANPR"),
    ("Nelamangala Toll Plaza", "Nelamangala", "Bengaluru Rural", 13.0985, 77.3920, "ANPR"),
    ("Hoskote Toll Plaza", "Hoskote", "Bengaluru Rural", 13.0700, 77.7980, "ANPR"),
    # Mysuru
    ("Mysuru Palace North Gate", "Palace", "Mysuru", 12.3052, 76.6552, "PTZ"),
    ("Mysuru City Bus Stand", "Sayyaji Rao Road", "Mysuru", 12.3106, 76.6526, "FACE"),
    ("Devaraja Market Entrance", "Devaraja Mohalla", "Mysuru", 12.3110, 76.6510, "PTZ"),
    ("Mysuru Junction Railway Station", "Yadavagiri", "Mysuru", 12.3164, 76.6460, "FACE"),
    ("Hunsur Road Ring Junction", "Hinkal", "Mysuru", 12.3290, 76.6080, "ANPR"),
    ("Bengaluru–Mysuru Expressway Entry", "Manipal Hospital Junction", "Mysuru", 12.3335, 76.6820, "ANPR"),
    # Mangaluru / Udupi
    ("Hampankatta Circle", "Hampankatta", "Dakshina Kannada", 12.8697, 74.8420, "PTZ"),
    ("Mangaluru Central Railway Station", "Attavar", "Dakshina Kannada", 12.8638, 74.8423, "FACE"),
    ("KSRTC Bijai Bus Stand", "Bejai", "Dakshina Kannada", 12.8840, 74.8420, "FACE"),
    ("Pumpwell Circle Flyover", "Pumpwell", "Dakshina Kannada", 12.8690, 74.8650, "ANPR"),
    ("Panambur Port Gate", "Panambur", "Dakshina Kannada", 12.9353, 74.8077, "ANPR"),
    ("Talapady Border Checkpost", "Talapady", "Dakshina Kannada", 12.7600, 74.8800, "ANPR"),
    ("Udupi Sri Krishna Temple Car Street", "Car Street", "Udupi", 13.3409, 74.7520, "PTZ"),
    ("Manipal Tiger Circle", "Manipal", "Udupi", 13.3523, 74.7849, "FACE"),
    # North Karnataka
    ("Hubballi Chennamma Circle", "Hubballi", "Dharwad", 15.3500, 75.1384, "PTZ"),
    ("Hubballi Railway Station", "Hubballi", "Dharwad", 15.3497, 75.1497, "FACE"),
    ("Dharwad Jubilee Circle", "Dharwad", "Dharwad", 15.4589, 75.0078, "ANPR"),
    ("Belagavi Rani Channamma Circle", "Belagavi", "Belagavi", 15.8590, 74.5140, "PTZ"),
    ("Belagavi Central Bus Stand", "Belagavi", "Belagavi", 15.8625, 74.5110, "FACE"),
    ("Kognoli Toll Plaza, NH-48", "Nippani", "Belagavi", 16.3800, 74.3600, "ANPR"),
    ("Kalaburagi Central Bus Stand", "Kalaburagi", "Kalaburagi", 17.3297, 76.8343, "FACE"),
    ("Kalaburagi Jagat Circle", "Kalaburagi", "Kalaburagi", 17.3330, 76.8330, "ANPR"),
    ("Vijayapura Gol Gumbaz Road", "Vijayapura", "Vijayapura", 16.8302, 75.7361, "CCTV"),
    ("Bidar Fort Road Junction", "Bidar", "Bidar", 17.9230, 77.5280, "CCTV"),
    ("Raichur Station Road", "Raichur", "Raichur", 16.2120, 77.3439, "CCTV"),
    ("Ballari Royal Circle", "Ballari", "Ballari", 15.1394, 76.9214, "ANPR"),
    ("Hosapete Central Bus Stand", "Hosapete", "Vijayanagara", 15.2689, 76.3909, "FACE"),
    ("Hampi Virupaksha Bazaar", "Hampi", "Vijayanagara", 15.3350, 76.4600, "PTZ"),
    ("Karwar Sadashivgad Bridge", "Karwar", "Uttara Kannada", 14.8134, 74.1295, "ANPR"),
    # Central / South
    ("Shivamogga Gopi Circle", "Shivamogga", "Shivamogga", 13.9299, 75.5681, "PTZ"),
    ("Tumakuru Town Hall Circle", "Tumakuru", "Tumakuru", 13.3409, 77.1010, "CCTV"),
    ("Davanagere PB Road Junction", "Davanagere", "Davanagere", 14.4644, 75.9218, "ANPR"),
    ("Chitradurga Fort Road", "Chitradurga", "Chitradurga", 14.2251, 76.3980, "CCTV"),
    ("Hassan NR Circle", "Hassan", "Hassan", 13.0072, 76.0962, "CCTV"),
    ("Mandya Silver Jubilee Park Junction", "Mandya", "Mandya", 12.5223, 76.8970, "ANPR"),
    ("Kolar Clock Tower", "Kolar", "Kolar", 13.1370, 78.1300, "CCTV"),
    ("Madikeri Raja Seat Road", "Madikeri", "Kodagu", 12.4200, 75.7330, "CCTV"),
]

DISTRICT_FIX = {"Mangaluru": "Dakshina Kannada", "Hubballi-Dharwad": "Dharwad"}
OWNERS = {"FACE": "KSP Safe City — Face Analytics", "PTZ": "KSP Safe City — PTZ",
          "ANPR": "Traffic Police ANPR", "CCTV": "Municipal CCTV (integrated)"}

# OCR confusions, both directions. Substituting one of these costs less than an
# arbitrary substitution because it is what a plate reader actually gets wrong.
_CONFUSABLE = {("0", "O"), ("0", "D"), ("O", "D"), ("1", "I"), ("1", "L"), ("8", "B"), ("5", "S"),
               ("2", "Z"), ("6", "G"), ("4", "A"), ("7", "T"), ("0", "Q"), ("M", "N"), ("U", "V")}
_CONFUSABLE |= {(b, a) for a, b in _CONFUSABLE}

# Each confusable group collapses to one representative, so a read that differs from
# a registered plate only by OCR confusions has the same canonical form.
_CANON = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "B": "8", "S": "5", "Z": "2",
          "G": "6", "A": "4", "T": "7", "N": "M", "V": "U"}
IST = timezone(timedelta(hours=5, minutes=30))     # Karnataka local time

_lock = threading.Lock()
_state = {}


def _h(*parts):
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    try:
        return bool(v) and not pd.isna(v)
    except (TypeError, ValueError):
        return bool(v)


def normalize_plate(p):
    return "".join(ch for ch in str(p or "").upper() if ch.isalnum())


def canonical_plate(p):
    return "".join(_CANON.get(ch, ch) for ch in normalize_plate(p))


def _deletes(s):
    return {s[:i] + s[i + 1:] for i in range(len(s))}


def plate_distance(a, b):
    """Edit distance where OCR-confusable substitutions cost 0.35."""
    a, b = normalize_plate(a), normalize_plate(b)
    n, m = len(a), len(b)
    prev = [float(j) for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [float(i)] + [0.0] * m
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                sub = 0.0
            elif (a[i - 1], b[j - 1]) in _CONFUSABLE:
                sub = 0.35
            else:
                sub = 1.0
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + sub)
        prev = cur
    return prev[m]


# =============================================================================
# Build: cameras, gallery, plate registry
# =============================================================================
def ensure(cache):
    """Build the network and gallery once per loaded corpus."""
    crim = cache.get("criminals")
    key = (id(crim), len(crim) if crim is not None else 0)
    with _lock:
        if _state.get("key") == key:
            return _state
        t0 = time.time()
        _state.clear()
        _state["key"] = key
        _state["cameras"] = _build_cameras(cache)
        _state["cam_index"] = {c["camera_id"]: i for i, c in enumerate(_state["cameras"])}
        _build_gallery(cache)
        _build_plates(cache)
        _state["blocks"] = {}
        _state["decisions"] = _load_decisions()
        _state["watchlists"] = {}
        _state["eval"] = None
        LOG.info("surveillance network ready in %.2fs: %d cameras, %d gallery faces",
                 time.time() - t0, len(_state["cameras"]), len(_state["gallery_ids"]))
        return _state


def _build_cameras(cache):
    rng = np.random.default_rng(SEED)
    cams = []
    for i, (name, area, district, lat, lng, typ) in enumerate(SITES):
        code = "".join(w[0] for w in district.split())[:3].upper()
        cams.append({"camera_id": f"CAM-{code}-{i + 1:04d}", "name": name, "area": area,
                     "district": district, "lat": lat, "lng": lng, "type": typ,
                     "landmark": True})
    df = cache.get("cctv")
    if df is not None and not df.empty:
        types = ["CCTV", "CCTV", "FACE", "ANPR", "PTZ"]
        for r in df.to_dict("records"):
            d = DISTRICT_FIX.get(r["district"], r["district"])
            t = types[_h(r["camera_id"]) % len(types)]
            cams.append({"camera_id": r["camera_id"], "name": f"{d} junction camera {r['camera_id'][-4:]}",
                         "area": d, "district": d, "lat": float(r["lat"]), "lng": float(r["lng"]),
                         "type": t, "landmark": False})
    for c in cams:
        u = _h("health", c["camera_id"]) % 1000 / 1000
        c["status"] = "offline" if u < 0.05 else "degraded" if u < 0.12 else "online"
        c["uptime_pct"] = round(99.6 - u * 9, 1) if c["status"] == "online" else round(70 + u * 100, 1)
        c["resolution"] = ["1080p", "4K", "1080p", "720p"][_h("res", c["camera_id"]) % 4]
        c["fps"] = 25 if c["type"] != "ANPR" else 30
        c["owner"] = OWNERS[c["type"]]
        c["night_ir"] = c["type"] in ("ANPR", "PTZ") or _h("ir", c["camera_id"]) % 3 == 0
        # Optical quality drives match confidence: 4K PTZ beats a 720p municipal box.
        base = {"4K": 0.92, "1080p": 0.82, "720p": 0.66}[c["resolution"]]
        c["quality"] = round(base * (0.9 if c["status"] == "degraded" else 1.0), 2)
        c["heading"] = _h("hd", c["camera_id"]) % 360
        c["analytics"] = {"FACE": ["face"], "PTZ": ["face"], "ANPR": ["plate"], "CCTV": ["face", "plate"]}[c["type"]]
    del rng
    return cams


def _build_gallery(cache):
    crim = cache.get("criminals")
    ids = sorted(str(x) for x in crim["criminal_id"]) if crim is not None else []
    rng = np.random.default_rng(SEED + 1)
    g = rng.standard_normal((len(ids), EMBED_DIM)).astype(np.float32)
    g /= np.linalg.norm(g, axis=1, keepdims=True)
    _state["gallery_ids"] = ids
    _state["gallery_pos"] = {s: i for i, s in enumerate(ids)}
    _state["gallery"] = g
    rows = {str(r["criminal_id"]): r for r in crim.to_dict("records")} if crim is not None else {}
    rec = (cache.get("recidivism") or {})
    top = {str(t.get("criminal_id")): t for t in (rec.get("top") or [])}
    _state["rec_top"] = list(rec.get("top") or [])
    people = {}
    for sid in ids:
        r = rows.get(sid, {})
        alias = r.get("alias")
        people[sid] = {"suspect_id": sid, "name": r.get("name"),
                       "alias": None if alias is None or pd.isna(alias) else str(alias).strip("'"),
                       "photo_url": r.get("photo_url"), "risk_level": r.get("risk_level"),
                       "gang": None if pd.isna(r.get("gang_affiliation")) else r.get("gang_affiliation"),
                       "active_warrant": _truthy(r.get("active_warrant")),
                       "rowdy_sheeter": _truthy(r.get("rowdy_sheeter")),
                       "history_sheeter": _truthy(r.get("history_sheet")),
                       "prior_firs": int(r.get("past_fir_count") or 0),
                       "recidivism_index": (rec.get("scores") or {}).get(sid),
                       "recidivism_band": (top.get(sid) or {}).get("band")}
    _state["people"] = people
    # Where each person operates: the districts of the cases naming them.
    links, firs = cache.get("links"), cache.get("firs")
    home = {}
    if links is not None and firs is not None:
        m = links.merge(firs[["case_id", "district"]], on="case_id", how="left")
        for sid, d in zip(m["suspect_id"].astype(str), m["district"].astype(str)):
            home.setdefault(sid, []).append(d)
    _state["home"] = home


def _build_plates(cache):
    veh = cache.get("vehicles")
    plates = {}
    if veh is not None and not veh.empty:
        for r in veh.to_dict("records"):
            plates[normalize_plate(r["vehicle_number"])] = {
                "vehicle_number": r["vehicle_number"], "make_model": r.get("make_model"),
                "theft_district": DISTRICT_FIX.get(r.get("theft_district"), r.get("theft_district")),
                "theft_date": str(r.get("theft_date")), "recovery_status": r.get("recovery_status"),
                "linked_case_id": r.get("linked_case_id")}
    _state["plates"] = plates
    # Symmetric-delete index over canonical forms: any read within one edit of a
    # registered plate shares the plate itself or one of its single deletions.
    idx = {}
    for p in plates:
        c = canonical_plate(p)
        for k in {c} | _deletes(c):
            idx.setdefault(k, set()).add(p)
    _state["plate_index"] = idx


# =============================================================================
# Matching
# =============================================================================
def match_face(probe, k=3):
    """1:N identification: cosine against the whole gallery, top-k, threshold + margin."""
    g = _state["gallery"]
    sims = g @ probe.astype(np.float32)
    top = np.argpartition(-sims, k)[:k]
    top = top[np.argsort(-sims[top])]
    cands = [{"suspect_id": _state["gallery_ids"][i], "similarity": round(float(sims[i]), 4)} for i in top]
    best = cands[0]["similarity"]
    margin = round(best - cands[1]["similarity"], 4)
    if best >= CONFIRMED_SIM and margin >= MIN_MARGIN:
        band = "strong"
    elif best >= POSSIBLE_SIM:
        band = "possible"
    else:
        band = "none"
    return {"band": band, "similarity": best, "margin": margin, "candidates": cands}


def _match_faces_batch(probes, k=3):
    """Same as match_face for many probes, chunked so memory stays small.

    A chunk is chunk x gallery float64 similarities and argpartition copies it again; at
    512 probes against 9,000 people that was ~110 MB at once, which on its own tipped
    the 512 MB container over the first time the CCTV page asked for the model card."""
    out = []
    g = _state["gallery"]
    for s in range(0, len(probes), 64):
        sims = probes[s:s + 64] @ g.T
        top = np.argpartition(sims, -k, axis=1)[:, -k:]
        for row, idx in zip(sims, top):
            idx = idx[np.argsort(-row[idx])]
            cands = [{"suspect_id": _state["gallery_ids"][i], "similarity": round(float(row[i]), 4)} for i in idx]
            best = cands[0]["similarity"]
            margin = round(best - cands[1]["similarity"], 4)
            band = ("strong" if best >= CONFIRMED_SIM and margin >= MIN_MARGIN
                    else "possible" if best >= POSSIBLE_SIM else "none")
            out.append({"band": band, "similarity": best, "margin": margin, "candidates": cands})
    return out


def match_plate(read, max_distance=1.2):
    """Exact or OCR-tolerant match of a plate read against the stolen-vehicle registry."""
    n = normalize_plate(read)
    plates = _state.get("plates") or {}
    if n in plates:
        return {"kind": "exact", "distance": 0.0, **plates[n]}
    best, best_d = None, max_distance + 1
    c = canonical_plate(n)
    idx = _state.get("plate_index", {})
    cands = set()
    for k in {c} | _deletes(c):
        cands |= idx.get(k, set())
    for p in cands:
        d = plate_distance(n, p)
        if d < best_d:
            best, best_d = p, d
    if best is not None and best_d <= max_distance:
        return {"kind": "fuzzy", "distance": round(best_d, 2), **plates[best]}
    return None


def _ocr_corrupt(plate, rng, p=0.28):
    s = list(normalize_plate(plate))
    if rng.random() < p and s:
        i = int(rng.integers(0, len(s)))
        subs = [b for a, b in _CONFUSABLE if a == s[i]]
        if subs:
            s[i] = subs[int(rng.integers(0, len(subs)))]
    if rng.random() < 0.04 and len(s) > 6:
        del s[int(rng.integers(0, len(s)))]
    raw = "".join(s)
    # Readers print with hyphens about half the time.
    if len(raw) == 10 and rng.random() < 0.5:
        raw = f"{raw[:2]}-{raw[2:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _random_plate(rng):
    letters = "ABCDEFGHJKLMNPRSTUVWXYZ"
    return (f"KA{int(rng.integers(1, 71)):02d}{letters[int(rng.integers(0, len(letters)))]}"
            f"{letters[int(rng.integers(0, len(letters)))]}{int(rng.integers(1000, 9999))}")


def _probe_for(sid, quality, rng):
    """A noisy sighting of a registered face. Noise grows as quality falls."""
    g = _state["gallery"][_state["gallery_pos"][sid]]
    sigma = 0.38 + (1 - quality) * 0.95
    e = g + rng.standard_normal(EMBED_DIM).astype(np.float32) * (sigma / math.sqrt(EMBED_DIM))
    return e / np.linalg.norm(e)


def _stranger_probe(rng):
    """Someone not in the registry. A share of them resemble a registered face."""
    e = rng.standard_normal(EMBED_DIM).astype(np.float32)
    e /= np.linalg.norm(e)
    if rng.random() < 0.10:
        g = _state["gallery"][int(rng.integers(0, len(_state["gallery_ids"])))]
        a = float(rng.uniform(0.35, 0.82))
        e = a * g + math.sqrt(max(0.0, 1 - a * a)) * e
        e /= np.linalg.norm(e)
    return e


# =============================================================================
# The event stream
# =============================================================================
def _light(ts):
    hour = datetime.fromtimestamp(ts, IST).hour
    return 0.78 if (hour >= 20 or hour < 6) else 1.0, hour


def _block(b):
    """All events in time block b, generated and matched once."""
    blocks = _state["blocks"]
    if b in blocks:
        return blocks[b]
    rng = np.random.default_rng(_h(SEED, "block", b))
    cams = [c for c in _state["cameras"] if c["status"] != "offline"]
    face_cams = [c for c in cams if "face" in c["analytics"]]
    plate_cams = [c for c in cams if "plate" in c["analytics"]]
    # Bengaluru carries most of the network and most of the footfall.
    fw = np.array([3.0 if c["district"] == "Bengaluru Urban" else 1.0 for c in face_cams]) * \
        np.array([2.0 if c["landmark"] else 1.0 for c in face_cams])
    pw = np.array([3.0 if c["district"] == "Bengaluru Urban" else 1.0 for c in plate_cams]) * \
        np.array([2.0 if c["landmark"] else 1.0 for c in plate_cams])
    fw, pw = fw / fw.sum(), pw / pw.sum()

    start = b * BLOCK_S
    n = int(rng.poisson(BLOCK_S / MEAN_GAP_S))
    times = np.sort(rng.uniform(start, start + BLOCK_S, n))
    people = _state["people"]
    ids = _state["gallery_ids"]
    by_district = {}
    for sid in ids:
        for d in set(_state["home"].get(sid, [])):
            by_district.setdefault(d, []).append(sid)
    priority = [s for s in ids if people[s]["active_warrant"] or people[s]["rowdy_sheeter"]] or ids
    stolen = [p for p, v in _state["plates"].items() if v["recovery_status"] != "Recovered"]

    events, probes, face_idx = [], [], []
    for i, ts in enumerate(times):
        ts = float(ts)
        light, _hour = _light(ts)
        if rng.random() < 0.58:
            cam = face_cams[int(rng.choice(len(face_cams), p=fw))]
            q = cam["quality"] * light * float(rng.uniform(0.85, 1.05))
            r = rng.random()
            if r < 0.10:
                pool = by_district.get(cam["district"]) or ids
                truth = pool[int(rng.integers(0, len(pool)))]
            elif r < 0.115:
                truth = priority[int(rng.integers(0, len(priority)))]
            else:
                truth = None
            probes.append(_probe_for(truth, q, rng) if truth else _stranger_probe(rng))
            face_idx.append(len(events))
            events.append({"kind": "face", "ts": ts, "camera_id": cam["camera_id"],
                           "quality": round(min(1.0, q), 2), "_truth": truth})
        else:
            cam = plate_cams[int(rng.choice(len(plate_cams), p=pw))]
            truth = stolen[int(rng.integers(0, len(stolen)))] if (stolen and rng.random() < 0.02) else _random_plate(rng)
            read = _ocr_corrupt(truth, rng, p=0.28 * (1.3 if light < 1 else 1.0))
            events.append({"kind": "plate", "ts": ts, "camera_id": cam["camera_id"],
                           "quality": round(cam["quality"] * light, 2), "read": read, "_truth": truth,
                           "confidence": round(float(rng.uniform(0.80, 0.99)) * light, 2)})

    # Movement trails: a few registered people seen at successive nearby cameras.
    # The people most worth finding move through the network most visibly in the demo:
    # half the trails follow someone from the top of the recidivism watchlist.
    top = [str(t.get("criminal_id")) for t in ((_state.get("rec_top") or [])[:12])] or priority
    for t in range(int(rng.integers(3, 6))):
        pool = top if rng.random() < 0.5 else priority
        sid = pool[int(rng.integers(0, len(pool)))]
        cam = face_cams[int(rng.choice(len(face_cams), p=fw))]
        ts = start + float(rng.uniform(0, BLOCK_S * 0.8))
        for hop in range(int(rng.integers(3, 7))):
            q = cam["quality"] * _light(ts)[0]
            probes.append(_probe_for(sid, q, rng))
            face_idx.append(len(events))
            events.append({"kind": "face", "ts": ts, "camera_id": cam["camera_id"],
                           "quality": round(q, 2), "_truth": sid, "_trail": f"T{b}-{t}"})
            near = sorted(face_cams, key=lambda c: (c["lat"] - cam["lat"]) ** 2 + (c["lng"] - cam["lng"]) ** 2)[1:6]
            cam = near[int(rng.integers(0, len(near)))]
            ts += float(rng.uniform(600, 2400))

    if probes:
        results = _match_faces_batch(np.vstack(probes))
        for j, res in zip(face_idx, results):
            events[j]["match"] = res
    for e in events:
        if e["kind"] == "plate":
            e["match"] = match_plate(e["read"])
    events.sort(key=lambda e: e["ts"])
    for k, e in enumerate(events):
        e["event_id"] = f"EV-{b}-{k:05d}"
    blocks[b] = events
    for old in [x for x in blocks if x < b - 6]:
        del blocks[old]
    return events


def _priority(e, watch):
    """P1 / P2 / P3 or None (not an alert)."""
    m = e.get("match")
    if not m:
        return None, []
    if e["kind"] == "plate":
        reasons = [f"{'Exact' if m['kind'] == 'exact' else 'OCR-tolerant'} match to a stolen vehicle"
                   if m.get("recovery_status") != "Recovered" else "Matches a vehicle reported stolen (since recovered)"]
        wl = normalize_plate(m["vehicle_number"]) in watch["plates"]
        if wl:
            reasons.insert(0, "On your watchlist")
        if m.get("recovery_status") == "Recovered" and not wl:
            return None, []
        return ("P1" if (m["kind"] == "exact" or wl) else "P2"), reasons
    if m["band"] == "none":
        return None, []
    sid = m["candidates"][0]["suspect_id"]
    p = _state["people"][sid]
    wl = sid in watch["people"]
    reasons = []
    if wl:
        reasons.append("On your watchlist")
    if p["active_warrant"]:
        reasons.append("Active warrant")
    if p["rowdy_sheeter"]:
        reasons.append("Rowdy-sheeter")
    if p["recidivism_band"] in ("Extreme", "High"):
        reasons.append(f"{p['recidivism_band']} recidivism band")
    if p["risk_level"] in ("Critical", "High"):
        reasons.append(f"{p['risk_level']} risk")
    if m["band"] == "possible" and not wl:
        return None, []
    if wl or (p["active_warrant"] and m["band"] == "strong"):
        return "P1", reasons
    if reasons:
        return "P2", reasons
    return "P3", ["Registered offender"]


def _events_between(t0, t1):
    out = []
    for b in range(int(t0 // BLOCK_S), int(t1 // BLOCK_S) + 1):
        out.extend(e for e in _block(b) if t0 < e["ts"] <= t1)
    return out


def _public(e, watch, include_people=True):
    cam = _state["cameras"][_state["cam_index"][e["camera_id"]]]
    out = {"event_id": e["event_id"], "kind": e["kind"], "ts": e["ts"],
           "time": datetime.fromtimestamp(e["ts"], timezone.utc).isoformat(),
           "camera_id": cam["camera_id"], "camera_name": cam["name"], "district": cam["district"],
           "area": cam["area"], "lat": cam["lat"], "lng": cam["lng"], "quality": e["quality"]}
    pr, reasons = _priority(e, watch)
    out["priority"], out["reasons"] = pr, reasons
    m = e.get("match")
    if e["kind"] == "plate":
        out["plate_read"] = e["read"]
        out["read_confidence"] = e.get("confidence")
        out["plate_match"] = m
    else:
        if m and m["band"] != "none":
            sid = m["candidates"][0]["suspect_id"]
            out["face_match"] = {"band": m["band"], "similarity": m["similarity"], "margin": m["margin"],
                                 "suspect_id": sid,
                                 "person": _state["people"][sid] if include_people else None,
                                 "candidates": [dict(c, name=_state["people"][c["suspect_id"]]["name"] if include_people else None,
                                                     photo_url=_state["people"][c["suspect_id"]]["photo_url"] if include_people else None)
                                                for c in m["candidates"]]}
        else:
            # A stranger: counted, never identified, never stored.
            out["face_match"] = None
    d = _state["decisions"].get(e["event_id"])
    out["status"] = d["status"] if d else "new"
    if d:
        out["decision"] = {k: d.get(k) for k in ("status", "actor", "note", "decided_at")}
    if e.get("_trail"):
        out["trail_id"] = e["_trail"]
    return out


def _watch(actor):
    return _state["watchlists"].setdefault(actor or "_anon", {"people": set(), "plates": set()})


# =============================================================================
# Public API used by the routes
# =============================================================================
def cameras(cache, district=None, type_=None, status=None):
    ensure(cache)
    cams = _state["cameras"]
    if district:
        cams = [c for c in cams if c["district"].lower() == district.lower()]
    if type_:
        cams = [c for c in cams if c["type"] == type_.upper()]
    if status:
        cams = [c for c in cams if c["status"] == status]
    now = time.time()
    recent = _events_between(now - 3600, now)
    hits = {}
    for e in recent:
        if e.get("match") and (e["kind"] == "plate" or e["match"]["band"] == "strong"):
            hits[e["camera_id"]] = hits.get(e["camera_id"], 0) + 1
    out = [dict(c, matches_last_hour=hits.get(c["camera_id"], 0)) for c in cams]
    allc = _state["cameras"]
    summary = {"total": len(allc),
               "online": sum(c["status"] == "online" for c in allc),
               "degraded": sum(c["status"] == "degraded" for c in allc),
               "offline": sum(c["status"] == "offline" for c in allc),
               "by_type": pd.Series([c["type"] for c in allc]).value_counts().to_dict(),
               "by_district": pd.Series([c["district"] for c in allc]).value_counts().to_dict(),
               "detections_last_hour": len(recent)}
    return {"cameras": out, "summary": summary, "simulated": True,
            "note": "Simulated camera network placed at public landmarks; positions approximate. "
                    "Not the KSP Safe City inventory."}


def feed(cache, actor, since=None, camera_id=None, kind=None, matches_only=False, limit=80, district=None):
    ensure(cache)
    now = time.time()
    t0 = max(now - HISTORY_S, float(since)) if since else now - 3600
    evs = _events_between(t0, now)
    if camera_id:
        evs = [e for e in evs if e["camera_id"] == camera_id]
    if kind:
        evs = [e for e in evs if e["kind"] == kind]
    if district:
        evs = [e for e in evs if _state["cameras"][_state["cam_index"][e["camera_id"]]]["district"].lower() == district.lower()]
    watch = _watch(actor)
    faces = [e for e in evs if e["kind"] == "face"]
    plates_n = sum(1 for e in evs if e["kind"] == "plate")
    discarded = sum(1 for e in faces if not e.get("match") or e["match"]["band"] == "none")
    if matches_only:
        evs = [e for e in evs if e.get("match") and (e["kind"] == "plate" or e["match"]["band"] != "none")]
    evs = evs[-max(1, min(int(limit), 300)):]
    return {"now": now, "events": [_public(e, watch) for e in reversed(evs)],
            "window": {"from": t0, "to": now},
            "counts": {"detections": len(faces) + plates_n, "faces": len(faces), "plates": plates_n,
                       "faces_discarded_no_match": discarded},
            "privacy": "Faces with no registry match are counted and discarded; no identity is kept."}


def alerts(cache, actor, since=None, status=None, priority=None, district=None, limit=100):
    ensure(cache)
    now = time.time()
    t0 = max(now - HISTORY_S, float(since)) if since else now - HISTORY_S
    watch = _watch(actor)
    out = []
    for e in _events_between(t0, now):
        pr, _ = _priority(e, watch)
        if pr in ("P1", "P2"):
            out.append(e)
    pub = [_public(e, watch) for e in reversed(out)]
    if status:
        pub = [a for a in pub if a["status"] == status]
    if priority:
        pub = [a for a in pub if a["priority"] == priority]
    if district:
        pub = [a for a in pub if a["district"].lower() == district.lower()]
    counts = {"P1": sum(a["priority"] == "P1" for a in pub), "P2": sum(a["priority"] == "P2" for a in pub),
              "new": sum(a["status"] == "new" for a in pub),
              "verified": sum(a["status"] == "verified" for a in pub),
              "rejected": sum(a["status"] == "rejected" for a in pub)}
    return {"now": now, "alerts": pub[:max(1, min(int(limit), 300))], "counts": counts,
            "policy": "A match is a lead, not an identification. Verify against the dossier before any action."}


def _find_event(event_id):
    try:
        b = int(event_id.split("-")[1])
    except (IndexError, ValueError):
        return None
    for e in _block(b):
        if e["event_id"] == event_id:
            return e
    return None


DECISIONS = {"acknowledge": "acknowledged", "verify": "verified", "reject": "rejected",
             "escalate": "escalated", "reopen": "new"}


def decide(cache, actor, event_id, action, note=None):
    ensure(cache)
    if action not in DECISIONS:
        return {"error": f"action must be one of {sorted(DECISIONS)}"}, 400
    e = _find_event(event_id)
    if not e or e["ts"] > time.time():
        return {"error": "no such alert"}, 404
    if action in ("verify", "reject") and not (note or "").strip():
        return {"error": "a note is required to verify or reject a match — say what you checked"}, 400
    rec = {"event_id": event_id, "status": DECISIONS[action], "action": action, "actor": actor,
           "note": (note or "")[:500], "decided_at": datetime.now(timezone.utc).isoformat()}
    _state["decisions"][event_id] = rec
    try:
        with open(DECISION_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError as ex:
        LOG.warning("surveillance decision not persisted: %s", ex)
    return {"ok": True, "alert": _public(e, _watch(actor))}, 200


def _load_decisions():
    out = {}
    try:
        with open(DECISION_LOG_PATH, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    out[r["event_id"]] = r
                except (ValueError, KeyError):
                    continue
    except OSError:
        pass
    return out


def track(cache, actor, suspect_id, hours=24):
    """Strong-band sightings of one person, oldest first — a movement trail."""
    ensure(cache)
    if suspect_id not in _state["people"]:
        return None
    now = time.time()
    watch = _watch(actor)
    hits = [e for e in _events_between(now - hours * 3600, now)
            if e["kind"] == "face" and e.get("match") and e["match"]["band"] in ("strong", "possible")
            and e["match"]["candidates"][0]["suspect_id"] == suspect_id]
    return {"suspect_id": suspect_id, "person": _state["people"][suspect_id],
            "sightings": [_public(e, watch) for e in hits],
            "last_seen": _public(hits[-1], watch) if hits else None,
            "note": "Sightings are matcher output. Each one is a lead until a person verifies it."}


def lookup_plate(cache, plate):
    ensure(cache)
    m = match_plate(plate, max_distance=2.0)
    now = time.time()
    reads = [e for e in _events_between(now - HISTORY_S, now) if e["kind"] == "plate"
             and plate_distance(e["read"], plate) <= 1.0]
    watch = _watch(None)
    return {"query": plate, "normalized": normalize_plate(plate), "registry_match": m,
            "recent_reads": [_public(e, watch) for e in reads[-10:]]}


def watchlist(cache, actor, add=None, remove=None):
    ensure(cache)
    w = _watch(actor)
    for item, op in ((add, "add"), (remove, "remove")):
        if not item:
            continue
        item = str(item).strip().upper()
        bucket = "people" if item.startswith("SUS-") else "plates"
        val = item if bucket == "people" else normalize_plate(item)
        if bucket == "people" and val not in _state["people"]:
            return {"error": f"{item} is not in the registry"}, 404
        (w[bucket].add if op == "add" else w[bucket].discard)(val)
    return {"people": [dict(_state["people"][s]) for s in sorted(w["people"])],
            "plates": [dict(_state["plates"].get(p, {"vehicle_number": p}), normalized=p) for p in sorted(w["plates"])]}, 200


def camera_detail(cache, actor, camera_id):
    ensure(cache)
    i = _state["cam_index"].get(camera_id)
    if i is None:
        return None
    now = time.time()
    evs = [e for e in _events_between(now - 6 * 3600, now) if e["camera_id"] == camera_id]
    watch = _watch(actor)
    faces = [e for e in evs if e["kind"] == "face"]
    return {"camera": _state["cameras"][i],
            "detections_6h": len(evs),
            "faces_discarded_no_match": sum(1 for e in faces if e["match"]["band"] == "none"),
            "matches": [_public(e, watch) for e in reversed(evs) if e.get("match") and
                        (e["kind"] == "plate" or e["match"]["band"] != "none")][:30]}


def evaluate(cache, n=1500):
    """Measure the matcher on fresh probes with known truth: the model card's numbers."""
    ensure(cache)
    if _state.get("eval"):
        return _state["eval"]
    rng = np.random.default_rng(SEED + 99)
    ids = _state["gallery_ids"]
    qualities = rng.uniform(0.55, 0.95, n)
    truth = [ids[int(rng.integers(0, len(ids)))] for _ in range(n)]
    gen = np.vstack([_probe_for(s, q, rng) for s, q in zip(truth, qualities)])
    imp = np.vstack([_stranger_probe(rng) for _ in range(n)])
    rg, ri = _match_faces_batch(gen), _match_faces_batch(imp)
    strong_g = [r["band"] == "strong" for r in rg]
    correct = [r["candidates"][0]["suspect_id"] == t for r, t in zip(rg, truth)]
    tpr = float(np.mean([s and c for s, c in zip(strong_g, correct)]))
    misid = float(np.mean([s and not c for s, c in zip(strong_g, correct)]))
    fpir = float(np.mean([r["band"] == "strong" for r in ri]))
    poss = float(np.mean([r["band"] != "none" for r in ri]))
    rank1 = float(np.mean(correct))
    low = qualities < 0.7
    tpr_low = float(np.mean([s and c for s, c, l in zip(strong_g, correct, low) if l]))
    tpr_high = float(np.mean([s and c for s, c, l in zip(strong_g, correct, low) if not l]))
    # Plates
    prng = np.random.default_rng(SEED + 7)
    stolen = list(_state["plates"])
    hits = exact = 0
    trials = min(600, len(stolen))
    for p in stolen[:trials]:
        read = _ocr_corrupt(p, prng, p=0.35)
        m = match_plate(read)
        exact += normalize_plate(read) == p
        hits += bool(m and normalize_plate(m["vehicle_number"]) == p)
    false_plate = sum(bool(match_plate(_random_plate(prng))) for _ in range(trials))
    _state["eval"] = {
        "face": {"probes": n, "impostor_probes": n, "gallery_size": len(ids),
                 "strong_threshold": CONFIRMED_SIM, "possible_threshold": POSSIBLE_SIM, "min_margin": MIN_MARGIN,
                 "true_identification_rate": round(tpr, 4), "misidentification_rate": round(misid, 4),
                 "false_positive_identification_rate": round(fpir, 4),
                 "impostor_reaching_possible_band": round(poss, 4), "rank1_accuracy": round(rank1, 4),
                 "tir_low_quality_or_night": round(tpr_low, 4), "tir_good_quality": round(tpr_high, 4)},
        "plate": {"trials": trials, "reads_with_ocr_error_pct": round(100 - exact / trials * 100, 1),
                  "recovered_by_matcher_pct": round(hits / trials * 100, 1),
                  "false_match_on_random_plates_pct": round(false_plate / trials * 100, 2)},
    }
    return _state["eval"]


def model_card(cache):
    ev = evaluate(cache)
    return {
        "name": "IntelHub sighting matcher (face 1:N + ANPR)",
        "simulated": True,
        "what_is_simulated": "Camera network, face embeddings and plate reads are generated. The matching, "
                             "thresholds and the measured error rates below are real computations on that data.",
        "face_method": "Cosine similarity of a 128-d probe against all registered offenders; top-3 candidates; "
                       f"'strong' needs similarity ≥ {CONFIRMED_SIM} and a margin ≥ {MIN_MARGIN} over the runner-up; "
                       f"'possible' is ≥ {POSSIBLE_SIM}.",
        "plate_method": "Normalised plate compared to the stolen-vehicle registry exactly, then by an edit distance "
                        "in which OCR-confusable substitutions (0/O, 1/I, 8/B, 5/S, 2/Z …) cost 0.35.",
        "evaluation": ev,
        "intended_use": "Order a queue of sightings for an officer to verify against the dossier.",
        "out_of_scope": "Identifying a person, establishing presence at a place as evidence, or triggering "
                        "any action without human verification.",
        "known_limits": ["Accuracy falls at night and on low-resolution cameras — see tir_low_quality_or_night.",
                         "Look-alikes produce false matches; the margin test reduces but does not remove them.",
                         "A real deployment must re-measure every number here on its own cameras and population, "
                         "including per age band and gender, before any operational use."],
        "retention": "Non-matching faces are discarded at detection time. Decisions are kept append-only.",
    }
