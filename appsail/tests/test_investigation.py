"""Investigation workbench: every finding is arithmetic over records and cites them.

* **Statutes.** The IPC → BNS rows are the CAPT Bhopal correspondence table's, and the
  code in force turns on the date of the offence (1 July 2024).
* **Integrity.** Each check's count is reproduced independently from the raw tables,
  and every flag cites the FIR it sits on.
* **Hypotheses.** Ranked by Heuer's rule — fewest inconsistencies, then most
  consistencies — and demographics never enter.
* **Series.** Every series spans two or more stations with three or more FIRs; an MO
  series shares its crime type; an offender series names its person on every FIR.
* **Geographic profile.** Recovers a planted anchor; refuses sites that are not one
  hunting area.
"""
import inspect
import math
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
import investigation as I  # noqa: E402
import main  # noqa: E402
import statutes  # noqa: E402

ON = date(2026, 10, 21)
API = "/serverless/api"


@pytest.fixture(scope="module")
def ix():
    main.load_and_build_intelligence()
    return I.index()


def _hdr(role):
    return {"Authorization": "Bearer " + auth.make_token("test-" + role, role)}


# ------------------------------------------------------------------ statutes
@pytest.mark.parametrize("ipc,bns", [("34", "3(5)"), ("302", "103(1)"), ("304", "105"), ("323", "115(2)"),
                                     ("356", "134"), ("379", "303(2)"), ("380", "305(a)"), ("384", "308(2)"),
                                     ("386", "308(5)"), ("420", "318(4)"), ("457", "331(4)")])
def test_crosswalk_rows_are_the_correspondence_tables(ipc, bns):
    assert statutes.lookup("IPC", ipc)["bns"] == bns
    assert statutes.lookup("BNS", bns)["ipc"] == ipc


def test_every_ipc_section_in_the_master_has_a_crosswalk(ix):
    sec = main.data_cache["Section"]
    for s in sec[sec["ActCode"] == "IPC"]["SectionCode"].astype(str):
        assert statutes.lookup("IPC", s), s


def test_the_code_in_force_turns_on_the_date_of_the_offence():
    assert statutes.code_for("2024-06-30 23:59") == "IPC"
    assert statutes.code_for("2024-07-01 00:00") == "BNS"
    after = statutes.check([("IPC", "379"), ("NDPS", "20(b)")], "2025-01-05")
    assert [f["code"] for f in after] == ["repealed_code"] and after[0]["suggestion"] == {"act": "BNS", "section": "303(2)"}
    before = statutes.check([("BNS", "304(2)")], "2023-03-01")
    assert before[0]["code"] == "bns_before_commencement" and before[0]["suggestion"] is None   # snatching is new
    assert statutes.check([("IPC", "302")], "2023-03-01") == []
    assert statutes.check([("BNS", "103(1)")], "2025-03-01") == []


# ------------------------------------------------------------------ integrity
def test_counts_match_an_independent_recount(ix):
    flags = I.integrity_flags(ix, on=ON)
    count = lambda code: sum(1 for fl in flags.values() for f in fl if f["code"] == code)  # noqa: E731
    cm = main.data_cache["CaseMaster"].copy()
    inc = pd.to_datetime(cm["IncidentFromDate"], errors="coerce")
    assert count("future_incident") == int((inc.dt.normalize() > pd.Timestamp(ON)).sum())
    cs_cases = set(main.data_cache["ChargesheetDetails"]["CaseMasterID"])
    assert count("chargesheet_status_lag") == int(((cm["CaseStatusID"] == 1) & cm["CaseMasterID"].isin(cs_cases)).sum())
    assert count("status_without_chargesheet") == int(((cm["CaseStatusID"] == 2) & ~cm["CaseMasterID"].isin(cs_cases)).sum())
    asa = main.data_cache["ActSectionAssociation"].merge(cm[["CaseMasterID"]].assign(inc=inc), on="CaseMasterID")
    assert count("repealed_code") == int(((asa["ActID"] == "IPC") & (asa["inc"] >= "2024-07-01")).sum())


def test_every_flag_cites_its_own_fir(ix):
    flags = I.integrity_flags(ix, on=ON)
    cm = ix["cm"]
    assert flags
    for cmid, fl in list(flags.items())[:3000]:
        for f in fl:
            assert f["code"] in I.CHECKS and f["question"] and f["cites"]
            assert any(c["kind"] == "fir" and c["id"] == cm.loc[cmid, "case_id"] for c in f["cites"]), f


