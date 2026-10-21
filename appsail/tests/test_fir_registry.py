"""FIR registration: schema-true rows, correct numbering, refusals with reasons.

* **Numbering is the corpus's.** CrimeNo = category, district (4), station (4), year,
  serial (5); the serial continues from the highest already issued for that
  (category, district, station, year), and a second registration takes the next one.
* **METADATA round-trips.** BriefFacts must end in the block main.py parses, or the
  FIR frame silently falls back to severity 50 and no weapon.
* **Every code is a master code.** An unknown station, section or occupation is a
  400 naming the field, never a row the rest of the platform cannot resolve.
* **Jurisdiction.** A station officer registers at their own station only.
"""
import base64
import io
import os
import re
import sys
from datetime import datetime

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
import main  # noqa: E402
import fir_registry as F  # noqa: E402

META = re.compile(r"\[METADATA: SEV=(\d+);WPN=([^;]+);VEH=([^;]+);TGT=([^;]+);FAT=(\d+);INJ=(\d+);WIT=(\d+)\]$")
NOW = datetime(2026, 10, 18, 12, 0, 0)


@pytest.fixture
def cache(monkeypatch, tmp_path):
    main.load_and_build_intelligence()
    # A private copy so registrations here never leak into other test modules, and a
    # private store: the real registered_firs.jsonl and fir_photos/ are never touched.
    c = {k: (v.copy() if isinstance(v, pd.DataFrame) else v) for k, v in list(main.data_cache.items())}
    seen = []
    monkeypatch.setattr(F, "STORE_PATH", str(tmp_path / "registered_firs.jsonl"))
    monkeypatch.setattr(F, "PHOTO_DIR", str(tmp_path / "fir_photos"))
    F.reset_photo_index()
    F.configure(lambda: c, on_registered=seen.append)
    c["_seen"] = seen
    yield c
    F.reset_photo_index()
    F.configure(lambda: main.data_cache, main._on_fir_registered, auth.current_identity)


def _station(c, name="Koramangala PS"):
    u = c["Unit"]
    return int(u.loc[u["UnitName"] == name, "UnitID"].iloc[0])


def _payload(c, **over):
    p = {
        "station_id": _station(c), "case_category_id": 1, "gravity_id": 2, "crime_type_id": 1,
        "sections": [{"act": "BNS", "section": "304(2)"}, {"act": "BNS", "section": "3(5)"}],
        "officer_id": int(c["Employee"]["EmployeeID"].iloc[0]),
        "incident_from": "2026-10-17T20:15", "incident_to": "2026-10-17T20:20", "info_received": "2026-10-17T21:05",
        "place_of_occurrence": "80 Feet Road, near Sony signal",
        "brief_facts": "The complainant was walking home when two persons on a motorcycle snatched her gold chain.",
        "weapon": "None", "vehicle": "Bajaj Pulsar", "target": "Public Area",
        "fatalities": 0, "injured": 1, "witnesses": 2,
        "complainant": {"name": "Lakshmi R", "age": 54, "gender_id": 2, "occupation_id": 1, "religion_id": 1, "caste_id": 1},
        "victims": [{"name": "Lakshmi R", "age": 54, "gender_id": 2}],
        "accused": [{"person_id": "SUS-1001", "age": 30, "gender_id": 1}, {"name": "Unknown rider", "gender_id": 1}],
    }
    p.update(over)
    return p