def test_a_vehicle_flag_names_a_vehicle_stolen_after_the_incident(ix):
    flags = I.integrity_flags(ix, on=ON)
    veh = main.data_cache["vehicles"].set_index("vehicle_number")
    hits = [(cmid, f) for cmid, fl in flags.items() for f in fl if f["code"] == "vehicle_before_theft"]
    assert hits
    for cmid, f in hits[:50]:
        v = next(c["id"] for c in f["cites"] if c["kind"] == "vehicle")
        theft = pd.to_datetime(veh.loc[v, "theft_date"] if not isinstance(veh.loc[v], pd.DataFrame) else veh.loc[v].iloc[0]["theft_date"])
        assert theft > ix["cm"].loc[cmid, "IncidentFromDate"].normalize()


def test_a_shared_fingerprint_is_reported_on_both_identities(ix):
    fp, ids = next(iter(ix["shared_fp"].items()))
    for pid in ids:
        t = I.person_timeline(ix, pid)
        conflict = next(x for x in t["identity_conflicts"] if x["code"] == "shared_fingerprint")
        assert {c["id"] for c in conflict["cites"]} == set(ids) - {pid} and fp in conflict["detail"]


def test_the_summary_adds_up(ix):
    s = I.integrity_summary(ix, on=ON, limit=10)
    assert s["firs_flagged"] == len(I.integrity_flags(ix, on=ON))
    assert sum(c["count"] for c in s["checks"]) == sum(d["total"] for d in s["by_district"])
    assert len(s["cases"]) == 10 and s["cases"][0]["flags"]


# ------------------------------------------------------------------ hypotheses
def test_hypotheses_rank_by_heuer_and_mark_the_named(ix):
    cid = main.data_cache["firs"]["case_id"].iloc[100]
    v = I.case_view(ix, cid)
    hyp = v["hypotheses"]["hypotheses"]
    named = {main.data_cache["Accused"].set_index("CaseMasterID").loc[[ix["by_case_id"][cid]], "PersonID"].astype(str).tolist()[0]}
    assert {h["id"] for h in hyp if h["named"]} >= named
    keys = [(h["inconsistent"], -h["consistent"]) for h in hyp]
    assert keys == sorted(keys)
    for h in hyp:
        assert h["inconsistent"] == sum(c["verdict"] == "I" for c in h["cells"].values())
        for c in h["cells"].values():
            assert c["verdict"] in ("C", "I", "N")
            if c["verdict"] != "N":
                assert c["cites"], c


def test_demographics_never_enter():
    src = inspect.getsource(I)
    for col in ("ReligionID", "CasteID", "religion_name", "caste_name", "GenderID", "OccupationID", "EmployeeDOB", "BloodGroupID"):
        assert col not in src, col
    assert set(I.EXCLUDED_FACTORS) >= {"religion", "caste", "gender", "age", "occupation"}


# ------------------------------------------------------------------ series
def test_series_hold_their_invariants(ix):
    si = I.series_index(ix)["series"]
    assert si
    A = ix["A"]
    for s in si.values():
        sub = ix["cm"].loc[s["members"]]
        assert len(sub) >= I.SERIES_MIN_CASES and sub["police_station"].nunique() >= 2
        assert sub["crime_type"].nunique() == 1
        if s["kind"] == "offender":
            for m in s["members"]:
                assert s["person"]["id"] in {A["PersonID"].iloc[i] for i in ix["accused_by_case"][m]}


def test_mo_series_members_chain_within_the_window(ix):
    si = I.series_index(ix)["series"]
    mo = [s for s in si.values() if s["kind"] == "mo"]
    assert mo
    for s in mo[:20]:
        sub = ix["cm"].loc[s["members"]].sort_values("IncidentFromDate")
        for a in sub.itertuples():
            ok = False
            for b in sub.itertuples():
                if a.CaseMasterID == b.CaseMasterID:
                    continue
                same = sum(getattr(a, f) == getattr(b, f) for f in I.MO_FIELDS)
                days = abs((a.IncidentFromDate - b.IncidentFromDate).days)
                km = float(I._km(a.latitude, a.longitude, b.latitude, b.longitude))
                ok |= same >= I.SERIES_MIN_MO_MATCH and days <= I.SERIES_WINDOW_DAYS and km <= I.SERIES_RADIUS_KM
            assert ok, (s["id"], a.case_id)


# ------------------------------------------------------------------ geographic profile
def test_rossmo_recovers_a_planted_anchor():
    rng = np.random.default_rng(7)
    home = (12.97, 77.59)
    pts = []
    for k in range(14):        # sites 2-6 km from home in every direction: the buffer the method expects
        a, r = rng.uniform(0, 2 * math.pi), rng.uniform(2, 6)
        pts.append({"lat": home[0] + r * math.sin(a) / 110.57, "lng": home[1] + r * math.cos(a) / (111.32 * math.cos(math.radians(home[0]))),
                    "case_id": f"C{k}"})
    g = I.geoprofile(pts)
    assert g["applicable"] and len(g["sites_used"]) == 14
    assert float(I._km(g["peak"]["lat"], g["peak"]["lng"], *home)) < 3.0
    assert 0 < g["search_area_km2"]["50"] < g["search_area_km2"]["80"] < g["search_area_km2"]["profiled"]