def test_a_registration_writes_schema_true_rows(cache):
    out = F.register(_payload(cache), now=NOW)
    cm = cache["CaseMaster"].iloc[-1]
    st = _station(cache)
    d = int(cache["Unit"].set_index("UnitID").loc[st, "DistrictID"])
    assert out["crime_no"] == cm["CrimeNo"] and cm["CrimeNo"].startswith(f"1{d:04d}{st:04d}2026")
    assert len(cm["CrimeNo"]) == 18 and cm["CaseNo"] == "2026" + cm["CrimeNo"][-5:]
    assert cm["CaseStatusID"] == 1 and cm["CrimeRegisteredDate"] == "2026-10-18"
    m = META.search(cm["BriefFacts"])
    assert m and m.group(2) == "None" and m.group(3) == "Bajaj Pulsar" and m.group(6) == "1"
    acc = cache["Accused"][cache["Accused"]["CaseMasterID"] == cm["CaseMasterID"]]
    assert list(acc["PersonID"]) == ["SUS-1001", ""]
    assert acc["AccusedName"].iloc[0] == main.data_cache["criminals"].set_index("criminal_id").loc["SUS-1001", "name"]
    asa = cache["ActSectionAssociation"][cache["ActSectionAssociation"]["CaseMasterID"] == cm["CaseMasterID"]]
    assert list(asa["SectionID"]) == ["304(2)", "3(5)"] and list(asa["SectionOrderID"]) == [1, 2]
    bundle = cache["_seen"][-1]
    assert bundle["links"] == [{"case_id": out["crime_no"], "suspect_id": "SUS-1001", "relationship": "Charged"}]
    assert os.path.exists(F.STORE_PATH) and F.stored_bundles()[-1]["crime_no"] == out["crime_no"]


def test_the_serial_continues_from_the_highest_issued(cache):
    st = _station(cache)
    d = int(cache["Unit"].set_index("UnitID").loc[st, "DistrictID"])
    prefix = f"1{d:04d}{st:04d}2026"
    nos = cache["CaseMaster"]["CrimeNo"].astype(str)
    prior = nos[nos.str.startswith(prefix)].str[-5:].astype(int).max() if nos.str.startswith(prefix).any() else 0
    a = F.register(_payload(cache), now=NOW)["crime_no"]
    b = F.register(_payload(cache), now=NOW)["crime_no"]
    assert int(a[-5:]) == prior + 1 and int(b[-5:]) == prior + 2
    assert cache["CaseMaster"]["CaseMasterID"].is_unique and cache["Victim"]["VictimMasterID"].is_unique


@pytest.mark.parametrize("over,field", [
    ({"station_id": 999999}, "station_id"),
    ({"sections": [{"act": "IPC", "section": "999"}]}, "sections.0"),
    ({"sections": []}, "sections"),
    ({"crime_type_id": 77}, "crime_type_id"),
    ({"incident_from": "2027-01-01T10:00"}, "incident_from"),
    ({"info_received": "2026-10-16T10:00"}, "info_received"),
    ({"latitude": 28.6, "longitude": 77.2}, "latitude"),
    ({"brief_facts": "too short"}, "brief_facts"),
    ({"brief_facts": "x" * 40 + " [METADATA: SEV=99]"}, "brief_facts"),
    ({"weapon": "Bazooka"}, "weapon"),
    ({"victims": []}, "victims"),
    ({"accused": [{"person_id": "SUS-0000", "gender_id": 1}]}, "accused.0.person_id"),
])
def test_bad_input_names_the_field(cache, over, field):
    p = _payload(cache, **over)
    if "complainant" in over:
        pass
    with pytest.raises(F.FIRError) as ex:
        F.register(p, now=NOW)
    assert field in ex.value.errors, ex.value.errors
    assert not cache["_seen"]


def test_a_bad_complainant_code_is_refused(cache):
    p = _payload(cache)
    p["complainant"]["occupation_id"] = 999
    with pytest.raises(F.FIRError) as ex:
        F.register(p, now=NOW)
    assert "complainant.occupation_id" in ex.value.errors


def _ident(level, district=None, station=None, perms=("register_fir",)):
    return {"user": "t", "permissions": list(perms), "scope": {"level": level, "district": district, "station": station}}


def test_jurisdiction(cache):
    p = _payload(cache)
    F.register(p, _ident("station", "Bengaluru Urban", "Koramangala PS"), now=NOW)
    with pytest.raises(PermissionError):
        F.register(p, _ident("station", "Bengaluru Urban", "Indiranagar PS"), now=NOW)
    with pytest.raises(PermissionError):
        F.register(p, _ident("district", "Mysuru"), now=NOW)
    with pytest.raises(PermissionError):
        F.register(p, _ident("state", perms=()), now=NOW)
    F.register(p, _ident("district", "Bengaluru Urban"), now=NOW)


def test_the_code_in_force_on_the_offence_date_is_enforced(cache):
    late = _payload(cache, sections=[{"act": "IPC", "section": "379"}, {"act": "IPC", "section": "34"}])
    with pytest.raises(F.FIRError) as ex:
        F.register(late, now=NOW)
    msg = ex.value.errors["sections"]
    assert "BNS 303(2)" in msg and "BNS 3(5)" in msg                       # the fix is named
    early = _payload(cache, sections=[{"act": "IPC", "section": "379"}], incident_from="2024-05-10T20:15",
                     incident_to="2024-05-10T20:20", info_received="2024-05-10T21:05")
    assert F.register(early, now=NOW)["crime_no"]                            # IPC is right for May 2024
    with pytest.raises(F.FIRError) as ex:
        F.register(_payload(cache, incident_from="2024-05-10T20:15", incident_to="2024-05-10T20:20",
                            info_received="2024-05-10T21:05"), now=NOW)     # BNS 304(2) did not exist yet
    assert "BNS 304(2)" in ex.value.errors["sections"]


def test_masters_cover_every_lookup(cache):
    m = F.masters()
    assert len(m["districts"]) == 31 and len(m["stations"]) == len(cache["Unit"])
    assert {s["act"] for s in m["sections"]} >= {"IPC", "BNS", "NDPS", "IT Act"}
    assert all(t["suggested_section"] for t in m["crime_types"])
    names = {(s["act"], s["section"]) for s in m["sections"]}
    assert all((t["suggested_section"]["act"], t["suggested_section"]["section"]) in names for t in m["crime_types"])
    assert all((t["suggested_section_bns"]["act"], t["suggested_section_bns"]["section"]) in names for t in m["crime_types"])
    assert ("BNS", "303(2)") in names and ("BNS", "103(1)") in names and m["bns_commencement"] == "2024-07-01"


# ------------------------------------------------------------------ photographs
# A real 48x40 JPEG carrying an EXIF block (camera make "TestCam", serial "SerialX123")
# and a comment, as a phone camera would produce one.
JPEG = base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/4QBCRXhpZgAATU0AKgAAAAgAAgEPAAIAAAAIAAAAJgEQAAIAAAALAAAALgAAAABUZXN0Q2FtAFNlcmlhbFgxMjMAAP/+ABBzZWNyZXQtY29tbWVudP/bAEMACgcHCAcGCggICAsKCgsOGBAODQ0OHRUWERgjHyUkIh8iISYrNy8mKTQpISIwQTE0OTs+Pj4lLkRJQzxINz0+O//bAEMBCgsLDg0OHBAQHDsoIig7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7O//AABEIACgAMAMBIgACEQEDEQH/xAAfAAABBQEBAQEBAQAAAAAAAAAAAQIDBAUGBwgJCgv/xAC1EAACAQMDAgQDBQUEBAAAAX0BAgMABBEFEiExQQYTUWEHInEUMoGRoQgjQrHBFVLR8CQzYnKCCQoWFxgZGiUmJygpKjQ1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4eLj5OXm5+jp6vHy8/T19vf4+fr/xAAfAQADAQEBAQEBAQEBAAAAAAAAAQIDBAUGBwgJCgv/xAC1EQACAQIEBAMEBwUEBAABAncAAQIDEQQFITEGEkFRB2FxEyIygQgUQpGhscEJIzNS8BVictEKFiQ04SXxFxgZGiYnKCkqNTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqCg4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2dri4+Tl5ufo6ery8/T19vf4+fr/2gAMAwEAAhEDEQA/AHUUUV8+e4FFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQB//Z")


def test_a_photo_keeps_the_picture_and_loses_the_metadata():
    clean = F.clean_jpeg(JPEG + b"TRAILING-PAYLOAD")
    assert clean[:2] == b"\xff\xd8" and clean[-2:] == b"\xff\xd9"
    for leak in (b"Exif", b"TestCam", b"SerialX123", b"secret-comment", b"TRAILING-PAYLOAD"):
        assert leak not in clean, leak
    assert b"\xff\xc0" in clean and b"\xff\xda" in clean     # frame header and scan survive
    assert F.clean_jpeg(clean) == clean                       # idempotent