def test_rossmo_refuses_sites_that_are_not_one_hunting_area():
    far = [{"lat": 12.9, "lng": 77.6, "case_id": "a"}, {"lat": 15.4, "lng": 75.1, "case_id": "b"},
           {"lat": 17.3, "lng": 76.8, "case_id": "c"}, {"lat": 12.3, "lng": 76.6, "case_id": "d"}]
    assert I.geoprofile(far)["applicable"] is False
    assert I.geoprofile(far[:2])["applicable"] is False


# ------------------------------------------------------------------ under-reporting, IO
def test_underreporting_flags_follow_their_rules(ix):
    u = I.underreporting(main.data_cache, ix)
    assert len(u["districts"]) == 31 and u["model"]
    for d in u["districts"]:
        expect = (d["z"] is not None and d["z"] <= -1.5) + bool(d["calls"] and d["calls"]["ci"][1] < u["state_cognizable_fir_rate"])
        assert len(d["flags"]) == expect


def test_io_recommendation_prefers_the_specialist_and_explains(ix):
    st = main.data_cache["Unit"].iloc[0]
    r = I.recommend_io(main.data_cache, ix, int(st["UnitID"]), "Murder")
    top = r["officers"][0]
    assert top["specialization"] == "Homicide" and top["reasons"]
    assert r["officers"] == sorted(r["officers"], key=lambda o: (-o["score"], o["open_cases"], o["officer_id"]))
    assert "gender" in r["excluded_factors"]


# ------------------------------------------------------------------ freshness and routes
def test_the_index_rebuilds_when_a_frame_is_replaced(ix):
    c = main.data_cache
    old = c["CaseMaster"]
    try:
        c["CaseMaster"] = old.iloc[:-1].copy()
        assert I.index() is not ix and len(I.index()["cm"]) == len(old) - 1
    finally:
        c["CaseMaster"] = old
    assert len(I.index()["cm"]) == len(old)


def test_routes_are_gated():
    client = main.app.test_client()
    cid = main.data_cache["firs"]["case_id"].iloc[0]
    assert client.get(f"{API}/investigate/case/{cid}").status_code == 403
    ok = client.get(f"{API}/investigate/case/{cid}", headers=_hdr("investigator"))
    assert ok.status_code == 200 and ok.get_json()["case_id"] == cid
    assert client.get(f"{API}/investigate/case/000", headers=_hdr("investigator")).status_code == 404
    assert client.get(f"{API}/investigate/underreporting").status_code == 200
    assert client.get(f"{API}/statutes/crosswalk?act=IPC&section=379&date=2025-01-01").get_json()["row"]["bns"] == "303(2)"
    assert client.get(f"{API}/investigate/person/SUS-1001/timeline").status_code == 403
    assert client.get(f"{API}/investigate/person/SUS-1001/timeline", headers=_hdr("investigator")).status_code == 200
    s = client.get(f"{API}/investigate/series?kind=mo&limit=2", headers=_hdr("investigator")).get_json()
    assert s["series"] and client.get(f"{API}/investigate/series/{s['series'][0]['id']}",
                                      headers=_hdr("investigator")).get_json()["members"]


# ------------------------------------------------------------------ the floating assistant's chats reach the Copilot page
def test_a_console_chat_is_saved_where_the_copilot_page_lists_it(monkeypatch):
    monkeypatch.setattr(main, "llm_available", lambda: False)
    client = main.app.test_client()
    h = _hdr("investigator")
    first = client.post(f"{API}/agent", json={"query": "how many murders in Mysuru in 2024?", "page": "/map"}, headers=h)
    cid = first.get_json()["conversation_id"]
    second = client.post(f"{API}/agent", json={"query": "and in 2023?", "page": "/map", "conversation_id": cid,
                                               "history": [{"role": "user", "content": "how many murders in Mysuru in 2024?"}]},
                         headers=h)
    assert second.get_json()["conversation_id"] == cid
    listed = client.get(f"{API}/copilot/conversations", headers=h).get_json()["conversations"]
    mine = next(c for c in listed if c["id"] == cid)
    assert mine["origin"] == "/map" and mine["messages"] == 4
    conv = client.get(f"{API}/copilot/conversations/{cid}", headers=h).get_json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant", "user", "assistant"]
    assert conv["messages"][2]["content"] == "and in 2023?"
    # Opting out, and an unknown id, behave
    assert "conversation_id" not in client.post(f"{API}/agent", json={"query": "hello", "save": False}, headers=h).get_json()
    fresh = client.post(f"{API}/agent", json={"query": "hello", "conversation_id": "nope"}, headers=h).get_json()
    assert fresh["conversation_id"] != cid