@pytest.mark.parametrize("data", [b"\x89PNG\r\n\x1a\n" + b"0" * 100, b"GIF89a" + b"0" * 100, JPEG[:40], b""])
def test_anything_but_a_whole_jpeg_is_refused(data):
    with pytest.raises(F.PhotoError):
        F.clean_jpeg(data)


def test_a_registration_carries_its_photographs(cache):
    comp_photo, _ = F.save_photo(JPEG)
    acc_photo, _ = F.save_photo(JPEG)
    p = _payload(cache)
    p["complainant"]["photo_id"] = comp_photo
    p["accused"][1]["photo_id"] = acc_photo
    out = F.register(p, now=NOW)
    assert out["photos"] == 2
    got = F.photos_for(out["crime_no"])
    assert [(x["role"], x["photo_id"]) for x in got] == [("complainant", comp_photo), ("accused", acc_photo)]
    assert got[1]["name"] == "Unknown rider" and got[1]["person_id"] is None
    # The CCTNS rows stay schema-true: no photo column is invented on them.
    bundle = F.stored_bundles()[-1]
    assert all("photo" not in k.lower() for rows in bundle["tables"].values() for r in rows for k in r)
    # A fresh process rebuilds the index from the store.
    F.reset_photo_index()
    assert F.photos_for(out["crime_no"]) == got


def test_a_photo_id_that_was_never_uploaded_is_refused(cache):
    p = _payload(cache)
    p["victims"][0]["photo_id"] = "0" * 32
    with pytest.raises(F.FIRError) as ex:
        F.register(p, now=NOW)
    assert "victims.0.photo_id" in ex.value.errors


def test_unclaimed_uploads_expire_and_claimed_ones_stay(cache):
    kept, _ = F.save_photo(JPEG)
    stale, _ = F.save_photo(JPEG)
    p = _payload(cache)
    p["complainant"]["photo_id"] = kept
    F.register(p, now=NOW)
    old = os.path.getmtime(F.photo_path(stale)) - F.PHOTO_ORPHAN_S - 60
    for pid in (kept, stale):
        os.utime(F.photo_path(pid), (old, old))
    assert F._sweep_orphans() == 1
    assert os.path.exists(F.photo_path(kept)) and not os.path.exists(F.photo_path(stale))


def test_photo_routes_are_gated_and_serve_only_registered_photos(cache):
    client = main.app.test_client()
    perms = {"register_fir", "view_cases"}
    F.configure(lambda: cache, on_registered=cache["_seen"].append,
                identity=lambda: {"user": "t", "role": "sho", "permissions": list(perms), "scope": {"level": "state"}})

    r = client.post("/serverless/api/fir/photo", data={"photo": (io.BytesIO(JPEG), "p.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()["photo_id"]
    assert F._PHOTO_ID.match(pid)

    bad = client.post("/serverless/api/fir/photo", data={"photo": (io.BytesIO(b"\x89PNG" + b"0" * 200), "p.png")},
                      content_type="multipart/form-data")
    assert bad.status_code == 415

    # Uploaded but not on any FIR: not a record, so not served.
    assert client.get(f"/serverless/api/fir/photo/{pid}").status_code == 404
    p = _payload(cache)
    p["complainant"]["photo_id"] = pid
    F.register(p, now=NOW)
    ok = client.get(f"/serverless/api/fir/photo/{pid}")
    assert ok.status_code == 200 and ok.mimetype == "image/jpeg"
    assert "no-store" in ok.headers["Cache-Control"] and b"TestCam" not in ok.data
    # Traversal never reaches a file: Flask normalises the path away from this route.
    assert client.get("/serverless/api/fir/photo/../../main.py").status_code >= 400
    assert client.get("/serverless/api/fir/photo/registered_firs").status_code == 404

    perms.discard("view_cases")
    assert client.get(f"/serverless/api/fir/photo/{pid}").status_code == 403
    perms.discard("register_fir")
    r = client.post("/serverless/api/fir/photo", data={"photo": (io.BytesIO(JPEG), "p.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 403
