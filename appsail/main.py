import sys
import os

# Ensure _vendor directory is in sys.path before any third-party imports
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_VENDOR_DIR = os.path.join(_CURRENT_DIR, '_vendor')
if os.path.exists(_VENDOR_DIR):
    if sys.platform != 'win32':
        if _VENDOR_DIR not in sys.path:
            sys.path.insert(0, _VENDOR_DIR)
    else:
        if _VENDOR_DIR not in sys.path:
            sys.path.append(_VENDOR_DIR)

import math
import re
import json
import threading
import time
from flask import Flask, jsonify, request
import pandas as pd
import numpy as np
import networkx as nx
import audit
import agent_answers
import copilot_engine
import surveillance
import fir_registry
import investigation
import insights
import network_intel
import news
import streams
import temporal
import trend_forecast
import conversations
import copilot_knowledge
_PROCESS_STARTED = time.time()
import auth
import grounding
import observability
import privacy      # role-based access control for person-level data
import analytics  # advanced ML layer (forecasting, clustering, link-prediction, NLP, risk)
# Heavy / optional libraries (scikit-learn, openai) are imported LAZILY inside the functions
# that use them. This keeps the gunicorn worker boot fast so it binds to the port and serves
# '/' immediately on a cold Catalyst container — instead of timing out (503) while importing
# scipy/sklearn, or crash-looping if an optional dep is missing from _vendor.

app = Flask(__name__)
LOG = observability.configure()


def _load_local_env():
    """On Catalyst, app-config.json env_variables are injected automatically. For a local
    `python main.py` run they are NOT — so the LLM key would be missing and the agent would
    fall back to 'could not act'. Populate any missing vars from app-config.json here."""
    try:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app-config.json')
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        for k, v in (cfg.get('env_variables') or {}).items():
            if v and not os.environ.get(k):
                os.environ[k] = v
    except Exception as e:
        LOG.warning(f"Local env load skipped: {e}")


_load_local_env()

# NOTE: Do NOT add CORS headers here. The Catalyst AppSail gateway already injects
# a complete CORS response (reflected Origin + Allow-Credentials/Methods/Headers).
# Adding our own would produce duplicate Access-Control-Allow-Origin headers, which
# browsers reject — blocking every fetch from the frontend.

@app.route('/', defaults={'path': ''}, methods=['GET', 'OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    if request.method == 'OPTIONS':
        return '', 204
    if path == '':
        return 'OK', 200

@app.before_request
def before_request_func():
    if request.method == 'OPTIONS':
        return
    if request.path == '/' or request.path == '/favicon.ico' or request.path == '/serverless/api/health':
        return
    check_ready()


# LOCAL DEV ONLY: when running outside Catalyst there is no gateway to inject CORS,
# so a browser on :3000 (next dev) cannot read responses from :9000. We add headers
# ONLY when X_ZOHO_CATALYST_LISTEN_PORT is unset — on Catalyst the gateway already
# sends Access-Control-Allow-Origin for the client domain (verified against the live
# service), and adding our own would produce a duplicate header, which browsers
# reject outright.
@app.after_request
def add_dev_cors(resp):
    if not os.environ.get('X_ZOHO_CATALYST_LISTEN_PORT'):
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        # Authorization was missing here, so any signed-in cross-origin request from
        # `next dev` failed preflight and never reached the server — every role-gated
        # route looked like a 403 for an authenticated developer.
        resp.headers['Access-Control-Allow-Headers'] = (
            'Content-Type, Authorization, X-Access-Reason')
    return resp


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0 # Earth radius in kilometers
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2) * math.sin(dLat/2) + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2) * math.sin(dLon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def haversine_vec(lat1, lon1, lat2, lon2):
    """Vectorized haversine distance (km) between one point and arrays of points."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dLat = lat2 - lat1
    dLon = lon2 - lon1
    a = np.sin(dLat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dLon / 2) ** 2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    return R * c

LOG.info("Initializing Deep Intelligence Core...")

# Global State
knowledge_graph = nx.Graph()
copilot_engine.configure(lambda: knowledge_graph)
copilot_knowledge.configure(app)
data_cache = {}
models = {}


def _on_fir_registered(bundle):
    """Publish a just-registered FIR to every derived view without a full reload.

    fir_registry has already appended the raw table rows; this builds the FIR-frame
    row with the loader's own function, extends links, reports and the graph, and
    drops the memoised views that are not keyed on the frame's identity.
    """
    cm = data_cache['CaseMaster']
    new = cm[cm['CaseMasterID'] == bundle['case_master_id']]
    records, _ = _firs_records(new)
    data_cache['firs'] = pd.concat([data_cache['firs'], pd.DataFrame(records)], ignore_index=True)
    if bundle.get('links'):
        data_cache['links'] = pd.concat([data_cache['links'], pd.DataFrame(bundle['links'])], ignore_index=True)
    if bundle.get('reports'):
        data_cache['reports'] = pd.concat([data_cache['reports'], pd.DataFrame(bundle['reports'])], ignore_index=True)
    for r in records:
        knowledge_graph.add_node(r['case_id'], type='Case', crime_type=r['crime_type'], district=r['district'])
    for l in bundle.get('links', []):
        if knowledge_graph.has_node(l['suspect_id']):
            knowledge_graph.add_edge(l['case_id'], l['suspect_id'], relationship=l['relationship'])
    for key in ('_map_incidents', '_grounding_sets', 'suspect_case_meta'):
        data_cache.pop(key, None)
    LOG.info("FIR %s registered and published", bundle['crime_no'])


fir_registry.configure(lambda: data_cache, _on_fir_registered, auth.current_identity)
investigation.configure(lambda: data_cache, suggest=lambda case_id, k: analytics.recommend_suspects_for_case(
    case_id, data_cache['firs'], data_cache.get('suspects'), data_cache.get('Accused'), data_cache, k=k))
insights.configure(lambda: data_cache)
news.configure(lambda: data_cache)
network_intel.configure(lambda: knowledge_graph, lambda: data_cache)
temporal.configure(lambda: data_cache)
trend_forecast.configure(lambda: data_cache)
streams.configure(lambda: {c["camera_id"]: c.get("district") for c in surveillance.ensure(data_cache)["cameras"]}
                  if data_cache.get('_load_complete') else set())
for _bp in (fir_registry.bp, investigation.bp, insights.bp, network_intel.bp, news.bp, streams.bp, temporal.bp, trend_forecast.bp):
    app.register_blueprint(_bp)

import pandas as pd

# Removed get_data_dir to prevent os.walk issues


# --- Dispatch scoring convention -------------------------------------------------
# Declared here rather than buried as literals inside the loop, so they can be cited,
# argued with, and changed in one place. None of these are learned parameters.
DISPATCH_WEIGHTS = {
    'open_cases': 0.40,
    'recent_severity': 0.30,
    'high_severity': 0.15,
    'spike': 0.15,
}
# Staffing is expressed as an APPORTIONMENT of a stated deployable pool, not as an
# unbounded per-district sum. The previous form was
#   12 + risk/100 * 48 + open_cases * 0.08
# which was tuned against a 5,000-case corpus. On the current 18,000-case corpus the
# caseload term dominates: Bengaluru Urban scored 250 officers, 197 of them from raw
# open-case count alone, swamping the risk signal it was supposed to modulate. An
# absolute headcount also implies knowledge this system does not have — actual KSP
# establishment strength. Apportioning a declared pool by relative risk is a claim it
# can actually support.
STAFFING = {
    'state_deployable_officers': 1200,  # notional pool being apportioned; a parameter
    'min_per_district': 8,              # floor so no district is left unstaffed
}
RISK_BANDS = {'Critical': 66, 'High': 40, 'Moderate': 20}


# Every path into the loader serialises here. Previously only `check_ready` held a
# lock, so `/sync`, the warm-up thread and any direct call could run concurrently.
# Re-entrant so the double-checked path in `check_ready` does not deadlock.
_load_lock = threading.RLock()


def _firs_records(df_cm):
    """CaseMaster rows -> FIR-frame records, with every master lookup resolved.

    One function for the boot-time load and for a newly registered FIR, so a case
    registered through the platform is shaped exactly like one loaded from disk.
    Returns (records, unparseable_timestamp_count).
    """
    unit_map = dict(zip(data_cache['Unit']['UnitID'], data_cache['Unit']['UnitName']))
    dist_map = dict(zip(data_cache['District']['DistrictID'], data_cache['District']['DistrictName']))
    unit_to_dist_id = dict(zip(data_cache['Unit']['UnitID'], data_cache['Unit']['DistrictID']))
    subhead_map = dict(zip(data_cache['CrimeSubHead']['CrimeSubHeadID'], data_cache['CrimeSubHead']['CrimeHeadName']))
    status_map = dict(zip(data_cache['CaseStatusMaster']['CaseStatusID'], data_cache['CaseStatusMaster']['CaseStatusName']))

    # Pre-aggregate victims
    df_vict = data_cache['Victim']
    vict_first = df_vict.groupby("CaseMasterID").first()
    vict_name_map = vict_first["VictimName"].to_dict()
    vict_age_map = vict_first["AgeYear"].to_dict()
    vict_gender_map = vict_first["GenderID"].map({1: "M", 2: "F", 3: "T"}).to_dict()
    vict_police_map = vict_first["VictimPolice"].astype(str).to_dict()

    # Pre-aggregate ActSectionAssociation
    df_asa = data_cache['ActSectionAssociation']
    df_asa['ActSection'] = df_asa['ActID'].astype(str) + " " + df_asa['SectionID'].astype(str)
    asa_map = df_asa.groupby("CaseMasterID")['ActSection'].apply(lambda x: " · ".join(x)).to_dict()

    # Accused counts
    df_acc = data_cache['Accused']
    acc_counts = df_acc.groupby("CaseMasterID").size().to_dict()

    # Map EmployeeID to KGID
    df_emp = data_cache['Employee']
    emp_kgid_map = dict(zip(df_emp['EmployeeID'], df_emp['KGID']))

    import re
    metadata_pat = re.compile(r"\[METADATA: SEV=(\d+);WPN=([^;]+);VEH=([^;]+);TGT=([^;]+);FAT=(\d+);INJ=(\d+);WIT=(\d+)\]")

    firs_records = []
    _bad_timestamps = 0
    for row in df_cm.itertuples():
        cm_id = row.CaseMasterID
        u_id = row.PoliceStationID
        d_id = unit_to_dist_id.get(u_id, 1)
        dist_name = dist_map.get(d_id, "Unknown")
        ps_name = unit_map.get(u_id, "Unknown PS")
        crime_type = subhead_map.get(row.CrimeMinorHeadID, "Unknown")
        status_name = status_map.get(row.CaseStatusID, "Under Investigation")
        emp_id = row.PolicePersonID
        off_kgid = emp_kgid_map.get(emp_id, f"OFF-{emp_id}")

        # Parse BriefFacts metadata
        facts = str(row.BriefFacts)
        m = metadata_pat.search(facts)
        if m:
            sev = int(m.group(1))
            weapon = m.group(2)
            vehicle = m.group(3)
            target = m.group(4)
            fatal = int(m.group(5))
            injured = int(m.group(6))
            wit = int(m.group(7))
        else:
            sev = 50
            weapon = "None"
            vehicle = "None"
            target = "Unknown"
            fatal = 0
            injured = 0
            wit = 0

        # Time of day calculation
        ts_str = str(row.IncidentFromDate)
        try:
            hour = int(ts_str.split()[1].split(":")[0])
            if hour >= 23 or hour < 5:
                tod = "Late Night (1AM-4AM)"
            elif hour >= 5 and hour < 12:
                tod = "Morning (7AM-10AM)"
            elif hour >= 12 and hour < 17:
                tod = "Afternoon (1PM-4PM)"
            else:
                tod = "Evening (5PM-8PM)"
        except Exception:
            tod = "Morning (7AM-10AM)"
            _bad_timestamps += 1

        firs_records.append({
            "case_id": str(row.CrimeNo),
            "district": dist_name,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "crime_type": crime_type,
            "timestamp": row.IncidentFromDate,
            "severity_score": sev,
            "status": status_name,
            "time_of_day": tod,
            "target_type": target,
            "weapon_used": weapon,
            "vehicle_used": vehicle,
            "police_station": ps_name,
            "ipc_bns_sections": asa_map.get(cm_id, "IPC 34"),
            "victim_name": vict_name_map.get(cm_id, ""),
            "victim_age": vict_age_map.get(cm_id, np.nan),
            "victim_gender": vict_gender_map.get(cm_id, "M"),
            "victim_police": vict_police_map.get(cm_id, "0"),
            "officer_id": off_kgid,
            "witnesses": wit,
            "fatalities": fatal,
            "injured": injured,
            "accused_count": acc_counts.get(cm_id, 0),
            "CrimeNo": str(row.CrimeNo),
            "CaseNo": str(row.CaseNo),
            "CaseMasterID": cm_id
        })
    return firs_records, _bad_timestamps


def load_and_build_intelligence(force=False):
    global knowledge_graph

    with _load_lock:
        return _load_and_build_locked(force)


def _load_and_build_locked(force=False):
    global knowledge_graph

    # A caller that queued behind a load which has just completed does not need to
    # repeat it. `/sync` passes force=True to rebuild deliberately.
    if not force and data_cache.get('_load_complete'):
        LOG.info("Corpus already loaded; skipping duplicate build.")
        return True

    from sklearn.neighbors import KernelDensity
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.ensemble import IsolationForest
    import traceback

    LOG.info("Loading 26-table relational schema and registries...")
    try:
        # Load the 26 tables
        tables = [
            'State', 'District', 'UnitType', 'Unit', 'Rank', 'Designation', 'Employee',
            'CaseCategory', 'GravityOffence', 'CaseStatusMaster', 'CasteMaster',
            'ReligionMaster', 'OccupationMaster', 'Court', 'CrimeHead', 'CrimeSubHead',
            'Act', 'Section', 'CrimeHeadActSection', 'CaseMaster', 'ComplainantDetails',
            'ActSectionAssociation', 'Victim', 'Accused', 'ArrestSurrender', 'ChargesheetDetails'
        ]
        # One read of the corpus, shared. `dataset.load_canonical` is the same
        # adapter the ML layer uses, so handing it the bundle below means the 26
        # tables are parsed once per process instead of twice. The second copy used
        # to be pinned in data_cache['ml_bundle'] for the process lifetime, in a
        # 512 MB container already measuring 444 MB at rest.
        from ml import dataset as ml_dataset
        ml_bundle = ml_dataset.load_canonical()
        raw = ml_bundle['raw']

        for t in tables:
            data_cache[t] = raw[t]

        # FIRs registered through the platform live outside data/ (see fir_registry)
        # and are replayed onto the tables here, before anything is derived from them.
        _registered = fir_registry.stored_bundles()
        for bundle in _registered:
            fir_registry.append_rows(data_cache, bundle)
        if _registered:
            LOG.info("Replayed %d registered FIR(s) onto the corpus", len(_registered))

        # Registries
        df_crim_reg = raw['CriminalsRegistry']
        df_off_reg = raw['OfficersRegistry']
        df_cc_reg = raw['CourtCasesRegistry']

        data_cache['CriminalsRegistry'] = df_crim_reg
        data_cache['CourtCasesRegistry'] = df_cc_reg

        LOG.info("Reconstructing standard DataFrames from 26-table relational schema...")

        # 1. Reconstruct df_links
        df_links = ml_bundle['links'].copy()
        _reg_links = [l for b in _registered for l in b.get('links', [])]
        if _reg_links:
            df_links = pd.concat([df_links, pd.DataFrame(_reg_links)], ignore_index=True)
        df_links["case_id"] = df_links["case_id"].astype(str)
        df_links["suspect_id"] = df_links["suspect_id"].astype(str)

        # 2. Reconstruct df_reports
        df_reports = ml_bundle['reports'].copy()
        _reg_reports = [r for b in _registered for r in b.get('reports', [])]
        if _reg_reports:
            df_reports = pd.concat([df_reports, pd.DataFrame(_reg_reports)], ignore_index=True)
        df_reports["case_id"] = df_reports["case_id"].astype(str)

        # 3. Reconstruct df_suspects
        df_suspects = raw['suspects'].copy()
        df_suspects["suspect_id"] = df_suspects["suspect_id"].astype(str)
        
        # 4. Reconstruct df_firs
        df_cm = data_cache['CaseMaster']
        unit_map = dict(zip(data_cache['Unit']['UnitID'], data_cache['Unit']['UnitName']))
        dist_map = dict(zip(data_cache['District']['DistrictID'], data_cache['District']['DistrictName']))
        unit_to_dist_id = dict(zip(data_cache['Unit']['UnitID'], data_cache['Unit']['DistrictID']))
        df_emp = data_cache['Employee']
        firs_records, _bad_timestamps = _firs_records(df_cm)
        df_firs = pd.DataFrame(firs_records)
        if _bad_timestamps:
            LOG.warning("%d of %d incidents had an unparseable timestamp and were "
                        "banded as Morning; time-of-day analysis is affected",
                        _bad_timestamps, len(firs_records))
        
        # 5. Reconstruct officers
        rank_name_map = dict(zip(data_cache['Rank']['RankID'], data_cache['Rank']['RankName']))
        off_reg_map = df_off_reg.set_index("EmployeeID").to_dict("index")
        
        officers_records = []
        for row in df_emp.itertuples():
            emp_id = row.EmployeeID
            u_id = row.UnitID
            st_name = unit_map.get(u_id, "Unknown PS")
            d_id = unit_to_dist_id.get(u_id, 1)
            d_name = dist_map.get(d_id, "Unknown")
            rk_name = rank_name_map.get(row.RankID, "PC")
            
            reg = off_reg_map.get(emp_id, {})
            spec = reg.get("specialization", "General")
            cases_assigned = reg.get("cases_assigned", 0)
            closure_rate = reg.get("closure_rate", 0.0)
            comm = reg.get("commendations", 0)
            
            officers_records.append({
                "officer_id": row.KGID,
                "name": row.FirstName,
                "rank": rk_name,
                "station": st_name,
                "district": d_name,
                "specialization": spec,
                "cases_assigned": cases_assigned,
                "closure_rate": closure_rate,
                "commendations": comm,
                "dob": row.EmployeeDOB,
                "appointment_date": row.AppointmentDate,
                "blood_group": row.BloodGroupID,
                "gender": "Male" if row.GenderID == 1 else "Female",
                "physically_challenged": bool(row.PhysicallyChallenged),
                "employee_id": emp_id
            })
        df_officers = pd.DataFrame(officers_records)
        data_cache['officers'] = df_officers
        
        # 6. Reconstruct criminals
        df_criminals = df_crim_reg.rename(columns={"PersonID": "criminal_id"})
        data_cache['criminals'] = df_criminals
        
        # 7. Reconstruct court_cases
        cm_to_crimeno = dict(zip(df_cm["CaseMasterID"], df_cm["CrimeNo"].astype(str)))
        df_cc_reg["fir_number"] = df_cc_reg["CaseMasterID"].map(cm_to_crimeno).astype(str)
        df_court_cases = df_cc_reg.drop(columns=["CaseMasterID"])
        data_cache['courts'] = df_court_cases
        
    except Exception as e:
        LOG.warning(f"Failed to load or reconstruct relational tables: {e}")
        traceback.print_exc()
        return False

    data_cache['firs'] = df_firs
    data_cache['suspects'] = df_suspects
    data_cache['reports'] = df_reports
    data_cache['links'] = df_links

    # Map FIR-style case IDs in child registries to 18-digit CrimeNo
    def map_fir_to_crimeno(val):
        if pd.isna(val) or not val:
            return ""
        val_str = str(val).strip()
        if val_str.startswith("FIR-"):
            try:
                num = int(val_str.split("-")[1])
                return cm_to_crimeno.get(num, val_str)
            except Exception as e:
                LOG.debug("FIR id %s did not map to a CrimeNo (%s)", val_str, e)
        return val_str

    # CCTNS-style extended registries (optional — absent files degrade gracefully).
    for key, fname in [
        ('vehicles', 'stolen_vehicles.csv'), ('cctv', 'cctv.csv'),
        ('complaints', 'complaints.csv'), ('cybercrime', 'cybercrime.csv'),
        ('missing', 'missing_persons.csv'),
        ('socio', 'socioeconomic.csv'),
    ]:
        try:
            # Already parsed by load_canonical; fall back to disk only if absent.
            df_reg = raw.get(key)
            df_reg = df_reg.copy() if df_reg is not None and not df_reg.empty \
                else pd.read_csv(f"data/{fname}")
            # Map old FIR format to CrimeNo if applicable
            col_to_map = None
            if 'linked_case_id' in df_reg.columns:
                col_to_map = 'linked_case_id'
            elif 'case_link' in df_reg.columns:
                col_to_map = 'case_link'
            elif 'case_id' in df_reg.columns:
                col_to_map = 'case_id'
                
            if col_to_map:
                df_reg[col_to_map] = df_reg[col_to_map].apply(map_fir_to_crimeno)
            
            data_cache[key] = df_reg
        except Exception as e:
            LOG.warning(f"Optional dataset {fname} not loaded: {e}")
            data_cache[key] = pd.DataFrame()
    
    LOG.info("Building Knowledge Graph (NetworkX)...")
    # Built off to the side and published at the end. Clearing the live graph in
    # place let concurrent readers observe it empty or half-built.
    new_graph = nx.Graph()
    
    # Add Nodes
    for row in df_firs.itertuples():
        new_graph.add_node(row.case_id, type='Case', crime_type=row.crime_type, district=row.district)
        
    for row in df_suspects.itertuples():
        # Suspect dataframe might not have risk_level or gang_affiliation in every config, use getattr with defaults if needed
        risk = getattr(row, 'risk_level', 'Unknown')
        gang = getattr(row, 'gang_affiliation', 'Unknown')
        name = getattr(row, 'name', 'Unknown')
        new_graph.add_node(row.suspect_id, type='Suspect', name=name, gang=gang, risk_level=risk)
        
    # Add Edges
    for row in df_links.itertuples():
        new_graph.add_edge(row.case_id, row.suspect_id, relationship=row.relationship)
        
    LOG.info(f"Graph built: {new_graph.number_of_nodes()} nodes, "
             f"{new_graph.number_of_edges()} edges (not yet published).")
    
    LOG.info("Calculating Network Centrality (Key Players)...")
    centrality = nx.degree_centrality(new_graph)
    # Store top 10 most connected suspects
    suspect_centrality = {n: c for n, c in centrality.items() if str(n).startswith('SUS-')}
    top_suspects = sorted(suspect_centrality.items(), key=lambda x: x[1], reverse=True)[:10]
    data_cache['top_suspects'] = top_suspects

    build_extended_graph_and_analytics(new_graph)

    # Publish. Readers held the previous complete graph until this line.
    knowledge_graph = new_graph

    LOG.info("Initializing NLP Engine (TF-IDF MO Matching)...")
    vectorizer = TfidfVectorizer(stop_words='english')
    # Fit on case reports
    tfidf_matrix = vectorizer.fit_transform(df_reports['report_text'].fillna(''))
    models['tfidf_vectorizer'] = vectorizer
    models['tfidf_matrix'] = tfidf_matrix
    models['report_ids'] = df_reports['case_id'].tolist()
    
    LOG.info("Running Spatial KDE (Predictive Policing)...")
    # Kernel Density Estimation on high severity coordinates
    high_sev = df_firs[df_firs['severity_score'] > 80]
    data_cache['predicted_hotspots'] = []
    if not high_sev.empty:
        # Fit KDE on lat/lng
        kde = KernelDensity(bandwidth=0.02, metric='haversine')
        # Convert lat/lng to radians for haversine
        coords = np.radians(high_sev[['latitude', 'longitude']].values)
        kde.fit(coords)
        models['kde_hotspots'] = kde

        # Score a grid covering the case extent and pick the highest-density,
        # spatially-separated cells as the actual predicted hotspots.
        lat_min, lat_max = df_firs['latitude'].min(), df_firs['latitude'].max()
        lon_min, lon_max = df_firs['longitude'].min(), df_firs['longitude'].max()
        grid_lat, grid_lon = np.meshgrid(
            np.linspace(lat_min, lat_max, 40),
            np.linspace(lon_min, lon_max, 40)
        )
        grid_points = np.column_stack([grid_lat.ravel(), grid_lon.ravel()])
        density = np.exp(kde.score_samples(np.radians(grid_points)))
        max_density = density.max()

        # Greedy non-max suppression so the predicted zones aren't all on top of each other
        order = np.argsort(density)[::-1]
        selected = []
        min_separation_km = 8.0
        for idx in order:
            pt = grid_points[idx]
            if all(haversine(pt[0], pt[1], s[0], s[1]) > min_separation_km for s in selected):
                selected.append(pt)
            if len(selected) >= 6:
                break

        predicted = []
        for i, pt in enumerate(selected):
            d = np.exp(kde.score_samples(np.radians([pt]))[0])
            predicted.append({
                "id": f"pred-hotspot-{i}",
                "lat": float(pt[0]),
                "lng": float(pt[1]),
                "intensity": round(float(d / max_density), 3),
                "type": "Predicted Hotspot (KDE)"
            })
        data_cache['predicted_hotspots'] = predicted

    LOG.info("Detecting emerging crime trend alerts...")
    timestamp_dt = pd.to_datetime(df_firs['timestamp'])
    max_date = timestamp_dt.max()
    min_date = timestamp_dt.min()
    recent_cutoff = max_date - pd.Timedelta(days=30)
    recent_days = 30.0
    historical_days = max(1.0, (recent_cutoff - min_date).days)

    recent = df_firs[timestamp_dt > recent_cutoff]
    historical = df_firs[timestamp_dt <= recent_cutoff]

    recent_counts = recent.groupby(['district', 'crime_type']).size()
    historical_counts = historical.groupby(['district', 'crime_type']).size()

    alerts = []
    for (district, crime_type), recent_count in recent_counts.items():
        hist_count = historical_counts.get((district, crime_type), 0)
        expected = max(0.5, (hist_count / historical_days) * recent_days)
        spike_ratio = recent_count / expected
        if recent_count >= 3 and spike_ratio > 1.75:
            alerts.append({
                "district": district,
                "crime_type": crime_type,
                "recent_count": int(recent_count),
                "expected_count": round(expected, 1),
                "spike_ratio": round(spike_ratio, 2)
            })
    alerts = sorted(alerts, key=lambda x: x['spike_ratio'], reverse=True)[:5]
    data_cache['trend_alerts'] = alerts

    LOG.info("Running behavioral anomaly detection (Isolation Forest)...")
    data_cache['anomalies'] = []
    try:
        anom = df_firs.copy()
        anom['hour'] = timestamp_dt.dt.hour.fillna(0)
        # Frequency-encode the categoricals: rarer crime types / districts read as more unusual.
        crime_freq = anom['crime_type'].map(anom['crime_type'].value_counts(normalize=True))
        district_freq = anom['district'].map(anom['district'].value_counts(normalize=True))

        # Enriched features for multidimensional anomaly detection
        vict_age = anom['victim_age'].fillna(anom['victim_age'].mean()).to_numpy(dtype=float)
        vict_gender = anom['victim_gender'].map({'M': 0, 'F': 1}).fillna(0).to_numpy(dtype=float)
        accused_count = anom['accused_count'].fillna(0).to_numpy(dtype=float)
        section_count = anom['ipc_bns_sections'].str.count('·').fillna(0).to_numpy(dtype=float) + 1.0

        features = np.column_stack([
            anom['severity_score'].to_numpy(dtype=float),
            anom['hour'].to_numpy(dtype=float),
            anom['latitude'].to_numpy(dtype=float),
            anom['longitude'].to_numpy(dtype=float),
            crime_freq.to_numpy(dtype=float),
            district_freq.to_numpy(dtype=float),
            vict_age,
            vict_gender,
            accused_count,
            section_count
        ])

        iso = IsolationForest(n_estimators=150, contamination=0.03, random_state=42)
        labels = iso.fit_predict(features)
        anom['_anom_score'] = iso.score_samples(features)  # lower = more anomalous
        models['anomaly_model'] = iso

        sev_mean, sev_std = anom['severity_score'].mean(), anom['severity_score'].std() or 1.0
        flagged = anom[labels == -1].sort_values('_anom_score').head(8)

        means = np.mean(features, axis=0)
        stds = np.std(features, axis=0)
        stds[stds == 0] = 1.0
        row_indices = anom.index.tolist()

        anomalies = []
        for row_ix, row in flagged.iterrows():
            reasons = []
            idx_in_feats = row_indices.index(row_ix)
            v = features[idx_in_feats]
            z_scores = (v - means) / stds
            
            if z_scores[0] >= 1.5:
                reasons.append(f"Severity score is {z_scores[0]:.1f}σ above average")
            if abs(z_scores[1]) >= 1.5:
                reasons.append(f"Incident hour deviates by {z_scores[1]:.1f}σ from norm")
            if z_scores[4] <= -1.5:
                reasons.append(f"Rare crime category in this area ({z_scores[4]:.1f}σ frequency deviation)")
            if abs(z_scores[6]) >= 1.5:
                reasons.append(f"Victim age profile deviates by {abs(z_scores[6]):.1f}σ")
            if z_scores[8] >= 1.5:
                reasons.append(f"Co-accused count is {z_scores[8]:.1f}σ above average")
            if z_scores[9] >= 1.5:
                reasons.append(f"IPC/BNS sections count is {z_scores[9]:.1f}σ above average")
                
            if not reasons:
                if row['severity_score'] >= sev_mean + 1.2 * sev_std:
                    reasons.append("Severity deviates above district norm")
                if row['hour'] < 5 or row['hour'] >= 23:
                    reasons.append("Off-hours incident (late night/pre-dawn)")
                if crime_freq.loc[row.name] < 0.04:
                    reasons.append("Relatively rare crime type")
                if row['accused_count'] >= 3:
                    reasons.append("Multiple co-accused (organized profile)")
                if not reasons:
                    reasons.append("Multidimensional feature vector outlier")

            anomalies.append({
                "case_id": row['case_id'],
                "district": row['district'],
                "crime_type": row['crime_type'],
                "severity": int(row['severity_score']),
                "status": row['status'],
                "anomaly_score": round(float(-row['_anom_score']), 3),
                "reasons": reasons
            })
        data_cache['anomalies'] = anomalies
    except Exception as e:
        LOG.warning(f"Anomaly detection skipped: {e}")

    LOG.info("Computing district centroids & patrol dispatch recommendations...")
    try:
        # Per-district centroids — used to focus the map on "show heatmap of <area>".
        centroids = df_firs.groupby('district').agg(
            lat=('latitude', 'mean'), lng=('longitude', 'mean')
        ).to_dict('index')
        data_cache['district_centroids'] = {
            d: {"lat": float(v['lat']), "lng": float(v['lng'])} for d, v in centroids.items()
        }

        recent_mask = timestamp_dt > recent_cutoff
        spike_by_district = {}
        for a in data_cache.get('trend_alerts', []):
            spike_by_district[a['district']] = max(spike_by_district.get(a['district'], 0.0), a['spike_ratio'])

        rows = []
        for district, g in df_firs.groupby('district'):
            g_recent = g[recent_mask.loc[g.index]]
            rows.append({
                "district": district,
                "open_cases": int((g['status'] != 'Closed').sum()),
                "total_cases": int(len(g)),
                "recent_count": int(len(g_recent)),
                "recent_severity": float(g_recent['severity_score'].sum()),
                "avg_severity": round(float(g['severity_score'].mean()), 1),
                "high_severity_cases": int((g['severity_score'] > 80).sum()),
                "spike_ratio": round(float(spike_by_district.get(district, 0.0)), 2),
                "lat": data_cache['district_centroids'][district]['lat'],
                "lng": data_cache['district_centroids'][district]['lng'],
            })

        def _norm(vals):
            mx = max(vals) if vals else 0
            return [(v / mx) if mx else 0.0 for v in vals]

        open_n = _norm([r['open_cases'] for r in rows])
        recent_n = _norm([r['recent_severity'] for r in rows])
        high_n = _norm([r['high_severity_cases'] for r in rows])
        spike_n = _norm([min(r['spike_ratio'], 4.0) for r in rows])

        # Both numbers below are a stated convention, not a fitted model. They are
        # served WITH their arithmetic — the same standard /operational-index holds
        # itself to. A composite 0-100 risk number and a staffing recommendation are
        # the easiest artefacts in this domain to produce and the hardest to defend,
        # so the weights, the baseline and every component travel with the score.
        for i, r in enumerate(rows):
            components = {
                "open_caseload": {"weight": DISPATCH_WEIGHTS['open_cases'],
                                  "normalised": round(open_n[i], 3)},
                "recent_severity": {"weight": DISPATCH_WEIGHTS['recent_severity'],
                                    "normalised": round(recent_n[i], 3)},
                "high_severity_cases": {"weight": DISPATCH_WEIGHTS['high_severity'],
                                        "normalised": round(high_n[i], 3)},
                "trend_spike": {"weight": DISPATCH_WEIGHTS['spike'],
                                "normalised": round(spike_n[i], 3)},
            }
            risk = 100.0 * sum(c['weight'] * c['normalised'] for c in components.values())
            r['risk_score'] = round(risk, 1)
            r['risk_components'] = components
            r['risk_method'] = ("weighted sum of four min-max normalised district measures; "
                                "weights are a stated convention, not learned")

            r['risk_level'] = ('Critical' if risk >= RISK_BANDS['Critical']
                               else 'High' if risk >= RISK_BANDS['High']
                               else 'Moderate' if risk >= RISK_BANDS['Moderate'] else 'Low')

        # Apportion the declared pool by relative risk, after every district's score
        # is known. A floor guarantees minimum presence; the remainder is distributed
        # in proportion to risk.
        total_risk = sum(r['risk_score'] for r in rows) or 1.0
        floor_total = STAFFING['min_per_district'] * len(rows)
        distributable = max(0, STAFFING['state_deployable_officers'] - floor_total)
        for r in rows:
            share = r['risk_score'] / total_risk
            r['risk_share_pct'] = round(100.0 * share, 1)
            r['recommended_officers'] = int(round(
                STAFFING['min_per_district'] + distributable * share))
            r['staffing_breakdown'] = {
                "state_pool": STAFFING['state_deployable_officers'],
                "floor_per_district": STAFFING['min_per_district'],
                "risk_share_pct": r['risk_share_pct'],
                "from_risk_share": round(distributable * share, 1),
                "formula": (f"{STAFFING['min_per_district']} floor + "
                            f"{distributable} distributable x risk share"),
                "basis": ("an apportionment of a declared pool of "
                          f"{STAFFING['state_deployable_officers']} deployable officers, "
                          "ranked by relative district risk. The pool is a parameter, not a "
                          "measurement — this system does not know KSP establishment "
                          "strength, and this is not a substitute for a posting order."),
            }

        rows.sort(key=lambda x: x['risk_score'], reverse=True)
        data_cache['dispatch'] = rows
    except Exception as e:
        LOG.warning(f"Dispatch computation skipped: {e}")
        data_cache['dispatch'] = []

    try:
        data_cache['suspect_case_meta'] = _build_suspect_case_metadata()
    except Exception as e:
        LOG.error(f"suspect_case_meta failed: {e}")
        data_cache['suspect_case_meta'] = {}

    try:
        build_advanced_analytics(timestamp_dt, ml_bundle=ml_bundle)
    except Exception as e:
        LOG.error(f"Advanced analytics failed: {e}")

    data_cache['_load_complete'] = True
    return True


def build_advanced_analytics(ts=None, ml_bundle=None):
    """Load the ML layer and cache its outputs.

    The supervised models are produced offline by `python -m ml.train`, which
    writes fitted artifacts plus `artifacts/model_registry.json`. This function
    loads them; if an artifact is missing or was built against a different
    scikit-learn version, `ml.serving` retrains that one model in-process from the
    same code. Metrics are read from the registry and never recomputed here, so
    the numbers on the dashboard are exactly the numbers a training run produced.

    The graph-derived intelligence (link prediction, community detection) and the
    socio-economic correlation are still computed here, because they depend on the
    knowledge graph that is built at load time rather than on a fitted model.
    """
    df = data_cache.get('firs')
    if df is None:
        return

    from ml import serving as ml_serving

    LOG.info("Loading ML layer (artifacts -> in-process retrain fallback)...")
    bundle = ml_serving.load_all(data=ml_bundle)
    data_cache['ml_bundle'] = bundle
    data_cache['ml_report'] = bundle.get('report')
    data_cache['ml_sources'] = bundle.get('sources', {})

    data_cache['forecast'] = bundle['outputs'].get('forecast', {})
    data_cache['crime_clusters'] = bundle['outputs'].get('clusters', [])
    data_cache['recidivism'] = bundle['outputs'].get('recidivism', {"top": [], "scores": {}})
    data_cache['pendency'] = bundle['outputs'].get(
        'pendency', {"cases": [], "summary": {}, "by_district": [], "by_station": []})

    models['crime_classifier'] = bundle['predictors'].get('narrative_classifier')
    models['allocation_predictor'] = bundle['predictors'].get('case_closure')
    models['suspect_conviction_predictor'] = bundle['predictors'].get('conviction_outcome')
    models['pendency_predictor'] = bundle['predictors'].get('case_pendency')

    # Metric blocks kept under their existing cache keys so every consumer and
    # endpoint that already reads them keeps working unchanged.
    data_cache['classifier_metrics'] = _registry_metrics(bundle, 'narrative_classifier')
    data_cache['allocation_metrics'] = _registry_metrics(bundle, 'case_closure')
    data_cache['suspect_conviction_metrics'] = _registry_metrics(bundle, 'conviction_outcome')

    LOG.info("Predicting emerging criminal alliances (Adamic-Adar link prediction)...")
    data_cache['predicted_links'] = analytics.build_link_prediction(
        knowledge_graph, data_cache.get('suspects'))

    LOG.info("Detecting hidden syndicate communities (Louvain)...")
    data_cache['syndicate_communities'] = analytics.detect_crime_communities(
        knowledge_graph, data_cache.get('suspects'))

    LOG.info("Correlating crime with socio-economic indicators...")
    data_cache['socioeconomic'] = analytics.build_socioeconomic_correlation(
        df, data_cache.get('socio'))

    LOG.info("Building the case lifecycle funnel (closed cohort)...")
    try:
        data_cache['lifecycle'] = analytics.build_case_lifecycle(bundle.get('data') or {})
    except Exception as e:
        LOG.warning(f"  lifecycle funnel failed — {e}")
        data_cache['lifecycle'] = {}

    LOG.info("Computing the district Operational Risk Index...")
    try:
        data_cache['operational_index'] = analytics.build_operational_index(
            bundle.get('data') or {})
    except Exception as e:
        LOG.warning(f"  operational index failed — {e}")
        data_cache['operational_index'] = {}

    fc = (data_cache.get('forecast') or {}).get('overall', {}) or {}
    clf = data_cache['classifier_metrics']
    LOG.info(f"Advanced analytics ready: forecast next-week={fc.get('next_week')} "
          f"({fc.get('trend')}), {len(data_cache['crime_clusters'])} DBSCAN zones, "
          f"{len(data_cache['predicted_links'])} predicted links, "
          f"classifier acc={clf.get('accuracy')}, "
          f"detected syndicates={len(data_cache['syndicate_communities'])}, "
          f"{len(data_cache['recidivism'].get('top', []))} top-risk offenders.")


def _registry_metrics(bundle, name):
    """Flatten one registry entry into the shape the existing endpoints expect."""
    entry = (bundle.get('metrics') or {}).get(name) or {}
    metrics = entry.get('metrics') or {}
    out = dict(metrics)
    out['model'] = name
    out['algorithm'] = entry.get('algorithm')
    out['headline'] = entry.get('headline')
    out['baselines'] = entry.get('baselines', [])
    out['notes'] = entry.get('notes', [])
    out['split'] = entry.get('split', {})
    out['source'] = (bundle.get('sources') or {}).get(name)
    if entry.get('class_keywords'):
        out['class_keywords'] = {
            k: [t['token'] if isinstance(t, dict) else t for t in v]
            for k, v in entry['class_keywords'].items()
        }
    if entry.get('permutation_importance'):
        out['feature_importances'] = {
            f['feature']: f['importance'] for f in entry['permutation_importance']
        }
    return out


def build_extended_graph_and_analytics(graph=None):
    """Fuse the CCTNS registries into the knowledge graph and derive cross-entity
    intelligence (fraud rings, recovery rates, conviction rates, officer performance)."""
    df_firs = data_cache.get('firs')
    # Operates on the graph being built, so the live one is never mutated mid-flight.
    G = knowledge_graph if graph is None else graph
    ext = {}

    def _split(x):
        return [t for t in str(x).split(';') if t and t.lower() != 'nan']

    # --- Officers ↔ cases ---
    officers = data_cache.get('officers')
    if officers is not None and not officers.empty:
        for o in officers.itertuples():
            G.add_node(o.officer_id, type='Officer', name=o.name,
                       rank=o.rank, station=o.station)
        if 'officer_id' in df_firs.columns:
            for r in df_firs.itertuples():
                oid = getattr(r, 'officer_id', None)
                if isinstance(oid, str) and G.has_node(oid):
                    G.add_edge(oid, r.case_id, relationship='Investigating Officer')
        ext['officers_total'] = int(len(officers))
        top = officers.sort_values(['commendations', 'closure_rate'], ascending=False).head(5)
        ext['top_officers'] = [{
            "officer_id": o.officer_id, "name": o.name, "rank": o.rank,
            "station": o.station, "cases": int(o.cases_assigned),
            "closure_rate": float(o.closure_rate)
        } for o in top.itertuples()]

    # --- Stolen vehicles ↔ cases ---
    vehicles = data_cache.get('vehicles')
    if vehicles is not None and not vehicles.empty:
        for v in vehicles.itertuples():
            # Use getattr for column names to avoid syntax errors if column doesn't match
            v_num = getattr(v, 'vehicle_number', None)
            make = getattr(v, 'make_model', None)
            rec_st = getattr(v, 'recovery_status', None)
            lc = getattr(v, 'linked_case_id', None)
            
            if v_num:
                G.add_node(v_num, type='Vehicle', make=make, recovery=rec_st)
                if isinstance(lc, str) and lc and G.has_node(lc):
                    G.add_edge(v_num, lc, relationship='Vehicle Involved')
        rec = int((vehicles['recovery_status'] == 'Recovered').sum())
        ext['vehicles_total'] = int(len(vehicles))
        ext['vehicles_recovered'] = rec
        ext['vehicle_recovery_rate'] = round(100.0 * rec / len(vehicles), 1)

    # --- Cyber identifiers ↔ cases  → fraud-ring detection ---
    cyber = data_cache.get('cybercrime')
    if cyber is not None and not cyber.empty:
        ident_cases = {}
        fraud_by_case = dict(zip(cyber['case_id'], cyber['fraud_amount']))
        for c in cyber.itertuples():
            cid = c.case_id
            for col, pfx in [('phone_numbers', 'PHONE'), ('upi_ids', 'UPI'), ('bank_accounts', 'ACCT')]:
                val = getattr(c, col, '')
                for tok in _split(val):
                    nid = f"{pfx}:{tok}"
                    ident_cases.setdefault(nid, set()).add(cid)
                    if not G.has_node(nid):
                        G.add_node(nid, type='CyberID', kind=pfx)
                    if G.has_node(cid):
                        G.add_edge(nid, cid, relationship='Cyber Trace')
        rings = []
        for nid, cases in ident_cases.items():
            if len(cases) >= 3:
                total = sum(float(fraud_by_case.get(c, 0) or 0) for c in cases)
                rings.append({"identifier": nid, "linked_cases": len(cases),
                              "total_fraud": int(total), "cases": sorted(cases)[:8]})
        rings.sort(key=lambda x: (x['linked_cases'], x['total_fraud']), reverse=True)
        ext['cyber_rings'] = rings[:8]
        ext['cyber_total_fraud'] = int(cyber['fraud_amount'].fillna(0).sum())
        ext['cyber_victims'] = int(cyber['victim_count'].fillna(0).sum())
        ext['cyber_cases'] = int(len(cyber))

    # --- Criminal history & associate links ---
    criminals = data_cache.get('criminals')
    if criminals is not None and not criminals.empty:
        ext['rowdy_sheeters'] = int(criminals.get('rowdy_sheeter', pd.Series(dtype=bool)).sum())
        ext['active_warrants'] = int(criminals.get('active_warrant', pd.Series(dtype=bool)).sum())
        ext['history_sheeters'] = int(criminals.get('history_sheet', pd.Series(dtype=bool)).sum())
        for cr in criminals.itertuples():
            cid = getattr(cr, 'criminal_id', None)
            ass = getattr(cr, 'known_associates', '')
            for a in _split(ass):
                if cid and G.has_node(cid) and G.has_node(a):
                    G.add_edge(cid, a, relationship='Known Associate')

    # --- Missing persons ---
    missing = data_cache.get('missing')
    if missing is not None and not missing.empty:
        found = int((missing['found_status'] == 'Found').sum())
        ext['missing_total'] = int(len(missing))
        ext['missing_open'] = int((missing['found_status'] == 'Missing').sum())
        ext['missing_found_rate'] = round(100.0 * found / len(missing), 1)

    # --- Courts / chargesheets ---
    courts = data_cache.get('courts')
    if courts is not None and not courts.empty:
        conv = int((courts['conviction_status'] == 'Convicted').sum())
        decided = int(courts['conviction_status'].isin(['Convicted', 'Acquitted']).sum())
        ext['court_total'] = int(len(courts))
        ext['chargesheets_filed'] = int((courts['chargesheet_status'] == 'Filed').sum())
        ext['conviction_rate'] = round(100.0 * conv / decided, 1) if decided else 0.0

    # --- Complaints / Dial-112 ---
    complaints = data_cache.get('complaints')
    if complaints is not None and not complaints.empty:
        ext['complaints_total'] = int(len(complaints))
        ext['avg_response_min'] = round(float(complaints['response_time_min'].mean()), 1)

    data_cache['extended'] = ext
    LOG.info(f"Extended graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges; "
          f"cyber rings detected: {len(ext.get('cyber_rings', []))}")


from flask import Request, Response, make_response
import traceback
import json

def sanitize_for_json(obj):
    """Recursively replace NaN/Infinity (which json.dumps emits as bare
    NaN/Infinity tokens that JS's JSON.parse rejects) with None."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, (np.floating,)):
        val = float(obj)
        return val if math.isfinite(val) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    return obj

def jsonify_safe(data, status=200):
    return Response(json.dumps(sanitize_for_json(data)), status=status, mimetype='application/json')

def get_stats():
    df_firs = data_cache['firs']
    df_suspects = data_cache['suspects']
    
    total_cases = len(df_firs)
    total_suspects = len(df_suspects)
    
    gang_counts = df_suspects['gang_affiliation'].value_counts().to_dict()
    
    trends = df_firs.groupby('crime_type').size().reset_index(name='count').to_dict('records')
    
    graph_density = nx.density(knowledge_graph)
    
    return jsonify_safe({
        "total_cases": total_cases,
        "tracked_suspects": total_suspects,
        "gang_activity": [{"name": k, "value": v} for k, v in gang_counts.items()],
        "crime_trends": trends,
        "graph_density": round(graph_density * 1000, 4),
        "trend_alerts": data_cache.get('trend_alerts', []),
        "anomalies": data_cache.get('anomalies', []),
        "districts": sorted(df_firs['district'].unique().tolist()),
        "time_bands": sorted(df_firs['time_of_day'].unique().tolist()),
        "extended": _redacted_extended(data_cache.get('extended', {})),
        "graph_nodes": knowledge_graph.number_of_nodes(),
        "graph_edges": knowledge_graph.number_of_edges(),
        "models": _model_summary(),
    })


def _model_summary():
    """Compact snapshot of the ML layer for dashboard tiles."""
    fc = data_cache.get('forecast', {}) or {}
    overall = fc.get('overall') or {}
    rec = data_cache.get('recidivism', {}) or {}
    clf = data_cache.get('classifier_metrics', {}) or {}
    return {
        "forecast_next_week": overall.get('next_week'),
        "forecast_trend": overall.get('trend'),
        "forecast_change_pct": overall.get('change_pct'),
        "dbscan_clusters": len(data_cache.get('crime_clusters', [])),
        "predicted_links": len(data_cache.get('predicted_links', [])),
        "classifier_accuracy": clf.get('accuracy'),
        "recidivism_extreme": (rec.get('bands', {}) or {}).get('Extreme'),
        # The top watchlist row is a named individual. /stats is an open aggregate
        # endpoint, so it travels through the same redaction as /recidivism rather
        # than handing the highest-risk person's name to anonymous callers — which
        # is what it did before, defeating the gate on /recidivism entirely.
        "recidivism_top": _redacted_watchlist_row(
            rec.get('top', [{}])[0] if rec.get('top') else {}),
    }


def _redacted_extended(ext):
    """`extended` is aggregate except for two blocks that identify people.

    /stats is open, but /officers is investigator-gated — serving the same officer
    identities here would bypass that gate the way /analytics bypassed the
    watchlist gate. Officer performance is employee data; below investigator the
    ranking keeps its shape and loses the people. Station is dropped too: a rank
    plus a single named station identifies one person in a small posting.
    """
    if not ext or auth.has_role('investigator'):
        return ext
    safe = dict(ext)

    # Cyber ring identifiers ARE the personal data: PHONE:<number>, UPI:<handle>,
    # ACCT:<account>, plus the case ids they link. /cyber/rings is gated; serving the
    # same rows here defeated that gate exactly as /analytics defeated the watchlist
    # gate. The shape — how many rings, how big, how connected — is what an open
    # aggregate can honestly show.
    rings = ext.get('cyber_rings')
    if rings:
        safe['cyber_rings'] = [{
            "identifier": auth.pseudonym(r.get('identifier')),
            "kind": str(r.get('identifier', '')).split(':', 1)[0] or None,
            "linked_cases": r.get('linked_cases'),
            "total_fraud": r.get('total_fraud'),
            "redacted": True,
        } for r in rings]

    officers = ext.get('top_officers')
    if not officers:
        return safe
    safe['top_officers'] = [{
        "officer_id": auth.pseudonym(o.get('officer_id')),
        "name": auth.pseudonym(o.get('officer_id')),
        "rank": o.get('rank'),
        "cases": o.get('cases'),
        "closure_rate": o.get('closure_rate'),
        "redacted": True,
    } for o in officers]
    return safe


def _redacted_link(link):
    """A predicted alliance, with the two people removed below investigator."""
    if not link or auth.has_role('investigator'):
        return link
    return {
        "a": auth.pseudonym(link.get('a')),
        "b": auth.pseudonym(link.get('b')),
        "a_name": auth.pseudonym(link.get('a')),
        "b_name": auth.pseudonym(link.get('b')),
        "score": link.get('score'),
        "common_associates": link.get('common_associates'),
        "redacted": True,
    }


def _redacted_watchlist_row(row, ident=None):
    """Full row for a caller holding `watchlist_names` whose jurisdiction covers the
    person; the pseudonymised shape for everyone else.

    A district SP holds the permission but only for their district: a person with no
    case there arrives redacted, so the SP sees the whole watchlist's shape and the
    names of the people they are actually responsible for.
    """
    if not row:
        return {}
    ident = ident or auth.current_identity()
    if (auth.has_permission('watchlist_names', ident)
            and _subject_in_jurisdiction(row.get('criminal_id'), ident)):
        return row
    return auth.redact_watchlist_row(row)

def get_heatmap(request):
    df_firs = data_cache['firs']

    district = request.args.get('district')
    time_of_day = request.args.get('time_of_day')

    filtered = df_firs
    if district:
        filtered = filtered[filtered['district'] == district]
    if time_of_day:
        filtered = filtered[filtered['time_of_day'] == time_of_day]

    # Use the full filtered set (≈5k max) for a granular, detailed heatmap rather than a
    # coarse 3k random sample. Cap only as a safety valve for very large datasets.
    map_sample = filtered.sample(n=min(12000, len(filtered)), random_state=42) if len(filtered) else filtered
    # Built from column arrays rather than `iterrows()`. At 12,000 rows the row-wise
    # form constructed a pandas Series per point and cost about a second per request;
    # zipping the columns produces the identical list without touching pandas
    # per row. The presence check for police_station moves out of the loop, where it
    # was re-evaluating a column lookup 12,000 times to answer the same question.
    has_station = 'police_station' in map_sample.columns
    lat_v = map_sample['latitude'].to_numpy(dtype=float)
    lng_v = map_sample['longitude'].to_numpy(dtype=float)
    sev_v = map_sample['severity_score'].to_numpy(dtype=float)
    case_v = map_sample['case_id'].astype(str).tolist()
    type_v = map_sample['crime_type'].astype(str).tolist()
    dist_v = map_sample['district'].astype(str).tolist()
    stat_v = map_sample['status'].astype(str).tolist()
    ps_v = (map_sample['police_station'].astype(str).tolist() if has_station
            else ["Unknown PS"] * len(map_sample))

    points = [{
        "lat": float(lat),
        "lng": float(lng),
        # Bias weight toward severity so high-severity incidents read hotter and
        # the map shows structure instead of a single saturated blob.
        "weight": round(0.4 + (sev / 100.0) * 2.6, 3),
        # Identity so the map can reveal & open the actual FIR when zoomed in.
        "case_id": cid,
        "crime_type": ctype,
        "severity": int(sev),
        "district": dist,
        "police_station": ps,
        "status": status,
    } for lat, lng, sev, cid, ctype, dist, ps, status
        in zip(lat_v, lng_v, sev_v, case_v, type_v, dist_v, ps_v, stat_v)]

    # Predicted hotspots and DBSCAN clusters filtering
    clusters = data_cache.get('predicted_hotspots', [])
    dbscan = data_cache.get('crime_clusters', [])

    if district:
        # Filter DBSCAN clusters to this district
        dbscan = [c for c in dbscan if c.get('district') == district]
        
        # Filter predicted hotspots to this district's approximate bounds
        dist_points = filtered[['latitude', 'longitude']].dropna()
        if not dist_points.empty:
            lat_min, lat_max = dist_points['latitude'].min(), dist_points['latitude'].max()
            lon_min, lon_max = dist_points['longitude'].min(), dist_points['longitude'].max()
            # Expand slightly to capture hotspots on district borders
            clusters = [h for h in clusters if (lat_min - 0.05 <= h['lat'] <= lat_max + 0.05) and (lon_min - 0.05 <= h['lng'] <= lon_max + 0.05)]
        else:
            clusters = []
    elif time_of_day:
        clusters = []
        dbscan = []

    return jsonify_safe({
        "points": points,
        "clusters": clusters,
        "dbscan_clusters": dbscan,
    })

def get_network():
    suspect_id = request.args.get('suspect_id', '').strip()
    case_id = request.args.get('case_id', '').strip()
    q = request.args.get('q', '').strip().lower()

    subgraph_nodes = set()

    # 1. Pivot on suspect_id
    if suspect_id:
        if suspect_id in knowledge_graph:
            subgraph_nodes.add(suspect_id)
            cases = [nb for nb in knowledge_graph.neighbors(suspect_id) if knowledge_graph.nodes[nb].get('type') == 'Case']
            for c in cases[:10]:
                subgraph_nodes.add(c)
        else:
            matched_nodes = [n for n, d in knowledge_graph.nodes(data=True) 
                             if d.get('type') == 'Suspect' and suspect_id.lower() in d.get('name', '').lower()]
            for n in matched_nodes[:5]:
                subgraph_nodes.add(n)
                cases = [nb for nb in knowledge_graph.neighbors(n) if knowledge_graph.nodes[nb].get('type') == 'Case']
                for c in cases[:5]:
                    subgraph_nodes.add(c)

    # 2. Pivot on case_id
    elif case_id:
        if case_id in knowledge_graph:
            subgraph_nodes.add(case_id)
        else:
            matched_nodes = [n for n, d in knowledge_graph.nodes(data=True) 
                             if d.get('type') == 'Case' and case_id in n]
            for n in matched_nodes[:5]:
                subgraph_nodes.add(n)

    # 3. Pivot on search query q
    #
    # This searched suspect NAMES and case IDs only, while the UI offered it as a
    # semantic query and suggested "Bawaria Gang associates" as an example — which
    # returned nothing, as did any crime type. The graph already carries `gang` on
    # suspects and `crime_type`/`district` on cases; the query simply never looked at
    # them. It is still a substring match and the UI now says so, but it now matches
    # the fields a person would reasonably type.
    elif q:
        def _hay(d, *keys):
            return ' '.join(str(d.get(k, '') or '') for k in keys).lower()

        matched_susps = [n for n, d in knowledge_graph.nodes(data=True)
                         if d.get('type') == 'Suspect'
                         and (q in _hay(d, 'name', 'gang') or q in str(n).lower())]
        matched_cases = [n for n, d in knowledge_graph.nodes(data=True)
                         if d.get('type') == 'Case'
                         and (q in str(n).lower() or q in _hay(d, 'crime_type', 'district'))]
        
        for n in matched_susps[:5]:
            subgraph_nodes.add(n)
            cases = [nb for nb in knowledge_graph.neighbors(n) if knowledge_graph.nodes[nb].get('type') == 'Case']
            for c in cases[:5]:
                subgraph_nodes.add(c)
        for n in matched_cases[:5]:
            subgraph_nodes.add(n)

    # 4. Default: If no target is specified, return empty so the frontend shows a setup search screen.
    if not subgraph_nodes:
        return jsonify_safe({"nodes": [], "links": []})

    # Add direct neighbors of primary Cases (limit to 5 per case to maintain clean topology)
    case_nodes = [n for n in subgraph_nodes if knowledge_graph.nodes[n].get('type') == 'Case']
    for case in case_nodes:
        for nb in list(knowledge_graph.neighbors(case))[:6]:
            nb_type = knowledge_graph.nodes[nb].get('type')
            if nb_type in ('Suspect', 'CyberID', 'Vehicle', 'Officer'):
                subgraph_nodes.add(nb)

    subgraph = knowledge_graph.subgraph(subgraph_nodes)
    
    import networkx as nx
    centrality = nx.degree_centrality(subgraph) if len(subgraph) > 0 else {}
    
    nodes = []
    for n, data in subgraph.nodes(data=True):
        ntype = data.get('type', 'Other')
        group = 1 if ntype == 'Suspect' else 2 if ntype == 'Case' else 3 if ntype == 'CyberID' else 4 if ntype == 'Vehicle' else 5 if ntype == 'Officer' else 6
        
        if ntype == 'Suspect' or ntype == 'Officer':
            label = data.get('name', n)
        elif ntype == 'Case':
            label = f"{n} ({data.get('crime_type', '')})"
        elif ntype == 'CyberID':
            label = n
        elif ntype == 'Vehicle':
            label = n
        else:
            label = n
            
        node_details = dict(data)
        node_details['centrality'] = centrality.get(n, 0)
        node_details['degree'] = subgraph.degree(n)
        val = max(1, centrality.get(n, 0) * 50)
            
        nodes.append({
            "id": n,
            "group": group,
            "label": label,
            "type": ntype,
            "val": val,
            "details": node_details
        })
        
    links = []
    for u, v, data in subgraph.edges(data=True):
        rel = data.get('relationship', 'Linked')
        strength = 1.0
        rel_lower = rel.lower()
        if any(w in rel_lower for w in ('accused', 'primary', 'prime', 'mastermind')):
            strength = 3.0
        elif any(w in rel_lower for w in ('associate', 'accomplice', 'gang')):
            strength = 2.0
        elif any(w in rel_lower for w in ('witness', 'informant')):
            strength = 0.5
            
        links.append({
            "source": u,
            "target": v,
            "label": rel,
            "strength": strength
        })
        
    return jsonify_safe({"nodes": nodes, "links": links})


def get_case_dossier(case_id, jurisdiction=None):
    from sklearn.metrics.pairwise import cosine_similarity
    df_firs = data_cache['firs']
    df_reports = data_cache['reports']
    
    case_row = df_firs[df_firs['case_id'] == case_id]
    if case_row.empty:
        return jsonify_safe({"error": "Case not found"}), 404
        
    case_info = case_row.iloc[0].to_dict()
    
    report_row = df_reports[df_reports['case_id'] == case_id]
    report_text = report_row.iloc[0]['report_text'] if not report_row.empty else "No report."
    
    others = df_firs[df_firs['case_id'] != case_id].copy()

    # NLP narrative similarity (vectorized cosine similarity, already computed at load time)
    nlp_sim = np.zeros(len(others))
    if case_id in models['report_ids']:
        idx = models['report_ids'].index(case_id)
        cosine_similarities = cosine_similarity(models['tfidf_matrix'][idx:idx+1], models['tfidf_matrix']).flatten()
        sim_by_case = dict(zip(models['report_ids'], cosine_similarities))
        nlp_sim = others['case_id'].map(sim_by_case).fillna(0.0).to_numpy()

    weapon_match = (others['weapon_used'] == case_info.get('weapon_used')) & (others['weapon_used'] != 'None')
    vehicle_match = (others['vehicle_used'] == case_info.get('vehicle_used')) & (others['vehicle_used'] != 'None')
    target_match = others['target_type'] == case_info.get('target_type')
    time_match = others['time_of_day'] == case_info.get('time_of_day')

    dist_km = haversine_vec(case_info['latitude'], case_info['longitude'], others['latitude'].to_numpy(), others['longitude'].to_numpy())
    proximity_score = np.select(
        [dist_km < 2.0, dist_km < 5.0, dist_km < 10.0],
        [30.0, 20.0, 10.0],
        default=0.0
    )

    composite_score = (
        weapon_match.to_numpy() * 15.0 +
        vehicle_match.to_numpy() * 10.0 +
        target_match.to_numpy() * 10.0 +
        time_match.to_numpy() * 5.0 +
        proximity_score +
        nlp_sim * 30.0
    )

    others['_score'] = composite_score
    others['_nlp_sim'] = nlp_sim
    others['_dist_km'] = dist_km
    others['_weapon_match'] = weapon_match.to_numpy()
    others['_vehicle_match'] = vehicle_match.to_numpy()
    others['_target_match'] = target_match.to_numpy()
    others['_time_match'] = time_match.to_numpy()

    top_matches = others[others['_score'] > 40.0].sort_values('_score', ascending=False).head(4)

    similar_cases = []
    for _, row in top_matches.iterrows():
        match_factors = []
        if row['_weapon_match']:
            match_factors.append(f"Weapon ({row['weapon_used']})")
        if row['_vehicle_match']:
            match_factors.append(f"Vehicle ({row['vehicle_used']})")
        if row['_target_match']:
            match_factors.append(f"Target ({row['target_type']})")
        if row['_time_match']:
            match_factors.append(f"Time ({row['time_of_day']})")

        if row['_dist_km'] < 2.0:
            match_factors.append("Hyper-Local (<2km)")
        elif row['_dist_km'] < 5.0:
            match_factors.append("Close Proximity (<5km)")
        elif row['_dist_km'] < 10.0:
            match_factors.append("Same Zone (<10km)")

        if row['_nlp_sim'] > 0.4:
            match_factors.append("High Narrative Semantic Match")

        similar_cases.append({
            "case_id": row['case_id'],
            "similarity_score": round(float(row['_score']), 1),
            "status": row['status'],
            "severity": int(row['severity_score']),
            "match_factors": match_factors
        })
                
    linked_suspects = []
    if knowledge_graph.has_node(case_id):
        for neighbor in knowledge_graph.neighbors(case_id):
            if knowledge_graph.nodes[neighbor].get('type') == 'Suspect':
                edge_data = knowledge_graph.get_edge_data(case_id, neighbor)
                node_data = knowledge_graph.nodes[neighbor]
                linked_suspects.append({
                    "suspect_id": neighbor,
                    "name": node_data.get('name'),
                    "gang": node_data.get('gang'),
                    "relationship": edge_data.get('relationship')
                })
                
    # Court / chargesheet status for this FIR
    court_info = None
    courts = data_cache.get('courts')
    if courts is not None and not courts.empty:
        cr = courts[courts['fir_number'] == case_id]
        if not cr.empty:
            court_info = cr.iloc[0].to_dict()

    # Linked stolen vehicles
    linked_vehicles = []
    vehicles = data_cache.get('vehicles')
    if vehicles is not None and not vehicles.empty:
        vr = vehicles[vehicles['linked_case_id'] == case_id]
        for _, v in vr.iterrows():
            linked_vehicles.append({"vehicle_number": v['vehicle_number'], "make_model": v.get('make_model'),
                                    "recovery_status": v.get('recovery_status'), "owner_name": v.get('owner_name')})

    # CCTV evidence on this case
    cctv_hits = []
    cctv = data_cache.get('cctv')
    if cctv is not None and not cctv.empty:
        ch = cctv[cctv['case_link'] == case_id].head(4)
        for _, cam in ch.iterrows():
            cctv_hits.append({"camera_id": cam['camera_id'], "timestamp": str(cam['timestamp']),
                              "detected_vehicle": cam.get('detected_vehicle'), "video_url": cam.get('video_url')})

    # Cyber trail
    cyber_info = None
    cyber = data_cache.get('cybercrime')
    if cyber is not None and not cyber.empty:
        cyr = cyber[cyber['case_id'] == case_id]
        if not cyr.empty:
            cyber_info = cyr.iloc[0].to_dict()

    # Calculate conviction probability if court info exists
    if court_info:
        prob = 35  # Baseline for chargesheet filed
        justifications = ["Chargesheet filed"]
        
        wpn = case_info.get('weapon_used', 'None')
        if wpn and wpn != 'None':
            prob += 15
            justifications.append(f"Weapon recovered ({wpn})")
            
        wits = int(case_info.get('witnesses', 0))
        if wits > 0:
            wit_pts = min(20, wits * 5)
            prob += wit_pts
            justifications.append(f"{wits} witness statement(s)")
            
        if len(cctv_hits) > 0:
            prob += 20
            justifications.append(f"{len(cctv_hits)} CCTV footage links")
            
        if len(linked_vehicles) > 0:
            recovered = any(v.get('recovery_status') == 'Recovered' for v in linked_vehicles)
            if recovered:
                prob += 15
                justifications.append("Getaway vehicle recovered")
            else:
                prob += 8
                justifications.append("Getaway vehicle identified")
                
        if cyber_info:
            prob += 15
            justifications.append("Cyber UPI & phone trace")
            
        verdict = court_info.get('conviction_status')
        if verdict == 'Convicted':
            prob = 100
            justifications = ["Case convicted by court judgement"]
        elif verdict == 'Acquitted':
            prob = 0
            justifications = ["Case acquitted by court judgement"]
            
        court_info['conviction_probability'] = min(95, max(15, prob)) if verdict not in ('Convicted', 'Acquitted') else prob
        court_info['probability_justification'] = ", ".join(justifications)

    # Investigating officer
    officer_info = None
    officers = data_cache.get('officers')
    if officers is not None and not officers.empty and case_info.get('officer_id'):
        orow = officers[officers['officer_id'] == case_info['officer_id']]
        if not orow.empty:
            officer_info = orow.iloc[0].to_dict()

    # AI narrative check: the trained classifier independently reads the report text and
    # predicts the crime type, and we flag whether it agrees with the filed classification.
    classifier_read = None
    clf = models.get('crime_classifier')
    if clf and report_text and report_text != "No report.":
        preds = clf(report_text)
        if preds:
            classifier_read = {
                "predictions": preds,
                "top": preds[0]["label"],
                "confidence": preds[0]["prob"],
                "agrees_with_filing": preds[0]["label"] == case_info.get('crime_type'),
            }

    # Complainant details resolution
    complainant_info = None
    if 'ComplainantDetails' in data_cache and 'ReligionMaster' in data_cache:
        df_comp = data_cache['ComplainantDetails']
        df_cm = data_cache['CaseMaster']
        cm_row = df_cm[df_cm['CrimeNo'].astype(str) == str(case_id)]
        if not cm_row.empty:
            cm_id = cm_row.iloc[0]['CaseMasterID']
            comp_rows = df_comp[df_comp['CaseMasterID'] == cm_id]
            if not comp_rows.empty:
                complainant_info = comp_rows.iloc[0].to_dict()
                rel_name = data_cache['ReligionMaster'].set_index('ReligionID')['ReligionName'].to_dict().get(complainant_info.get('ReligionID'), 'Unknown')
                caste_name = data_cache['CasteMaster'].set_index('caste_master_id')['caste_master_name'].to_dict().get(complainant_info.get('CasteID'), 'Unknown')
                occ_name = data_cache['OccupationMaster'].set_index('OccupationID')['OccupationName'].to_dict().get(complainant_info.get('OccupationID'), 'Unknown')
                complainant_info['religion_name'] = rel_name
                complainant_info['caste_name'] = caste_name
                complainant_info['occupation_name'] = occ_name

    # Arrest / Surrender events
    arrest_events = []
    if 'ArrestSurrender' in data_cache and 'CaseMaster' in data_cache:
        df_as = data_cache['ArrestSurrender']
        df_cm = data_cache['CaseMaster']
        cm_row = df_cm[df_cm['CrimeNo'].astype(str) == str(case_id)]
        if not cm_row.empty:
            cm_id = cm_row.iloc[0]['CaseMasterID']
            as_rows = df_as[df_as['CaseMasterID'] == cm_id]
            if not as_rows.empty:
                df_acc = data_cache.get('Accused')
                acc_name_map = {}
                if df_acc is not None:
                    acc_name_map = dict(zip(df_acc['AccusedMasterID'], df_acc['AccusedName']))
                
                df_emp = data_cache.get('Employee')
                emp_name_map = {}
                if df_emp is not None:
                    emp_name_map = dict(zip(df_emp['EmployeeID'], df_emp['FirstName']))
                    
                df_court = data_cache.get('Court')
                court_name_map = {}
                if df_court is not None:
                    court_name_map = dict(zip(df_court['CourtID'], df_court['CourtName']))

                for _, r in as_rows.iterrows():
                    arrest_events.append({
                        "arrest_id": int(r['ArrestSurrenderID']),
                        "type": "Arrest" if int(r['ArrestSurrenderTypeID']) == 1 else "Surrender",
                        "date": str(r['ArrestSurrenderDate']),
                        "accused_name": acc_name_map.get(r['AccusedMasterID'], f"Accused #{r['AccusedMasterID']}"),
                        "io_name": emp_name_map.get(r['IOID'], f"Officer #{r['IOID']}"),
                        "court_name": court_name_map.get(r['CourtID'], f"Court #{r['CourtID']}"),
                        "is_primary": bool(r['IsAccused']),
                        "is_complainant_accused": bool(r['IsComplainantAccused'])
                    })

    # Chargesheet details
    chargesheet_info = None
    if 'ChargesheetDetails' in data_cache and 'CaseMaster' in data_cache:
        df_cs = data_cache['ChargesheetDetails']
        df_cm = data_cache['CaseMaster']
        cm_row = df_cm[df_cm['CrimeNo'].astype(str) == str(case_id)]
        if not cm_row.empty:
            cm_id = cm_row.iloc[0]['CaseMasterID']
            cs_rows = df_cs[df_cs['CaseMasterID'] == cm_id]
            if not cs_rows.empty:
                cs_row = cs_rows.iloc[0]
                df_emp = data_cache.get('Employee')
                emp_name_map = {}
                if df_emp is not None:
                    emp_name_map = dict(zip(df_emp['EmployeeID'], df_emp['FirstName']))

                type_map = {"A": "Chargesheet", "B": "False Case", "C": "Undetected"}
                chargesheet_info = {
                    "cs_id": int(cs_row['CSID']),
                    "date": str(cs_row['csdate']),
                    "report_type": type_map.get(cs_row['cstype'], cs_row['cstype']),
                    "submitting_officer": emp_name_map.get(cs_row['PolicePersonID'], f"Officer #{cs_row['PolicePersonID']}")
                }

    # AI Suspect Suggestions
    ai_suggested_suspects = []
    try:
        ai_suggested_suspects = analytics.recommend_suspects_for_case(
            case_id, df_firs, data_cache.get('suspects'), data_cache.get('Accused'), data_cache
        )
    except Exception as e:
        LOG.warning(f"Failed to generate AI suspect suggestions: {e}")

    return jsonify_safe({
        "case_details": case_info,
        "narrative_report": report_text,
        "nlp_similar_cases": similar_cases,
        "graph_linked_suspects": linked_suspects,
        "court": court_info,
        "vehicles": linked_vehicles,
        "cctv": cctv_hits,
        "cyber": cyber_info,
        "officer": officer_info,
        "classifier_read": classifier_read,
        "complainant": complainant_info,
        "arrests": arrest_events,
        "chargesheet": chargesheet_info,
        "ai_suggested_suspects": ai_suggested_suspects,
        # Photographs taken at registration (FIRs registered through the platform only).
        "photos": fir_registry.photos_for(case_id),
        "jurisdiction": jurisdiction,
    })

def get_suspect_dossier(suspect_id, jurisdiction=None):
    """Repeat-offender profile: every case linked to a suspect plus their MO summary."""
    df_suspects = data_cache['suspects']
    df_firs = data_cache['firs']

    row = df_suspects[df_suspects['suspect_id'] == suspect_id]
    if row.empty:
        return jsonify_safe({"error": "Suspect not found"}), 404
    info = row.iloc[0].to_dict()

    # Merge rich details from CriminalsRegistry
    df_crim = data_cache.get('criminals')
    if df_crim is not None and not df_crim.empty:
        crim_row = df_crim[df_crim['criminal_id'] == suspect_id]
        if not crim_row.empty:
            info.update(crim_row.iloc[0].to_dict())
            
    # Resolve age from Accused table
    df_acc = data_cache.get('Accused')
    if df_acc is not None and not df_acc.empty:
        acc_rows = df_acc[df_acc['PersonID'] == suspect_id]
        if not acc_rows.empty:
            info['age'] = int(acc_rows.iloc[0]['AgeYear'])

    # Collect linked cases (and the relationship label) from the knowledge graph.
    rel_map = {}
    if knowledge_graph.has_node(suspect_id):
        for nb in knowledge_graph.neighbors(suspect_id):
            if knowledge_graph.nodes[nb].get('type') == 'Case':
                rel_map[nb] = knowledge_graph.get_edge_data(suspect_id, nb).get('relationship', 'Linked')

    case_rows = df_firs[df_firs['case_id'].isin(rel_map.keys())].copy()

    linked_cases = []
    for _, cr in case_rows.sort_values('timestamp', ascending=False).iterrows():
        linked_cases.append({
            "case_id": cr['case_id'],
            "crime_type": cr['crime_type'],
            "district": cr['district'],
            "status": cr['status'],
            "severity": int(cr['severity_score']),
            "time_of_day": cr['time_of_day'],
            "weapon_used": cr['weapon_used'],
            "timestamp": str(cr['timestamp']),
            "relationship": rel_map.get(cr['case_id'], 'Linked')
        })

    # Modus-operandi summary across all their cases.
    def top_counts(series, n=3):
        return [{"label": k, "count": int(v)} for k, v in series.value_counts().head(n).items()]

    mo_summary = {
        "total_cases": len(case_rows),
        "open_cases": int((case_rows['status'] != 'Closed').sum()) if len(case_rows) else 0,
        "avg_severity": round(float(case_rows['severity_score'].mean()), 1) if len(case_rows) else 0,
        "top_crime_types": top_counts(case_rows['crime_type']) if len(case_rows) else [],
        "top_districts": top_counts(case_rows['district']) if len(case_rows) else [],
        "top_weapons": top_counts(case_rows[case_rows['weapon_used'] != 'None']['weapon_used']) if len(case_rows) else [],
        "active_districts": int(case_rows['district'].nunique()) if len(case_rows) else 0,
    }

    # Network centrality rank (1 = most connected).
    centrality_rank = None
    top = data_cache.get('top_suspects', [])
    for i, (sid, _) in enumerate(top):
        if sid == suspect_id:
            centrality_rank = i + 1
            break

    # ML conviction outlook. The model scores an (accused, case) pair rather than a
    # person — how strong a prosecution is depends on the case — so this reports the
    # probability for the offender's most recent case and names which case it is.
    ml_conviction_prob = None
    ml_conviction_basis = None
    bundle = data_cache.get('ml_bundle')
    if bundle:
        from ml import serving as ml_serving
        outlook = ml_serving.conviction_outlook(
            suspect_id, bundle['data'],
            bundle['predictors'].get('conviction_outcome'),
            bundle['artifacts'].get('conviction_outcome'))
        if outlook:
            ml_conviction_prob = int(round(outlook['probability'] * 100))
            ml_conviction_basis = outlook

    # Suspect correlation report
    correlation_report = {}
    try:
        correlation_report = analytics.get_suspect_crime_correlation(
            suspect_id, case_rows, df_firs, data_cache
        )
    except Exception as e:
        LOG.warning(f"Failed to generate suspect correlation report: {e}")

    return jsonify_safe({
        "suspect": info,
        "linked_cases": linked_cases,
        "mo_summary": mo_summary,
        "centrality_rank": centrality_rank,
        "ml_conviction_probability": ml_conviction_prob,
        "ml_conviction_basis": ml_conviction_basis,
        "ml_feature_importances": data_cache.get("suspect_conviction_metrics", {}).get("feature_importances", {}),
        "correlation_report": correlation_report,
        "jurisdiction": jurisdiction,
    })

def get_all_cases(request, identity=None):
    df = data_cache['firs']
    a = request.args

    # Jurisdiction first, and from the signed token rather than the query string:
    # a `district=` parameter below can only narrow what is left, never widen it.
    scope = (identity or {}).get('scope') or {"level": "state"}
    if scope.get('level') in ('district', 'station'):
        df = df[df['district'] == scope['district']]
    if scope.get('level') == 'station':
        df = df[df['police_station'] == scope['station']]
    df = df.copy()

    if 'fatalities' in df.columns:
        df['casualties'] = df['fatalities'].fillna(0).astype(int) + df['injured'].fillna(0).astype(int)
    else:
        df['casualties'] = 0

    status = a.get('status')
    if status and status.lower() != 'all':
        st_val = status.lower().strip()
        if st_val == 'open':
            df = df[~df['status'].str.lower().str.contains('closed|disposed|convicted', na=False)]
        elif st_val in ('investigating', 'under investigation'):
            df = df[~df['status'].str.lower().str.contains('closed|disposed|convicted', na=False)]
        elif st_val in ('closed', 'disposed'):
            df = df[df['status'].str.lower().str.contains('closed|disposed|convicted|acquitted', na=False)]
        else:
            df = df[df['status'].str.lower().str.contains(st_val, na=False)]
    if a.get('district'):
        df = df[df['district'] == a.get('district')]
    if a.get('crime_type'):
        df = df[df['crime_type'] == a.get('crime_type')]
    if a.get('time_of_day'):
        df = df[df['time_of_day'] == a.get('time_of_day')]
    min_sev = a.get('min_severity', type=int)
    if min_sev:
        df = df[df['severity_score'] >= min_sev]
    if a.get('casualties') == 'true':
        df = df[df['casualties'] > 0]
    q = (a.get('q') or '').strip().lower()
    if q:
        df = df[
            df['case_id'].str.lower().str.contains(q, na=False)
            | df['crime_type'].str.lower().str.contains(q, na=False)
            | df['district'].str.lower().str.contains(q, na=False)
            | df['CrimeNo'].str.lower().str.contains(q, na=False)
            | df['CaseNo'].str.lower().str.contains(q, na=False)
        ]

    sort = a.get('sort', 'recent')
    if sort == 'severity':
        df = df.sort_values('severity_score', ascending=False)
    elif sort == 'casualties':
        df = df.sort_values(['casualties', 'severity_score'], ascending=False)
    else:
        df = df.sort_values('timestamp', ascending=False)

    total = int(len(df))
    cols = [c for c in ['case_id', 'district', 'crime_type', 'timestamp', 'severity_score',
                        'status', 'time_of_day', 'casualties', 'fatalities', 'injured',
                        'ipc_bns_sections', 'police_station', 'accused_count', 'CrimeNo', 'CaseNo'] if c in df.columns]
    limit = a.get('limit', default=150, type=int) or 150
    rows = df[cols].fillna("").head(max(1, min(limit, 400))).to_dict('records')
    src = data_cache['firs']
    # A scoped officer is offered only the district they can list, so the filter
    # dropdown does not advertise districts that will always come back empty.
    districts = ([scope['district']] if scope.get('level') in ('district', 'station')
                 else sorted(src['district'].unique().tolist()))
    return jsonify_safe({
        "cases": rows, "total": total,
        "crime_types": sorted(src['crime_type'].unique().tolist()),
        "districts": districts,
        "time_bands": sorted(src['time_of_day'].unique().tolist()),
        "scope": _scope_block(identity, a.get('district')) if identity else None,
    })

def build_intelligence_briefing():
    """Summarize the live dataset into a compact text briefing the LLM can ground its answers in."""
    df_firs = data_cache.get('firs')
    df_suspects = data_cache.get('suspects')
    if df_firs is None or df_suspects is None:
        return "No live data is currently loaded."

    lines = []
    lines.append(f"Total cases on file: {len(df_firs)}")
    lines.append(f"Total tracked suspects: {len(df_suspects)}")

    top_crimes = df_firs['crime_type'].value_counts().head(5)
    lines.append("Top crime types overall: " + ", ".join(f"{c} ({n})" for c, n in top_crimes.items()))

    top_districts = df_firs['district'].value_counts().head(5)
    lines.append("Districts with the most cases: " + ", ".join(f"{d} ({n})" for d, n in top_districts.items()))

    open_counts = df_firs['status'].value_counts()
    lines.append("Case status breakdown: " + ", ".join(f"{s} ({n})" for s, n in open_counts.items()))

    try:
        sev_row = df_firs.loc[df_firs['severity_score'].idxmax()]
        lines.append(f"Highest-severity case on file: {sev_row['case_id']} "
                     f"({sev_row['crime_type']} in {sev_row['district']}, severity {int(sev_row['severity_score'])}, {sev_row['status']})")
        ts = pd.to_datetime(df_firs['timestamp'], errors='coerce')
        recent_row = df_firs.loc[ts.idxmax()]
        lines.append(f"Most recent case on file: {recent_row['case_id']} "
                     f"({recent_row['crime_type']} in {recent_row['district']}, {recent_row['timestamp']})")
        if 'fatalities' in df_firs.columns:
            tot_fatal = int(df_firs['fatalities'].fillna(0).sum())
            tot_inj = int(df_firs['injured'].fillna(0).sum())
            n_fatal_cases = int((df_firs['fatalities'].fillna(0) > 0).sum())
            deadliest = df_firs.loc[df_firs['fatalities'].fillna(0).idxmax()]
            lines.append(f"Casualties / homicides: {tot_fatal} fatalities and {tot_inj} injured across "
                         f"{n_fatal_cases} cases involving deaths. Deadliest/most-murders case: "
                         f"{deadliest['case_id']} ({deadliest['crime_type']} in {deadliest['district']}, "
                         f"{int(deadliest['fatalities'])} killed). (Note: homicides are recorded via the "
                         f"fatalities field; there is no separate 'Murder' crime type.)")
            fat_by_dist = df_firs.groupby('district')['fatalities'].sum().sort_values(ascending=False).head(3)
            lines.append("Districts with most fatalities: " + ", ".join(f"{d} ({int(n)})" for d, n in fat_by_dist.items()))
    except Exception as e:
        # Losing part of the briefing means the LLM answers from an incomplete
        # ground truth and cannot tell. Degrade loudly.
        LOG.warning("briefing: casualty/recency section omitted (%s)", e)
        lines.append("[briefing incomplete: casualty and recency figures unavailable]")

    cyber_df = data_cache.get('cybercrime')
    if cyber_df is not None and not cyber_df.empty:
        try:
            big = cyber_df.loc[cyber_df['fraud_amount'].fillna(0).idxmax()]
            lines.append(f"Biggest single cyber fraud: {big['case_id']} (Rs {int(big['fraud_amount']):,}).")
        except Exception as e:
            LOG.warning("briefing: cyber-fraud total unavailable (%s)", e)

    gang_counts = df_suspects['gang_affiliation'].value_counts().head(5)
    lines.append("Most active gang affiliations: " + ", ".join(f"{g} ({n})" for g, n in gang_counts.items()))

    top_suspect_ids = [s[0] for s in data_cache.get('top_suspects', [])[:5]]
    if top_suspect_ids:
        names = []
        for sid in top_suspect_ids:
            if knowledge_graph.has_node(sid):
                names.append(
                    f"{privacy.mask(knowledge_graph.nodes[sid].get('name', sid))} ({sid})")
        lines.append("Most-connected suspects in the network graph: " + ", ".join(names))

    alerts = data_cache.get('trend_alerts', [])
    if alerts:
        alert_strs = [
            f"{a['crime_type']} in {a['district']} ({a['recent_count']} cases in last 30 days vs expected {a['expected_count']}, {a['spike_ratio']}x spike)"
            for a in alerts
        ]
        lines.append("Emerging trend alerts: " + "; ".join(alert_strs))
    else:
        lines.append("Emerging trend alerts: none detected above threshold.")

    hotspots = data_cache.get('predicted_hotspots', [])
    if hotspots:
        hotspot_strs = [f"({h['lat']:.4f}, {h['lng']:.4f}, intensity {h['intensity']})" for h in hotspots[:5]]
        lines.append("Predicted hotspot zones (KDE on high-severity cases): " + "; ".join(hotspot_strs))

    anomalies = data_cache.get('anomalies', [])
    if anomalies:
        anom_strs = [f"{a['case_id']} ({a['crime_type']} in {a['district']}: {', '.join(a['reasons'])})" for a in anomalies[:5]]
        lines.append("Flagged behavioral anomalies (Isolation Forest): " + "; ".join(anom_strs))

    ext = data_cache.get('extended', {})
    if ext:
        if 'vehicles_total' in ext:
            lines.append(f"Stolen vehicles: {ext['vehicles_total']} registered, {ext['vehicles_recovered']} "
                         f"recovered ({ext['vehicle_recovery_rate']}% recovery rate).")
        if 'cyber_total_fraud' in ext:
            lines.append(f"Cybercrime: {ext['cyber_cases']} cases, total reported fraud "
                         f"Rs {ext['cyber_total_fraud']:,} across {ext['cyber_victims']} victims.")
        rings = ext.get('cyber_rings', [])
        if rings:
            top_ring = rings[0]
            lines.append(f"Largest cyber fraud ring: identifier {top_ring['identifier']} links "
                         f"{top_ring['linked_cases']} cases (Rs {top_ring['total_fraud']:,}). "
                         f"{len(rings)} multi-case rings detected.")
        if 'missing_total' in ext:
            lines.append(f"Missing persons: {ext['missing_total']} on record, {ext['missing_open']} still "
                         f"missing ({ext['missing_found_rate']}% found).")
        if 'conviction_rate' in ext:
            lines.append(f"Courts: {ext['chargesheets_filed']} chargesheets filed, "
                         f"{ext['conviction_rate']}% conviction rate among decided cases.")
        if 'rowdy_sheeters' in ext:
            lines.append(f"Criminal history: {ext['rowdy_sheeters']} rowdy-sheeters, "
                         f"{ext.get('history_sheeters', 0)} history-sheeters, {ext.get('active_warrants', 0)} active warrants.")
        if 'officers_total' in ext:
            lines.append(f"Force: {ext['officers_total']} officers on the system; "
                         f"avg Dial-112 response {ext.get('avg_response_min', 'n/a')} min.")

    # --- Phase-2 ML layer (predictive intelligence) ---
    fc = data_cache.get('forecast', {}) or {}
    overall = fc.get('overall')
    if overall:
        lines.append(f"FORECAST (ML, Ridge+Fourier): statewide next-week incidents ≈ {overall['next_week']} "
                     f"({overall['trend']}, {overall['change_pct']:+}% vs prior 4 weeks; avg {overall['avg_weekly']}/wk).")
        rising = sorted([(d, s['change_pct']) for d, s in fc.get('by_district', {}).items() if s['trend'] == 'rising'],
                        key=lambda x: x[1], reverse=True)[:3]
        if rising:
            lines.append("Districts forecast to rise: " + ", ".join(f"{d} ({c:+}%)" for d, c in rising))

    clusters = data_cache.get('crime_clusters', [])
    if clusters:
        top_c = clusters[0]
        lines.append(f"DBSCAN crime clusters: {len(clusters)} dense zones. Largest: {top_c['cases']} incidents of "
                     f"{top_c['dominant_crime']} around {top_c['district']} (~{top_c['radius_km']}km radius).")

    links = data_cache.get('predicted_links', [])
    if links:
        l0 = links[0]
        lines.append(f"Link prediction (Adamic-Adar): {len(links)} likely emerging alliances flagged. Strongest: "
                     f"{privacy.mask(l0['a_name'])} ↔ {privacy.mask(l0['b_name'])} "
                     f"({l0['common_associates']} shared associates, score {l0['score']}).")

    rec = data_cache.get('recidivism', {}) or {}
    if rec.get('top'):
        bands = rec.get('bands', {})
        t0 = rec['top'][0]
        lines.append(f"Recidivism risk index: {bands.get('Extreme', 0)} offenders in the Extreme band. Highest-risk: "
                     f"{privacy.mask(t0['name'])} ({t0['criminal_id']}, index {t0['risk_index']}/100, "
                     f"{t0['past_fir_count']} priors).")

    clf = data_cache.get('classifier_metrics', {})
    if clf.get('accuracy'):
        lines.append(f"FIR narrative classifier (TF-IDF+LogReg): {clf['accuracy']*100:.0f}% accuracy across "
                     f"{len(clf.get('classes', []))} crime types — auto-reads report text to confirm crime type.")

    socio = data_cache.get('socioeconomic', {}) or {}
    if socio.get('insights'):
        lines.append("Socio-economic correlation (crime × demographics): " + " ".join(socio['insights'][:3]))

    return "\n".join(lines)

@app.route('/serverless/api/stats', methods=['GET'])
def route_stats():
    return get_stats()

@app.route('/serverless/api/heatmap', methods=['GET'])
def route_heatmap():
    return get_heatmap(request)

@app.route('/serverless/api/network', methods=['GET'])
@audit.audited('read_network_graph')
@auth.require_role('investigator')
def route_network():
    return get_network()

@app.route('/serverless/api/case/<case_id>', methods=['GET'])
@audit.audited('read_case_dossier', subject_type='case', subject_arg='case_id')
@auth.require_permission('view_cases')
def route_case(case_id):
    return get_case_dossier(case_id, jurisdiction=_case_jurisdiction(case_id))

@app.route('/serverless/api/cases', methods=['GET'])
@audit.audited('list_cases')
@auth.require_permission('view_cases')
def route_cases():
    return get_all_cases(request, identity=auth.current_identity())

@app.route('/serverless/api/suspect/<suspect_id>', methods=['GET'])
@audit.audited('read_offender_dossier', subject_type='offender', subject_arg='suspect_id')
@auth.require_permission('view_dossiers')
def route_suspect(suspect_id):
    return get_suspect_dossier(suspect_id, jurisdiction=_suspect_jurisdiction(suspect_id))


# ---------------------------------------------------------------------------
# Jurisdiction
# ---------------------------------------------------------------------------
# District and station roles see *lists* narrowed to their jurisdiction, taken from
# the signed token so no query parameter can widen it. Opening a single record
# outside it is allowed — an investigation crosses district lines every day, and a
# hard refusal would push officers to borrow a colleague's login — but it leaves a
# second, specific audit entry and the response says so. That is how a real record
# system handles it: the access is permitted, and it is conspicuous.

def _scope_block(ident, requested_district=None):
    """The `scope` field every jurisdiction-filtered list view returns."""
    scope = ident["scope"]
    restricted = scope["level"] != "state"
    out = {"level": scope["level"], "district": scope["district"],
           "station": scope["station"], "restricted": restricted}
    if restricted:
        out["note"] = ("Limited to your jurisdiction by your sign-in. A query parameter "
                       "can narrow this list but never widen it.")
        if requested_district and str(requested_district).strip().lower() != scope["district"].lower():
            out["requested_district_outside_scope"] = requested_district
    return out


def _linked_case_rows(suspect_id):
    """FIR rows linked to a person in the knowledge graph — the same links the
    offender dossier lists, so "in scope" means the cases the officer will see."""
    if not knowledge_graph.has_node(suspect_id):
        return data_cache['firs'].iloc[0:0]
    case_ids = [nb for nb in knowledge_graph.neighbors(suspect_id)
                if knowledge_graph.nodes[nb].get('type') == 'Case']
    df = data_cache['firs']
    return df[df['case_id'].isin(case_ids)]


def _subject_in_jurisdiction(suspect_id, ident):
    """A person is in a scoped officer's jurisdiction if any linked case is."""
    if ident["scope"]["level"] == "state":
        return True
    rows = _linked_case_rows(suspect_id)
    return any(auth.in_jurisdiction(d, st, ident)
               for d, st in zip(rows['district'], rows['police_station']))


def _jurisdiction_result(ident, subject_type, subject_id, in_scope, record):
    """Build the response block, and audit the read separately when out of scope."""
    block = {"in_scope": bool(in_scope), "scope": dict(ident["scope"]), "record": record}
    if not in_scope:
        audit.record('out_of_jurisdiction_read', subject_type, subject_id,
                     reason=audit.stated_reason(),
                     extra={"scope_level": ident["scope"]["level"],
                            "scope_district": ident["scope"]["district"],
                            "scope_station": ident["scope"]["station"],
                            "record_location": record})
        block["audited"] = True
        block["note"] = ("This record is outside your jurisdiction. You may read it, and "
                         "the access has been recorded separately for review.")
    return block


def _case_jurisdiction(case_id):
    """Jurisdiction block for one FIR, or None if there is no such case (the dossier
    answers 404 on its own)."""
    ident = auth.current_identity()
    df = data_cache['firs']
    row = df[df['case_id'] == case_id]
    if row.empty:
        return None
    r = row.iloc[0]
    record = {"district": r['district'], "station": r['police_station']}
    return _jurisdiction_result(ident, 'case', case_id,
                                auth.in_jurisdiction(r['district'], r['police_station'], ident),
                                record)


def _suspect_jurisdiction(suspect_id):
    """Jurisdiction block for one person, or None if there is no such person."""
    ident = auth.current_identity()
    sus = data_cache['suspects']
    if sus[sus['suspect_id'] == suspect_id].empty:
        return None
    rows = _linked_case_rows(suspect_id)
    hits = sum(auth.in_jurisdiction(d, st, ident)
               for d, st in zip(rows['district'], rows['police_station']))
    record = {"linked_cases": int(len(rows)), "linked_cases_in_scope": int(hits),
              "districts": sorted(rows['district'].dropna().unique().tolist())}
    in_scope = ident["scope"]["level"] == "state" or hits > 0
    return _jurisdiction_result(ident, 'offender', suspect_id, in_scope, record)

@app.route('/serverless/api/anomalies', methods=['GET'])
@audit.audited('read_anomalies')
@auth.require_role('investigator')
def route_anomalies():
    return jsonify_safe({"anomalies": data_cache.get('anomalies', [])})

@app.route('/serverless/api/health', methods=['GET'])
def route_health():
    """Liveness AND readiness.

    This used to report `"status": "online"` whenever the corpus dict had a key,
    so a total ML-layer failure returned 200 here while every model was unavailable
    and the dashboard rendered empty charts. It now inspects what actually loaded
    and returns 503 when the platform is serving but degraded, so a monitor trips
    instead of reading 200 off a broken system.
    """
    payload, status = observability.health_report(data_cache, knowledge_graph)
    payload["llm_connected"] = llm_available()
    # Uptime makes a container restart visible. Without it, the symptom of a
    # recycled container — everyone's token silently invalidated — has to be
    # inferred from the outside, which is how it went unnoticed.
    payload["uptime_seconds"] = int(time.time() - _PROCESS_STARTED)
    payload["auth_secret_configured"] = auth.AUTH_SECRET_CONFIGURED
    if not auth.AUTH_SECRET_CONFIGURED:
        payload.setdefault("warnings", []).append(
            "AUTH_SECRET is not set, so the token signing key is regenerated on "
            "every start. Sessions do not survive a container restart: a user who "
            "logged in before the last restart is treated as anonymous and gets 403 "
            "on person-level pages.")
    return jsonify_safe(payload, status=status)


def _sanitize_history(history, max_turns=10):
    """Keep the last few user/assistant turns for conversational memory."""
    out = []
    for m in (history or [])[-max_turns:]:
        role = m.get('role')
        content = (m.get('content') or '').strip()
        if role in ('user', 'assistant') and content:
            out.append({"role": role, "content": content[:2000]})
    return out


@app.route('/serverless/api/chat', methods=['POST'])
@audit.audited('copilot_chat')
@auth.require_role('investigator')
def route_chat():
    """The long-form copilot. Same engine as /agent, without moving the screen."""
    data = _json_object()
    query = (data.get('query') or '').strip()
    if not query:
        return jsonify_safe({"reply": "Ask me about any case, person, district or model."})
    history = _sanitize_history(data.get('history'))
    refusal = agent_answers.refusal_for(query)
    if refusal:
        return jsonify_safe({"reply": refusal, "grounding": grounding.check(refusal, data_cache)})

    result = None
    if llm_available():
        try:
            result = copilot_engine.llm_answer(
                query, data_cache, LLM_PROVIDERS, history=history,
                lang_directive=_lang_directive(data.get('lang')),
                briefing=build_intelligence_briefing(), page=data.get('page'),
                context=data.get('context'))
        except Exception as e:
            LOG.warning("copilot LLM loop failed: %s", e)
    if result is None:
        result = copilot_engine.offline_answer(query, data_cache, data.get('context'), history)
    if result is None:
        grounded = agent_answers.answer(query, data_cache)
        result = {"reply": grounded["reply"] if grounded else
                  "I could not find that in the records. Here is the current statewide briefing:\n\n"
                  + build_intelligence_briefing()}
    reply = result["reply"]
    # Every identifier the copilot names is checked against the corpus. A fluent
    # sentence citing a case number that does not exist is the failure an officer
    # cannot spot.
    report = grounding.check(reply, data_cache)
    if not report["grounded"]:
        LOG.warning("copilot reply referenced %d unknown identifier(s): %s",
                    len(report["unknown"]),
                    ", ".join(u["value"] for u in report["unknown"][:5]))
        reply = grounding.annotate(reply, report)
    return jsonify_safe({"reply": reply, "grounding": report, "trace": result.get("trace", []),
                         "followups": result.get("followups", []), "mode": result.get("mode")})

# ---------------------------------------------------------------------------
# Dispatch / predictive resourcing
# ---------------------------------------------------------------------------
def get_dispatch():
    rows = data_cache.get('dispatch', [])
    hotspots = data_cache.get('predicted_hotspots', [])
    top = rows[0] if rows else None
    next_zone = None
    if top:
        next_zone = {
            "district": top['district'],
            "risk_score": top['risk_score'],
            "risk_level": top['risk_level'],
            "recommended_officers": top['recommended_officers'],
            "lat": top['lat'],
            "lng": top['lng'],
        }
    return jsonify_safe({
        "districts": rows,
        "predicted_hotspots": hotspots,
        "next_predicted_zone": next_zone,
        "total_recommended_officers": int(sum(r['recommended_officers'] for r in rows)),
    })


# ---------------------------------------------------------------------------
# Conversational agent — multilingual intent + entity extraction → action
# ---------------------------------------------------------------------------
# Words that only ever name a destination. If what survives removing them is empty,
# the user named a page, not a thing to look up inside it.
_INTENT_NOUNS = re.compile(
    r"\b(the|a|an|show|open|me|my|please|view|display|go|to|of|for|all|"
    r"network|graph|link|links|analysis|chart|map|heatmap|hotspot|"
    r"case|cases|directory|registry|recent|latest|list|"
    r"vehicle|vehicles|missing|person|persons|people|cyber|fraud|ring|rings|"
    r"officer|officers|court|courts|dashboard|page|screen|panel)\b",
    re.I)

INTENT_LEXICON = {
    "heatmap": ["heatmap", "heat map", "hotspot", "hot spot", "map of", "show map", "on the map", "on a map",
                "on map", "the map", "3d map", "district map",
                "नक्शा", "मानचित्र", "हीटमैप", "नक़्शा",
                "ನಕ್ಷೆ", "ಹೀಟ್‌ಮ್ಯಾಪ್", "ಹಾಟ್‌ಸ್ಪಾಟ್"],
    "network": ["link", "links", "network", "connection", "connections", "associate",
                "graph", "network graph", "link analysis", "link chart",
                "gang", "criminal link", "नेटवर्क", "संबंध", "लिंक", "गिरोह",
                "ಸಂಪರ್ಕ", "ಜಾಲ", "ಲಿಂಕ್", "ಗ್ಯಾಂಗ್"],
    "dispatch": ["dispatch", "deploy", "police needed", "how many police", "force",
                 "patrol", "reinforcement", "predict", "prediction", "next area",
                 "तैनात", "पुलिस", "भविष्यवाणी", "बल", "गश्त",
                 "ನಿಯೋಜನೆ", "ಪೊಲೀಸ್", "ಮುನ್ಸೂಚನೆ", "ಬಲ", "ಗಸ್ತು"],
    "models": ["model registry", "models page", "model card", "model cards",
               "show models", "open models", "ml models", "how the models",
               "model performance", "model metrics"],
    "recent": ["recent", "latest", "new case", "new cases", "today", "last",
               "recent cases", "case directory", "browse cases", "list cases",
               "हाल", "नया केस", "ताज़ा", "ಇತ್ತೀಚಿನ", "ಹೊಸ ಪ್ರಕರಣ"],
    "reg_vehicles": ["stolen vehicle", "stolen vehicles", "vehicle theft", "vehicle registry",
                     "recovered vehicle", "चोरी की गाड़ी", "वाहन", "ಕಳ್ಳತನ ವಾಹನ", "ವಾಹನ"],
    "reg_missing": ["missing person", "missing people", "missing persons", "गुमशुदा", "लापता",
                    "ಕಾಣೆಯಾದ", "ನಾಪತ್ತೆ"],
    "reg_cyber": ["cyber", "cybercrime", "fraud ring", "fraud rings", "upi", "phishing",
                  "jamtara", "साइबर", "धोखाधड़ी", "ಸೈಬರ್", "ವಂಚನೆ"],
    "reg_officers": ["officer", "officers", "performance", "best officer", "force strength",
                     "अधिकारी", "ಅಧಿಕಾರಿ"],
    "reg_courts": ["court", "chargesheet", "conviction", "convictions", "hearing", "trial",
                   "अदालत", "चार्जशीट", "ನ್ಯಾಯಾಲಯ", "ದೋಷಾರೋಪಣೆ"],
    "analytics": ["forecast", "forecasting", "predict crime", "prediction model", "trend forecast",
                  "next week", "next month", "cluster", "clustering", "hotspot cluster", "recidivism",
                  "risk index", "reoffend", "link prediction", "predicted link", "alliance", "analytics",
                  "ml model", "machine learning", "classifier", "socio-economic", "socioeconomic",
                  "socio economic", "correlation", "unemployment", "literacy", "what drives crime",
                  "poverty", "demographic", "पूर्वानुमान", "भविष्यवाणी मॉडल",
                  "ಮುನ್ಸೂಚನೆ ಮಾದರಿ", "ವಿಶ್ಲೇಷಣೆ"],
    "ai_matching": ["ai match", "ai matching", "suspect matching", "match suspect", "matched suspects",
                   "ai recommended", "recommend suspect", "recommended suspect", "suspect recommendation",
                   "who matches this case", "match accused", "ai suspect", "ai match suspect",
                   "ai matching suspects", "suspects based on ai", "suspect based on ai", "recommendation"],
}

REGISTRY_INTENTS = [
    ("reg_vehicles", "vehicles"), ("reg_missing", "missing"), ("reg_cyber", "cybercrime"),
    ("reg_officers", "officers"), ("reg_courts", "courts"),
]


def _detect_district(text):
    """District named in text, including the names people actually say — "Bangalore",
    "Mysore", "Hubli", "Mangalore". Matching only the official names dropped the
    district from "show the latest chain snatching cases in Bangalore" and opened the
    statewide list instead."""
    idx = copilot_engine._index(data_cache)
    return copilot_engine.detect_district(text, idx) if idx is not None else None


def _has_any(tl, words):
    return any(w in tl for w in words)


CRIME_SYNONYMS = {
    "Chain Snatching": ["chain snatch", "chain-snatch", "chain snatching", "snatching", "chain theft",
                        "mangalsutra", "gold chain", "necklace snatch"],
    "Extortion": ["extortion", "hafta", "protection money", "blackmail", "extort", "ransom demand"],
    "Narcotics Distribution": ["narcotic", "narcotics", "drug", "drugs", "ndps", "peddl", "ganja",
                               "mdma", "cocaine", "substance"],
    "Vehicle Theft": ["vehicle theft", "car theft", "bike theft", "motorcycle theft", "motor theft",
                      "auto theft", "stolen vehicle case", "vehicle lifting"],
    "House Break-in (Night)": ["house break", "housebreak", "burglary", "burglar", "break-in", "break in",
                               "breaking in", "night robbery", "home invasion", "dacoity"],
    "Sandalwood Smuggling": ["sandalwood", "sandal wood", "red sanders", "red sandal", "timber smuggl",
                             "forest smuggl", "wood smuggl"],
    "Cyber Fraud (Jamtara style)": ["cyber fraud", "cyber crime", "cybercrime", "cyber-crime", "online fraud",
                                    "phishing", "jamtara", "upi fraud", "digital fraud", "otp fraud",
                                    "digital arrest", "kyc fraud", "online scam"],
}


SURVEILLANCE_WORDS = ("cctv", "camera feed", "cameras", "camera wall", "anpr", "surveillance", "live feed",
                      "face match", "facial recognition", "plate reader", "video wall")


def _detect_crime_type(text):
    """Match a query to one of the dataset's real crime types via synonyms."""
    df = data_cache.get('firs')
    if df is None:
        return None
    tl = text.lower()
    present = set(df['crime_type'].unique().tolist())
    # Exact crime-type name first, then synonyms — longest match wins.
    best, best_len = None, 0
    for ct in present:
        if ct.lower() in tl and len(ct) > best_len:
            best, best_len = ct, len(ct)
    if best:
        return best
    for ct, syns in CRIME_SYNONYMS.items():
        if ct not in present:
            continue
        for s in syns:
            if s in tl and len(s) > best_len:
                best, best_len = ct, len(s)
    return best


def _resolve_crime_intent(text):
    """'open an extortion case', 'show chain snatching cases', 'latest cyber fraud case' →
    a REAL case of that crime type (not the same fixed case every time)."""
    ct = _detect_crime_type(text)
    if not ct:
        return None
    df = data_cache.get('firs')
    if df is None:
        return None
    tl = text.lower()

    # Pure question about the crime type ("which district has most extortion?") → let the LLM answer.
    if _is_question(text) and not _has_any(tl, ("show", "open", "list", "pull up", "find",
                                                "take me", "display", "view", "give me")):
        return None
    if _has_any(tl, ("how many", "how much", "count", "number of")):
        return None

    d = _detect_district(text)
    plural = ("cases" in tl) or ("list" in tl) or ("all " in tl)
    open_one = _has_any(tl, ("open ", "pull up", "show me a ", "show a ", "find a ", "give me a ",
                             "a case", "one case", "an example", "first case", "worst case",
                             "top case", "single case", "example of"))

    if open_one and not plural:
        pool = df[df['crime_type'] == ct]
        if d:
            dp = pool[pool['district'] == d]
            if not dp.empty:
                pool = dp
        if pool.empty:
            return None
        # Respect a latest/severity qualifier; otherwise open the most serious open case.
        if _has_any(tl, ("recent", "latest", "newest", "last")):
            ts = pd.to_datetime(pool['timestamp'], errors='coerce')
            row = pool.loc[ts.idxmax()]
            qual = "most recent"
        else:
            openpool = pool[pool['status'] != 'Closed']
            use = openpool if not openpool.empty else pool
            row = use.loc[use['severity_score'].idxmax()]
            qual = "highest-severity"
        return {"action": "open_case", "params": {"case_id": str(row['case_id'])},
                "reply": f"Opening {row['case_id']}, the {qual} {ct} case{(' in ' + d) if d else ''} "
                         f"(severity {int(row['severity_score'])}, {row['status']})."}

    # "chain snatching on the map" is a map request, not a list request.
    if _has_any(tl, INTENT_LEXICON["heatmap"]):
        params = {"crime_type": ct, **({"district": d} if d else {})}
        n = int(((df['crime_type'] == ct) & (df['district'] == d)).sum()) if d else int((df['crime_type'] == ct).sum())
        return {"action": "show_heatmap", "params": params,
                "reply": f"Mapping {n:,} {ct} cases{(' in ' + d) if d else ''}."}

    # Otherwise show the filtered list.
    params = {"crime_type": ct}
    if d:
        params["district"] = d
    if _has_any(tl, ("severe", "serious", "worst", "dangerous")):
        params["sort"] = "severity"
    elif _has_any(tl, ("latest", "recent", "newest", "last", "new ")):
        params["sort"] = "recent"
    n = int(((df['crime_type'] == ct) & (df['district'] == d)).sum()) if d else int((df['crime_type'] == ct).sum())
    return {"action": "recent_cases", "params": params,
            "reply": f"Showing {n} {ct} case{'s' if n != 1 else ''}{(' in ' + d) if d else ''}."}


def _resolve_superlative(text):
    """Resolve 'most severe case', 'latest case', 'most wanted suspect' etc. to a concrete
    record id by querying the live data — so a superlative request opens the right entity
    instead of a generic list page."""
    tl = text.lower()
    df = data_cache.get('firs')
    if df is None:
        return None

    severe = ("most severe", "highest severity", "most serious", "worst case", "severe case",
              "most dangerous case", "high severity case", "gravest", "most critical case",
              "biggest case", "top case")
    latest = ("most recent case", "latest case", "newest case", "the last case", "open the last",
              "latest activity", "recent activity", "latest incident", "recent incident",
              "latest one", "recent one", "most recent one", "latest fir", "recent fir",
              "last incident", "latest crime", "recent crime", "last case", "recentmost")
    wanted = ("most wanted", "most connected", "top suspect", "highest risk suspect", "kingpin",
              "most dangerous suspect", "biggest suspect", "prime suspect overall")
    deadly = ("most murder", "most murders", "most death", "most deaths", "deadliest",
              "most fatal", "most killing", "most killings", "highest fatalit", "most homicide",
              "most lives lost")
    hurt = ("most injured", "most injuries", "most casualt", "highest casualt", "bloodiest")
    fraud = ("biggest fraud", "highest fraud", "largest fraud", "biggest scam", "most money stolen",
             "biggest cyber", "largest scam")

    # Only treat as "open a specific record" when the user is asking to open/show/find ONE.
    singular = any(w in tl for w in ("open", "show me the", "go to", "find the", "pull up", "the "))

    if any(w in tl for w in deadly) and 'fatalities' in df.columns:
        row = df.loc[df['fatalities'].fillna(0).idxmax()]
        return {"action": "open_case", "params": {"case_id": str(row['case_id'])}}
    if any(w in tl for w in hurt) and 'fatalities' in df.columns:
        cas = df['fatalities'].fillna(0) + df['injured'].fillna(0)
        return {"action": "open_case", "params": {"case_id": str(df.loc[cas.idxmax(), 'case_id'])}}
    if any(w in tl for w in fraud):
        cyber = data_cache.get('cybercrime')
        if cyber is not None and not cyber.empty:
            row = cyber.loc[cyber['fraud_amount'].fillna(0).idxmax()]
            return {"action": "open_case", "params": {"case_id": str(row['case_id'])}}
    if any(w in tl for w in severe):
        pool = df[df['status'] != 'Closed']
        if pool.empty:
            pool = df
        row = pool.loc[pool['severity_score'].idxmax()]
        return {"action": "open_case", "params": {"case_id": str(row['case_id'])}}
    # Broad latest/recent → open the single most-recent FIR, unless the officer clearly wants a LIST.
    plural = ("cases" in tl) or ("list" in tl) or ("all " in tl)
    latest_hit = any(w in tl for w in latest)
    if not latest_hit and not plural and (("recent" in tl) or ("latest" in tl) or
                                          ("newest" in tl) or ("most recent" in tl)):
        if any(k in tl for k in ("case", "incident", "activity", "fir", "crime", "one")):
            latest_hit = True
    if latest_hit:
        ts = pd.to_datetime(df['timestamp'], errors='coerce')
        idx = ts.idxmax()
        if idx is not None and not (isinstance(idx, float) and math.isnan(idx)):
            return {"action": "open_case", "params": {"case_id": str(df.loc[idx, 'case_id'])}}
    if any(w in tl for w in wanted) and singular:
        top = data_cache.get('top_suspects', [])
        if top:
            return {"action": "open_suspect", "params": {"suspect_id": str(top[0][0])}}
    return None


def normalize_case_id(num_str):
    num_str = str(num_str).strip()
    if num_str.startswith("FIR-"):
        val = num_str[4:]
        df_cm = data_cache.get('CaseMaster')
        if df_cm is not None and not df_cm.empty:
            try:
                cm_id = int(val)
                row = df_cm[df_cm['CaseMasterID'] == cm_id]
                if not row.empty:
                    return str(row.iloc[0]['CrimeNo'])
            except ValueError:
                pass
        return num_str
    if num_str.isdigit() and len(num_str) >= 15:
        # A CrimeNo-shaped string is not automatically a real CrimeNo. This used to
        # return verbatim, which is the one shape that matters: `grounding.PATTERNS`
        # flags `\d{15,20}` as the dangerous hallucination, and the heuristic router
        # matches `\d{15,18}` first — so an invented case number routed straight to
        # open_case and the "no case matching X" guard below never ran.
        df_cm = data_cache.get('firs')
        if df_cm is not None and not df_cm.empty:
            return num_str if (df_cm['case_id'].astype(str) == num_str).any() else None
        return num_str
    df_cm = data_cache.get('CaseMaster')
    if df_cm is not None and not df_cm.empty:
        try:
            cm_id = int(num_str)
            row = df_cm[(df_cm['CaseMasterID'] == cm_id) | (df_cm['CaseNo'] == num_str)]
            if not row.empty:
                return str(row.iloc[0]['CrimeNo'])
        except ValueError:
            pass
    # A short numeric identifier that resolves to nothing is not a case number.
    # Returning it verbatim navigated the UI to a dossier that does not exist —
    # which, before the fabricated fallback was removed, rendered an invented one.
    if num_str.isdigit():
        return None
    return num_str


def normalize_suspect_id(num_str):
    try:
        num = int(num_str)
        if num < 1000:
            num += 1000
        return f"SUS-{num}"
    except ValueError:
        return f"SUS-{num_str}"


def _deterministic_intent(text):
    """Reliable rule-based parse so voice/text commands work even without the LLM."""
    tl = text.lower()

    # Refusals first, before any rule that could act on the query. "arrest SUS-1001"
    # used to match the suspect-id rule and silently open the dossier — answering a
    # request for action by performing a lookup, which is the exact reading
    # GOVERNANCE.md exists to refuse. A refusal that explains itself is part of the
    # control; a silent redirect is not.
    _refusal = agent_answers.refusal_for(text)
    if _refusal:
        return {
            "action": "answer",
            "params": {"refused": True},
            "reply": _refusal,
            "suggestions": [
                {"label": "What this model does and does not claim", "query": "show models"},
                {"label": "Who accessed this record", "query": "show the audit log"},
            ],
        }

    # Then answers that can be computed from the corpus. Every factual question used
    # to fall through to "I could not reach my reasoning core" while the number sat
    # in data_cache; and a grounded count is better than a fluent guess even when a
    # language model is available.
    _grounded = agent_answers.answer(text, data_cache)
    if _grounded:
        return _grounded
    fir_m = (
        re.search(r'\b(\d{15,18})\b', text) or
        re.search(r'fir[\s\-_]*0*(\d{1,6})', text, re.I) or 
        re.search(r'case[\s\-_]*0*(\d{1,6})', text, re.I) or 
        re.search(r'\b(2\d{4})\b', text)
    )
    sus_m = (
        re.search(r'sus[\s\-_]*0*(\d{1,6})', text, re.I) or 
        re.search(r'suspect[\s\-_]*0*(\d{1,6})', text, re.I)
    )

    is_map = _has_any(tl, INTENT_LEXICON["heatmap"])
    is_matching = _has_any(tl, INTENT_LEXICON["ai_matching"])

    if is_matching:
        c_id = normalize_case_id(fir_m.group(1)) if fir_m else None
        target_q = _extract_query_target(text)
        recs = []
        if c_id:
            try:
                recs = analytics.recommend_suspects_for_case(
                    c_id, data_cache.get('firs'), data_cache.get('suspects'), data_cache.get('Accused'), data_cache
                )
            except Exception as e:
                # An empty list here reads as "no candidates", which is a different
                # claim from "the matcher failed". Say which.
                LOG.warning("suspect recommendation failed for %s: %s", c_id, e)
                recs = []
        
        if recs and c_id:
            top_s = recs[0]
            top_name = top_s.get('name', 'Top Candidate')
            top_score = top_s.get('match_score', 88)
            sid = top_s.get('suspect_id', 'SUS-1001')
            reply_text = f"Surfacing AI-matched suspect candidates for {c_id}. Top AI Recommendation: {top_name} ({sid}) with a {top_score}% match score based on criminological MO specialty overlap, spatial proximity, and recidivism risk."
            return {
                "action": "open_case",
                "params": {"case_id": c_id, "suspect_id": sid, "tab": "matching"},
                "reply": reply_text,
                "suggestions": [
                    {"label": f"Deep compare {c_id} with {top_name}", "query": f"deep compare {c_id} with {sid}"},
                    {"label": f"Open profile of {top_name}", "query": f"open {sid}"},
                    {"label": "Show suspect network", "query": f"show network of {top_name}"}
                ]
            }
        else:
            return {
                "action": "show_analytics",
                "params": {"mode": "ai_matching", "q": target_q or c_id or "AI Suspect Matching"},
                "reply": "Opening the AI Case-Suspect Deep Comparison Matrix. Showing criminological specialty correlation, spatial proximity, and recidivism risk indices for all registered offenders.",
                "suggestions": [
                    {"label": "Compare FIR-20345", "query": "compare FIR-20345 with SUS-1001"},
                    {"label": "Recidivism risk ranking", "query": "show recidivism risk scores"},
                    {"label": "DBSCAN crime clusters", "query": "show crime clusters"}
                ]
            }

    if fir_m and not _is_question(text):
        case_id = normalize_case_id(fir_m.group(1))
        if not case_id:
            return {"action": "answer", "params": {},
                    "reply": f"No case matching {fir_m.group(1)} exists in the corpus."}
        if is_map:
            return {"action": "show_heatmap", "params": {"case_id": case_id}}
        return {"action": "open_case", "params": {"case_id": case_id}}
    if sus_m and not _is_question(text):
        suspect_id = normalize_suspect_id(sus_m.group(1))
        return {"action": "open_suspect", "params": {"suspect_id": suspect_id}}


    sup = _resolve_superlative(text)
    if sup:
        return sup
    if _has_any(tl, INTENT_LEXICON["models"]):
        return {"action": "show_models", "params": {},
                "reply": "Opening the model registry — every model, its baselines, "
                         "and the ones it fails to beat."}
    if _has_any(tl, INTENT_LEXICON["analytics"]):
        return {"action": "show_analytics", "params": {}}
    if _has_any(tl, INTENT_LEXICON["dispatch"]):
        d = _detect_district(text)
        return {"action": "show_dispatch", "params": {"district": d} if d else {}}
    if _has_any(tl, SURVEILLANCE_WORDS):
        d = _detect_district(text)
        return {"action": "show_surveillance", "params": {"district": d} if d else {},
                "reply": f"Opening the CCTV & ANPR command{(' for ' + d) if d else ''}."}
    if _has_any(tl, INTENT_LEXICON["heatmap"]):
        d = _detect_district(text)
        ct = _detect_crime_type(text)
        params = {k: v for k, v in (("district", d), ("crime_type", ct)) if v}
        if _has_any(tl, ("3d", "three d", "buildings")):
            params["mode"] = "3d"
        if _has_any(tl, ("district map", "choropleth", "by district", "districts map")):
            params["mode"] = "districts"
        return {"action": "show_heatmap", "params": params}

    # `_heuristic_agent_router` carries the rest of the navigation surface — the link
    # graph, the four registry views, the recent-cases directory, AI suspect matching —
    # and was defined and then never called from anywhere. Every one of those
    # destinations was unreachable by voice or text: "show the network graph" and
    # "recent cases" both fell through to "I could not reach my reasoning core" while
    # a rule that handles them sat a hundred lines further down the file.
    return _heuristic_agent_router(text)


def _extract_query_target(text: str) -> str:
    """Extract query target (person name, keyword, case phrase) from command string."""
    tl = text.lower().strip()
    phrases = [
        "can you show me the network analysis of",
        "show me the network analysis of",
        "network analysis of",
        "network graph of",
        "network of",
        "network for",
        "show me court cases for",
        "show court cases for",
        "show court cases of",
        "court cases for",
        "court cases of",
        "show cases for",
        "show cases of",
        "cases for",
        "cases of",
        "show me cases for",
        "show me cases of",
        "show me",
        "show",
        "open",
    ]
    for p in phrases:
        if tl.startswith(p):
            res = text[len(p):].strip(" .?:!\"'")
            if res and res.lower() not in ["network", "cases", "courts", "registry", "heatmap", "analysis"]:
                return res
    m = re.search(r'(?:of|for|about|on)\s+([A-Za-z0-9\s-]+)$', text, re.IGNORECASE)
    if m:
        t = m.group(1).strip(" .?:!\"'")
        if t and t.lower() not in ["network", "cases", "courts", "heatmap", "registry", "analysis"]:
            return t
    return ""


def _heuristic_agent_router(text: str):
    tl = text.lower()
    fir_m = (
        re.search(r'\b(\d{15,18})\b', text) or
        re.search(r'fir[\s\-_]*0*(\d{1,6})', text, re.I) or 
        re.search(r'case[\s\-_]*0*(\d{1,6})', text, re.I) or 
        re.search(r'\b(2\d{4})\b', text)
    )
    sus_m = (
        re.search(r'\b(SUS-\d{1,6})\b', text, re.I) or
        re.search(r'sus[\s\-_]*0*(\d{1,6})', text, re.I) or 
        re.search(r'suspect[\s\-_]*0*(\d{1,6})', text, re.I)
    )

    is_map = _has_any(tl, INTENT_LEXICON["heatmap"])
    is_matching = _has_any(tl, INTENT_LEXICON["ai_matching"])

    if is_matching:
        c_id = normalize_case_id(fir_m.group(1)) if fir_m else None
        target_q = _extract_query_target(text)
        recs = []
        if c_id:
            try:
                recs = analytics.recommend_suspects_for_case(
                    c_id, data_cache.get('firs'), data_cache.get('suspects'), data_cache.get('Accused'), data_cache
                )
            except Exception as e:
                # An empty list here reads as "no candidates", which is a different
                # claim from "the matcher failed". Say which.
                LOG.warning("suspect recommendation failed for %s: %s", c_id, e)
                recs = []
        
        if recs and c_id:
            top_s = recs[0]
            top_name = top_s.get('name', 'Top Candidate')
            top_score = top_s.get('match_score', 88)
            sid = top_s.get('suspect_id', 'SUS-1001')
            reply_text = f"Surfacing AI-matched suspect candidates for {c_id}. Top AI Recommendation: {top_name} ({sid}) with a {top_score}% match score based on criminological MO specialty overlap, spatial proximity, and recidivism risk."
            return {
                "action": "open_case",
                "params": {"case_id": c_id, "suspect_id": sid, "tab": "matching"},
                "reply": reply_text,
                "suggestions": [
                    {"label": f"Deep compare {c_id} with {top_name}", "query": f"deep compare {c_id} with {sid}"},
                    {"label": f"Open profile of {top_name}", "query": f"open {sid}"},
                    {"label": "Show suspect network", "query": f"show network of {top_name}"}
                ]
            }
        else:
            return {
                "action": "show_analytics",
                "params": {"mode": "ai_matching", "q": target_q or c_id or "AI Suspect Matching"},
                "reply": "Opening the AI Case-Suspect Deep Comparison Matrix. Showing criminological specialty correlation, spatial proximity, and recidivism risk indices for all registered offenders.",
                "suggestions": [
                    {"label": "Compare FIR-20345", "query": "compare FIR-20345 with SUS-1001"},
                    {"label": "Recidivism risk ranking", "query": "show recidivism risk scores"},
                    {"label": "DBSCAN crime clusters", "query": "show crime clusters"}
                ]
            }

    if fir_m and not _is_question(text):
        case_id = normalize_case_id(fir_m.group(1))
        if not case_id:
            return {"action": "answer", "params": {},
                    "reply": f"No case matching {fir_m.group(1)} exists in the corpus."}
        if is_map:
            return {"action": "show_heatmap", "params": {"case_id": case_id}}
        return {"action": "open_case", "params": {"case_id": case_id}}

    if sus_m and not _is_question(text):
        match_txt = sus_m.group(1)
        if match_txt.upper().startswith("SUS-"):
            suspect_id = match_txt.upper()
        else:
            suspect_id = normalize_suspect_id(match_txt)
        return {"action": "open_suspect", "params": {"suspect_id": suspect_id}, "reply": f"Opening suspect dossier for {suspect_id}."}

    # "suspect", "open suspect", "suspect dossier" without an explicit ID -> open top/primary suspect dossier
    is_list_query = any(w in tl for w in ["list", "registry", "all ", "table", "offender list", "suspects list", "repeat offenders"])
    is_suspect_dossier_query = any(phrase in tl for phrase in [
        "suspect dossier", "suspect profile", "open suspect", "show suspect",
        "suspect details", "who is the suspect", "suspect info", "open the suspect"
    ]) or (tl in ["suspect", "suspects"])

    if is_suspect_dossier_query and not is_list_query and not _is_question(text):
        top = data_cache.get('top_suspects', [])
        top_id = str(top[0][0]) if top else "SUS-1001"
        return {"action": "open_suspect", "params": {"suspect_id": top_id}, "reply": f"Opening primary suspect dossier for {top_id}."}

    sup = _resolve_superlative(text)
    if sup:
        return sup
    if _has_any(tl, INTENT_LEXICON["analytics"]):
        return {"action": "show_analytics", "params": {}}
    if _has_any(tl, INTENT_LEXICON["dispatch"]):
        d = _detect_district(text)
        return {"action": "show_dispatch", "params": {"district": d} if d else {}}
    if _has_any(tl, INTENT_LEXICON["heatmap"]):
        d = _detect_district(text)
        return {"action": "show_heatmap", "params": {"district": d} if d else {}}
    
    target_q = _extract_query_target(text)
    # "show the network graph" extracted "the network graph" as the thing to search
    # for, so the page opened with a query matching nothing and rendered an empty
    # graph. A target made only of the words that chose the destination is not a
    # target.
    if target_q and not _INTENT_NOUNS.sub("", target_q).strip(" -,"):
        target_q = None

    # Checked before analytics, because "model registry" also contains words the
    # analytics lexicon claims, and the registry is the more specific destination.
    if _has_any(tl, INTENT_LEXICON["models"]):
        return {"action": "show_models", "params": {},
                "reply": "Opening the model registry — every model, its baselines, "
                         "and the ones it fails to beat."}

    if _has_any(tl, INTENT_LEXICON["network"]):
        params = {}
        if target_q:
            params["q"] = target_q
        return {"action": "show_network", "params": params}
    for lex_key, kind in REGISTRY_INTENTS:
        if _has_any(tl, INTENT_LEXICON[lex_key]):
            params = {"kind": kind}
            if target_q:
                params["q"] = target_q
            return {"action": "show_registry", "params": params}
    if _has_any(tl, INTENT_LEXICON["recent"]):
        params = {}
        if target_q:
            params["q"] = target_q
        return {"action": "recent_cases", "params": params}
    return None


def _fallback_reply(action, params):
    d = params.get("district")
    return {
        "open_case": f"Opening dossier and criminal links for {params.get('case_id', 'the case')}.",
        "open_suspect": f"Opening the suspect profile for {params.get('suspect_id', 'the suspect')}.",
        "show_heatmap": f"Zooming in on {params.get('case_id')} on the map." if params.get('case_id') else f"Showing the crime heatmap{f' for {d}' if d else ''}.",
        "show_network": "Opening the link-analysis network graph.",
        "show_models": "Opening the model registry — every model, its baselines, and "
                       "the ones it fails to beat.",
        "show_dispatch": f"Showing patrol dispatch recommendations{f' for {d}' if d else ''}.",
        "show_analytics": "Opening the predictive analytics & ML models.",
        "show_anomalies": "Surfacing the behavioural anomalies flagged by the model.",
        "open_copilot": "Opening the Investigative Copilot for deep analysis.",
        "recent_cases": "Showing the most recent cases.",
        "show_registry": {
            "vehicles": "Opening the stolen-vehicle registry.",
            "missing": "Opening the missing-persons registry.",
            "cybercrime": "Opening the cybercrime & fraud-ring registry.",
            "officers": "Opening the officer performance registry.",
            "courts": "Opening the court & chargesheet registry.",
        }.get(params.get("kind"), "Opening the intelligence registry."),
        "answer": "",
    }.get(action, "")


# Provider fallback chain: Groq (llama-3.3-70b) is tried FIRST because it answers in
# ~1s — critical for a live, voice-driven demo — with NVIDIA (minimax-m3) as a
# higher-ceiling fallback. Any provider that errors, times out or returns empty is
# skipped so the agent never dead-ends.
LLM_PROVIDERS = [
    {
        "name": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "model_env": "GROQ_MODEL",
        "model_default": "llama-3.3-70b-versatile",
    },
    {
        "name": "nvidia",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "key_env": "NVIDIA_API_KEY",
        "model_env": "NVIDIA_MODEL",
        "model_default": "minimaxai/minimax-m3",
    },
]


def llm_available():
    return any(
        os.environ.get(p["key_env"], "") and not os.environ.get(p["key_env"], "").startswith(("nvapi-****", "gsk_****"))
        for p in LLM_PROVIDERS
    )


def llm_complete(messages, temperature=0.3, max_tokens=1024):
    """Run a chat completion against the first working provider. Returns text or None."""
    try:
        from openai import OpenAI
    except Exception as e:
        LOG.warning(f"openai client unavailable: {e}")
        return None
    for p in LLM_PROVIDERS:
        key = os.environ.get(p["key_env"], "")
        if not key or key.startswith(("nvapi-****", "gsk_****")):
            continue
        try:
            client = OpenAI(base_url=p["base_url"], api_key=key, timeout=30)
            resp = client.chat.completions.create(
                model=os.environ.get(p["model_env"], p["model_default"]),
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content
            if content and content.strip():
                # Names were aliased before egress; the provider never saw a person.
                # Restore them here, locally, so the answer is still usable.
                return privacy.restore(content)
        except Exception as e:
            LOG.warning(f"LLM provider {p['name']} failed: {e}")
            continue
    return None


_QUESTION_CUES = ("?", "how many", "how much", "what", "why", "which", "who", "where", "when",
                  "list", "tell me", "explain", "summar", "compare", "should", "is there",
                  "are there", "kya", "kaun", "kahan", "kyun", "batao",
                  "yaava", "eshtu", "hege", "yake", "yaaru", "elli", "heli",
                  "ಎಷ್ಟು", "ಯಾರು", "ಯಾವ", "ಏನು", "ಎಲ್ಲಿ", "ಏಕೆ", "ಹೇಗೆ")


def _is_question(text):
    tl = text.lower()
    return any(c in tl for c in _QUESTION_CUES)


def _lang_directive(lang):
    name = {"en": "English", "kn": "Kannada"}.get((lang or "").split("-")[0].lower())
    if not name:
        return "Detect the officer's language and reply in that same language."
    return (f"CRITICAL LANGUAGE RULE: The officer's interface language is {name}. Reply ONLY in "
            f"{name}. Do NOT use any other language or script for your prose, even if place names "
            f"are in another script. Keep codes (FIR-xxxxx, SUS-xxxx), numbers and district names as-is.")


# --- Guidance layer -------------------------------------------------------------
# Every capability the agent can route to, with a plain-language description. This
# doubles as (a) the menu the LLM router chooses from and (b) the source for the
# "what can I do here / help" walkthroughs, so the agent can genuinely guide an
# officer through the system instead of only answering trivia.
CAPABILITIES = [
    ("open_case", "Open a full FIR dossier — narrative, ML-matched similar cases, linked suspects, "
                  "court status, vehicles, CCTV and cyber trail (e.g. 'open FIR-20345')."),
    ("open_suspect", "Open a suspect / criminal profile — every linked case, modus operandi, "
                     "associates and network rank (e.g. 'open SUS-1113')."),
    ("show_heatmap", "Open the predictive crime map — incident heat + KDE-predicted hotspots, "
                     "filterable by district (e.g. 'heatmap of Bengaluru Urban')."),
    ("show_network", "Open the link-analysis graph of the most-connected suspects and their cases."),
    ("show_dispatch", "Open patrol dispatch — per-district risk scores and recommended officer strength."),
    ("show_registry", "Open a records registry: stolen vehicles, missing persons, cybercrime/fraud "
                      "rings, officers, or court cases."),
    ("show_anomalies", "Surface behavioural anomalies flagged by the Isolation Forest model."),
    ("recent_cases", "List the most recent cases, filterable and sortable."),
    ("open_copilot", "Open the full Investigative Copilot chat for deep, multi-turn analysis."),
    ("answer", "Answer a question directly using the live intelligence (counts, comparisons, rankings)."),
]

# Per-page onboarding: what this screen is for and the smartest next moves on it.
PAGE_GUIDE = {
    "/overview": ("the Command Overview — headline metrics, emerging trend spikes, behavioural anomalies, crime "
                  "charts and the hour-by-weekday FIR heatmap",
                  ["Which districts are heating up?", "When do chain snatchings happen?", "How many police should we deploy?"]),
    "/fir/new": ("the FIR registration form — station, offence and sections, complainant, victims, accused, brief facts",
                 ["Which section applies to chain snatching?", "Is SUS-1001 in the registry?", "What happens after I register?"]),
    "/news": ("Live News — Karnataka crime and policing headlines, tagged by district and crime type",
              ["What's happening in Bengaluru right now?", "Any narcotics news?", "Compare news with our FIRs"]),
    "/access": ("Account & roles — your role, jurisdiction, the role/permission matrix and the persona switcher",
                ["What can my role do?", "Who can verify CCTV matches?", "Who can register FIRs?"]),
    "/": ("the Command Overview — headline metrics, emerging trend spikes, behavioural anomalies and crime charts",
          ["Which districts are heating up?", "Show behavioural anomalies", "How many police should we deploy?"]),
    "/cases": ("the Case Registry — every FIR, filterable by district, crime type, severity and casualties",
               ["Show the most severe open case", "Cases with casualties", "Recent cases in Bengaluru Urban"]),
    "/map": ("the Predictive Crime Map — incident heat and KDE-predicted hotspots",
             ["Predicted hotspots explained", "Heatmap of Mysuru", "Where should we deploy patrols?"]),
    "/network": ("the Link-Analysis Graph — how suspects, cases and cyber IDs interconnect",
                 ["Who is the most-connected suspect?", "Show the biggest fraud ring", "Open the top suspect"]),
    "/intel": ("the Intelligence Registries — vehicles, missing persons, cyber rings, officers and courts",
               ["Show cyber fraud rings", "Vehicle recovery rate", "Best-performing officers"]),
    "/dispatch": ("the Patrol Dispatch board — district risk scoring and recommended force strength",
                  ["Which district is highest risk?", "Total officers recommended", "Show that district's heatmap"]),
    "/copilot": ("the Investigative Copilot — deep multilingual analysis grounded on the live case graph",
                 ["Summarise the top repeat offenders", "Compare Bengaluru vs Mysuru crime", "Biggest cyber fraud?"]),
}


def _s(label, query):
    return {"label": label, "query": query}


def _suggest_next(action, params, text=""):
    """Propose 2–4 genuinely useful next steps so the agent leads the investigation
    forward instead of dead-ending after every command."""
    d = params.get("district")
    disp = data_cache.get('dispatch', [])
    top_district = disp[0]['district'] if disp else "Bengaluru Urban"

    if action == "open_case":
        cid = params.get("case_id", "this case")
        return [_s("Similar cases (ML)", f"show cases similar to {cid}"),
                _s("Linked suspects", f"who are the suspects in {cid}?"),
                _s("Court status", f"court status of {cid}")]
    if action == "open_suspect":
        sid = params.get("suspect_id", "this suspect")
        return [_s("Their network", "show the suspect network graph"),
                _s("All their cases", f"list every case linked to {sid}"),
                _s("Known associates", f"who are the associates of {sid}?")]
    if action == "show_heatmap":
        where = d or "the state"
        return [_s(f"Deploy patrols in {where}", f"how many police to deploy in {d or top_district}?"),
                _s("Predicted hotspots", "explain the predicted hotspots"),
                _s(f"Recent cases {('in ' + d) if d else ''}".strip(), f"recent cases {('in ' + d) if d else ''}".strip())]
    if action == "show_dispatch":
        return [_s(f"Heatmap of {d or top_district}", f"show heatmap of {d or top_district}"),
                _s("Behavioural anomalies", "show anomalies"),
                _s("Best officers", "show officer performance")]
    if action == "show_registry":
        kind = params.get("kind")
        if kind == "cybercrime":
            return [_s("Biggest fraud ring", "show the largest cyber fraud ring"),
                    _s("Total money lost", "how much money was lost to cyber fraud?"),
                    _s("Open biggest scam", "open the biggest cyber fraud case")]
        if kind == "vehicles":
            return [_s("Recovery rate", "what is the vehicle recovery rate?"),
                    _s("Theft hotspots", "show heatmap of vehicle theft"),
                    _s("Missing persons", "show missing persons")]
        if kind == "officers":
            return [_s("Highest caseload", "which officer has the most cases?"),
                    _s("Dispatch plan", "show patrol dispatch"),
                    _s("Conviction rate", "what is the conviction rate?")]
        return [_s("Cyber fraud rings", "show cyber fraud rings"),
                _s("Missing persons", "show missing persons"),
                _s("Court cases", "show court cases")]
    if action == "show_network":
        return [_s("Top suspect profile", "open the most connected suspect"),
                _s("Biggest fraud ring", "show the largest cyber fraud ring"),
                _s("How to read this", "how do I use the network graph?")]
    if action == "show_anomalies":
        return [_s("Open the top anomaly", "open the most anomalous case"),
                _s("Trend spikes", "which crimes are spiking?"),
                _s("Dispatch plan", "show patrol dispatch")]
    # answer / recent / default → keep the investigation moving
    return [_s(f"Highest-risk district", f"where should we deploy patrols?"),
            _s("Emerging spikes", "which districts are heating up?"),
            _s("Biggest cyber fraud", "show the largest cyber fraud ring")]


def _page_guide(page, lang=None):
    """Deterministic 'what can I do here / help' response for the current screen."""
    desc, starters = PAGE_GUIDE.get(page or "/", PAGE_GUIDE["/"])
    reply = (f"You're on {desc}. I can drive the whole system by voice or text — open any FIR or "
             f"suspect, pull up the predictive map, dispatch plan, registries, network graph or "
             f"anomalies, and answer questions grounded on the live data. Try one of these:")
    return {"action": "guide", "params": {"page": page or "/"}, "reply": reply,
            "suggestions": [_s(s, s) for s in starters]}


def _case_suspects(case_id):
    """Suspect ids linked to a case in the knowledge graph, primary relationships first."""
    if not case_id or not knowledge_graph.has_node(case_id):
        return []
    ranked = []
    for nb in knowledge_graph.neighbors(case_id):
        if knowledge_graph.nodes[nb].get('type') == 'Suspect':
            rel = (knowledge_graph.get_edge_data(case_id, nb) or {}).get('relationship', '')
            # Prefer the accused/primary over mere associates/witnesses.
            rank = 0 if any(w in rel.lower() for w in ('accused', 'prime', 'main', 'suspect')) else 1
            ranked.append((rank, nb, rel))
    ranked.sort(key=lambda x: x[0])
    return [(nb, rel) for _, nb, rel in ranked]


_SUSPECT_WORDS = ("suspect", "accused", "criminal", "culprit", "offender", "perpetrator",
                  "who did", "who committed", "arrested", "gang member", "आरोपी", "संदिग्ध",
                  "अपराधी", "ಆರೋಪಿ", "ಶಂಕಿತ", "ಅಪರಾಧಿ")
_THAT_CASE_WORDS = ("this case", "that case", "the case", "this fir", "that fir", "the fir",
                    "this one", "that one", "it", "its", "here", "current case", "open case",
                    "is case", "इस केस", "उस केस", "ಈ ಪ್ರಕರಣ", "ಆ ಪ್ರಕರಣ")


def _resolve_case_suspect(text, context, history):
    """Map 'open the suspect for that case / for FIR-xxxxx' to the ACTUAL graph-linked
    suspect of that case — instead of falling through to a generic FIR open or the wrong
    suspect. Resolves 'that case' via the current page context or the conversation."""
    tl = text.lower()
    if not any(w in tl for w in _SUSPECT_WORDS):
        return None
    if _has_any(tl, INTENT_LEXICON["ai_matching"]):
        return None

    case_id = None
    m = (
        re.search(r'fir[\s\-_]*0*(\d{1,6})', text, re.I) or 
        re.search(r'case[\s\-_]*0*(\d{1,6})', text, re.I) or 
        re.search(r'\b(2\d{4})\b', text)
    )
    if m:
        case_id = normalize_case_id(m.group(1))
    elif any(w in tl for w in _THAT_CASE_WORDS):
        case_id = (context or {}).get('case_id')
        if not case_id:
            for h in reversed(history or []):
                mm = re.search(r'FIR-\d{3,6}', h.get('content', ''), re.I)
                if mm:
                    case_id = mm.group(0).upper()
                    break
    if not case_id:
        return None

    susp = _case_suspects(case_id)
    if not susp:
        return {"action": "open_case", "params": {"case_id": case_id},
                "reply": f"No suspects are linked to {case_id} in the knowledge graph yet — opening the case dossier instead."}
    sid, rel = susp[0]
    name = knowledge_graph.nodes[sid].get('name', sid)
    extra = f" ({len(susp)} suspects are linked to it — showing the primary one)" if len(susp) > 1 else ""
    rel_txt = f", {rel}" if rel else ""
    return {"action": "open_suspect", "params": {"suspect_id": sid},
            "reply": f"Opening {name} ({sid}{rel_txt}), the suspect linked to {case_id}{extra}."}


_HELP_CUES = ("what can you do", "what can i do", "how do i use", "help me", "guide me",
              "get started", "how do i start", "what can i ask", "how does this work",
              "walk me through", "show me around", "what is this", "how to use",
              "मदद", "कैसे", "क्या कर सकते", "ಸಹಾಯ", "ಹೇಗೆ", "ಏನು ಮಾಡಬಹುದು")


# Maps an 'answer' that clearly implies a destination onto the right navigational
# action, so the agent ACTS on guidance instead of only describing it.
_KIND_TO_REGISTRY = {"vehicles", "missing", "cybercrime", "officers", "courts", "criminals", "complaints"}


def _valid_param(v):
    """Guard against the LLM emitting placeholder values like the string 'null'."""
    return v is not None and str(v).strip().lower() not in ("", "null", "none", "n/a", "undefined")


def _upgrade_answer_to_nav(action, params, is_question=False):
    """If the router said 'answer' but the params point at a concrete screen, promote it
    to the matching navigational action so the UI actually moves there — UNLESS the officer asked a question."""
    if action != "answer" or is_question:
        return action
    if params.get("case_id"):
        return "open_case"
    if params.get("suspect_id"):
        return "open_suspect"
    if params.get("kind") in _KIND_TO_REGISTRY:
        return "show_registry"
    return action


def run_agent(query, history=None, lang=None, page=None, context=None):
    result = _run_agent(query, history, lang, page, context)
    if result.get("reply") and not result.get("speech"):
        result["speech"] = copilot_engine.speech_summary(result["reply"])
    original = (query or "").strip()
    if result.get("transcript") and result["transcript"] != original:
        result["interpreted_as"] = result["transcript"]
    result["transcript"] = original
    return result


def _json_object():
    """The request body if it is a JSON object, else {} — a list or a bare string
    must be a 400 from the handler's own validation, never a 500 on `.get`."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _copilot_cache():
    """The corpus as the caller's jurisdiction lets Kavya see it.

    A statewide role reads the whole cache. A district or station role gets a view
    whose FIR frame is cut to its jurisdiction — the same rule /cases enforces — and
    whose CCTV alerts are pinned to its district, so the copilot cannot be used to
    read around the case list. The view is memoised per scope and per loaded corpus,
    so its copilot index is built once.
    """
    from flask import has_request_context
    if not has_request_context():
        return data_cache      # an internal call (warm-up, tests): no caller, no scope
    scope = auth.current_identity().get("scope") or {}
    level = scope.get("level")
    firs = data_cache.get("firs")
    if level not in ("district", "station") or firs is None:
        return data_cache
    key = (level, scope.get("district"), scope.get("station"), id(firs), len(firs))
    views = data_cache.setdefault("_scoped_views", {})
    view = views.get(key)
    if view is None:
        mask = firs["district"] == scope.get("district")
        if level == "station":
            mask &= firs["police_station"] == scope.get("station")
        view = {k: v for k, v in data_cache.items() if k not in ("_scoped_views", "_copilot_index")}
        view["firs"] = firs[mask].reset_index(drop=True)
        view["_scope_district"] = scope.get("district")
        view["_scope_label"] = scope.get("station") or scope.get("district")
        for stale in [k for k in views if k[3] != id(firs) or k[4] != len(firs)]:
            views.pop(stale, None)
        views[key] = view
    return view


def _run_agent(query, history=None, lang=None, page=None, context=None):
    cache = _copilot_cache()
    text = (query or "").strip()
    if not text:
        return {"action": "answer", "params": {}, "reply": "How can I assist your investigation?",
                "suggestions": _suggest_next("answer", {})}

    tl = text.lower().rstrip("?.,!")
    greetings = ("hi", "hello", "hey", "namaste", "good morning", "good afternoon", "good evening",
                 "namaskara", "namaskar", "नमस्ते", "नमस्कार", "ನಮಸ್ತೆ", "ನಮಸ್ಕಾರ")
    if tl in greetings or any(w == tl for w in greetings):
        reply = ("Namaste! I am Kavya, your KSP investigative copilot. I can read any FIR, offender, officer or "
                 "registry record, rank and count crime anywhere in Karnataka, trace links between people and "
                 "explain every forecast and risk model. What are we working on?")
        if lang and lang.startswith("hi"):
            reply = "नमस्ते! मैं काव्या हूँ, आपकी केएसपी एआई खोजी सहायक। आज मैं आपकी क्या मदद कर सकती हूँ?"
        elif lang and lang.startswith("kn"):
            reply = "ನಮಸ್ತೆ! ನಾನು ಕಾವ್ಯಾ, ನಿಮ್ಮ ಕೆಎಸ್‌ಪಿ ಎಐ ತನಿಖಾ ಸಹಾಯಕ. ಇಂದು ನಾನು ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಬಹುದು?"
        return {"action": "answer", "params": {}, "reply": reply,
                "suggestions": [_s("Brief me on the state", "give me a crime overview"),
                                _s("Emerging trend alerts", "which crimes are spiking?"),
                                _s("Who is the top offender?", "who is SUS-8286?")]}

    # A follow-up that leans on the conversation ("what about Mysuru?", "show them on the
    # map") becomes a self-contained request before anything routes it.
    resolved = copilot_engine.rewrite_followup(text, history, copilot_engine._index(cache))
    if resolved != text:
        LOG.info("follow-up resolved: %r -> %r", text, resolved)
        text = resolved
    tl = text.lower()
    # "What can I do here / help / guide me" → deterministic, page-aware walkthrough.
    if _has_any(tl, _HELP_CUES) and len(text) < 60 and not re.search(
            r"project|platform|intelhub|about|datathon|built|model|data|stack|governance", tl):
        g = _page_guide(page, lang)
        g["transcript"] = text
        return g

    # "open the suspect for that case / for FIR-xxxxx" → resolve to the real linked suspect.
    cs = None if _is_question(text) else _resolve_case_suspect(text, context, history)
    if cs:
        cs["suggestions"] = _suggest_next(cs["action"], cs.get("params", {}))
        cs["transcript"] = text
        return cs

    # "open an extortion case", "show chain-snatching cases" → a REAL case/list of that crime type.
    # "forecast chain snatching in Bengaluru", "busiest day for murders in Mysuru" name a
    # crime type but ask for numbers; they must reach the engine, not a case list.
    analytic = copilot_engine.is_analytic_question(text) or bool(
        copilot_engine.NEWS_CUE.search(text.lower()) and not copilot_knowledge.is_platform_question(text))
    crime = None if analytic else _resolve_crime_intent(text)
    if crime and cache is not data_cache:
        # The list the officer lands on is cut to their jurisdiction by /cases; the
        # spoken count must be the same number, not the statewide one.
        prm = crime.get("params") or {}
        f = cache["firs"]
        if prm.get("crime_type"):
            f = f[f["crime_type"] == prm["crime_type"]]
        where = cache.get("_scope_district")
        asked = prm.get("district")
        crime["reply"] = (f"Showing {len(f)} {prm.get('crime_type') or ''} cases in your jurisdiction"
                          f" ({where})." + (f" {asked} is outside it, so its cases are not listed."
                                            if asked and asked != where else "")).replace("  ", " ")
    if crime:
        crime["suggestions"] = _suggest_next(crime["action"], crime.get("params", {}))
        crime["transcript"] = text
        return crime

    det = _deterministic_intent(text)
    question = _is_question(text)

    # A refusal is the platform's stated position, not a phrasing choice.
    if det and (det.get("params") or {}).get("refused"):
        return _agent_response(det["action"], det.get("params", {}), det.get("reply"),
                               det.get("suggestions"), text)

    # A navigation command ("open FIR-20345", "show heatmap of Mysuru"): go there at
    # once, and say something worth hearing about what is on that screen rather than
    # a bare "Opening dossier".
    if det and not question and not analytic and det["action"] != "answer":
        action = det["action"]
        params = dict(det.get("params", {}))
        if action in ("show_heatmap", "show_dispatch", "recent_cases") and not params.get("district") \
                and re.search(r"\b(that|same|this) (district|area|region|place)\b|\bthere\b", tl):
            for h in reversed(history or []):
                d = _detect_district(str(h.get("content") or ""))
                if d:
                    params["district"] = d
                    break
        reply = _nav_brief(action, params) or det.get("reply") or _fallback_reply(action, params)
        return _agent_response(action, params, reply, det.get("suggestions") or _suggest_next(action, params, text),
                               text)

    # Everything else is a question, and questions are answered from the records.
    grounded = det if det and det.get("grounding") else None
    if grounded and (grounded.get("grounding") or {}).get("names_withheld"):
        # A ranked list of people stays in the gated registry (agent_answers._watchlist).
        return _agent_response(grounded["action"], grounded.get("params", {}), grounded["reply"],
                               grounded.get("suggestions"), text, extra={"grounding_source": grounded["grounding"]})

    result = None
    if llm_available():
        try:
            result = copilot_engine.llm_answer(
                text, cache, LLM_PROVIDERS, history=_sanitize_history(history, 8),
                lang_directive=_lang_directive(lang), briefing=build_intelligence_briefing(),
                page=page, context=context, extra_facts=grounded["reply"] if grounded else None)
        except Exception as e:
            LOG.warning("copilot LLM loop failed, answering deterministically: %s", e)
            result = None
    if result is None:
        offline = copilot_engine.offline_answer(text, cache, context, history)
        if grounded and offline is None:
            # A computed answer from agent_answers already says exactly this.
            result = {"reply": grounded["reply"], "trace": [], "nav": None, "mode": "deterministic",
                      "suggestions": grounded.get("suggestions")}
        else:
            result = offline

    if result is None:
        if det and (not question or det["action"] in _ANSWERABLE_NAV):
            action, params = det["action"], det.get("params", {})
            return _agent_response(action, params, det.get("reply") or _fallback_reply(action, params),
                                   det.get("suggestions") or _suggest_next(action, params, text), text)
        # Not an outage. Nothing matched because the question is outside what this
        # system holds; saying "I could not reach my reasoning core" teaches a user to
        # distrust the tool exactly when it is behaving correctly.
        # Never dead-end. Say plainly what is not in the records, then give the officer
        # something useful: the live statewide picture.
        brief = copilot_engine.compose([(("situation", {}), copilot_engine.tool_situation(cache))])
        return _agent_response("answer", {}, (
            "I don't have that in the KSP records or the camera network, and without a connected "
            "language model I can't answer general questions. Here is what is happening across "
            "Karnataka right now:\n\n" + brief),
            _suggest_next("answer", {}, text), text,
            extra={"trace": [copilot_engine._trace("situation", {}, {})], "mode": "deterministic"})

    nav = result.get("nav") or {}
    if not nav and det and det["action"] in _ANSWERABLE_NAV and not analytic:
        nav = {"action": det["action"], "params": det.get("params", {})}
    action = nav.get("action") or "answer"
    params = {k: v for k, v in (nav.get("params") or {}).items() if _valid_param(v)}
    # A question about a record answers it in place; it moves the screen only when the
    # question is about one record, so asking "how many" never yanks the officer away.
    if action in ("open_case", "open_suspect") and not _single_entity(result):
        action, params = "answer", {}

    suggestions = [_s(u[:48], u) for u in (result.get("followups") or []) if isinstance(u, str) and u.strip()]
    suggestions = suggestions or result.get("suggestions") or _engine_suggestions(result, text) \
        or _suggest_next(action, params, text)
    return _agent_response(action, params, result["reply"], suggestions, text,
                           extra={"trace": result.get("trace", []), "mode": result.get("mode")})


# Screens that are a fair answer to a question the engine could not answer in words
# ("where are the hotspots?"). A recent-cases list is not: "what is the weather today"
# matched "today" and used to answer with the case directory.
_ANSWERABLE_NAV = {"show_heatmap", "show_network", "show_dispatch", "show_analytics", "show_models",
                   "show_registry", "show_anomalies", "open_case", "open_suspect"}


def _single_entity(result):
    ents = result.get("entities") or {}
    return len(ents.get("cases") or []) + len(ents.get("suspects") or []) == 1


def _engine_suggestions(result, text=""):
    """Follow-ups that continue from the record the answer was about — minus the one
    just asked, which would only repeat the answer."""
    asked = (text or "").lower()
    picks = _engine_suggestion_pool(result)
    keys = {"accused": ("accused", "suspect"), "similar": ("similar", "mo "), "court": ("court", "trial"),
            "co-offenders": ("co-offender", "associate", "partner"), "network": ("network", "connect"),
            "warrant": ("warrant",), "forecast": ("forecast",), "pendency": ("pendency",), "map": ("map", "heatmap"),
            "station": ("station",), "time": ("time of day", "when"), "year": ("year",)}
    def _repeats(s):
        q = s["query"].lower()
        return any(k in q and any(w in asked for w in ws) for k, ws in keys.items())
    return [s for s in picks if not _repeats(s)][:3] or picks[:3]


def _engine_suggestion_pool(result):
    ents = result.get("entities") or {}
    if ents.get("cases"):
        cid = ents["cases"][0]
        return [_s("Who are the accused?", f"who are the accused in {cid}?"),
                _s("Similar MO cases", f"which cases are similar to {cid}?"),
                _s("Court status", f"what is the court status of {cid}?"),
                _s("Who investigates it?", f"who is the investigating officer of {cid}?")]
    if ents.get("suspects"):
        sid = ents["suspects"][0]
        return [_s("Co-offenders", f"who are the co-offenders of {sid}?"),
                _s("Network graph", f"show network of {sid}"),
                _s("Does a warrant exist?", f"does {sid} have an active warrant?"),
                _s("All their cases", f"list every case of {sid}")]
    d = ents.get("district")
    if d:
        return [_s(f"Forecast for {d}", f"what is the forecast for {d}?"),
                _s(f"Pendency in {d}", f"what is the pendency situation in {d}?"),
                _s(f"Map {d}", f"show heatmap of {d}")]
    ct = ents.get("crime_type")
    if ct:
        return [_s("Worst stations", f"which police station has the most {ct}?"),
                _s("Time of day", f"what time of day does {ct} happen?"),
                _s("Year by year", f"how many {ct} cases per year?")]
    return []


def _nav_brief(action, params):
    """One or two sentences about what the screen being opened holds."""
    try:
        if action == "open_case" and params.get("case_id"):
            c = copilot_engine.tool_get_case(data_cache, case_ref=params["case_id"])
            if c.get("error"):
                return None
            acc = c.get("accused") or []
            who = ", ".join(f"{a.get('name')} ({a['suspect_id']})" for a in acc[:3])
            court = (c.get("court") or {}).get("conviction_status")
            return (f"Opening FIR {c['case_id']}: {c.get('crime_type')} at {c.get('police_station')}, "
                    f"{c.get('district')} — {c.get('status')}, severity {c.get('severity_score')}."
                    + (f" {len(acc)} accused: {who}{'…' if len(acc) > 3 else ''}." if acc else " No accused linked yet.")
                    + (f" Court: {court}." if court else ""))
        if action == "open_suspect" and params.get("suspect_id"):
            p = copilot_engine.tool_get_person(data_cache, person_ref=params["suspect_id"])
            if p.get("error") or p.get("ambiguous") or not p.get("suspect_id"):
                return None
            flags = [f for f, on in (("rowdy-sheeter", p.get("rowdy_sheeter")),
                                     ("active warrant", p.get("active_warrant"))) if on]
            return (f"Opening {p.get('name')} ({p['suspect_id']}): {p.get('risk_level') or 'unrated'} risk, "
                    f"named in {p.get('cases_in_corpus', 0)} case(s), {len(p.get('co_offenders') or [])} co-offender(s)"
                    + (f", {', '.join(flags)}" if flags else "") + ".")
        if action in ("show_heatmap", "recent_cases", "show_dispatch") and (params.get("district") or params.get("crime_type")):
            r = copilot_engine.tool_search_cases(data_cache, district=params.get("district"),
                                                 crime_type=params.get("crime_type"), limit=1)
            n = r.get("total_matching", 0)
            scope = " ".join(x for x in (params.get("crime_type"), "cases", ("in " + params["district"]) if params.get("district") else None) if x)
            top = next(iter((r.get("by_crime_type") or {}).items()), None)
            label = {"show_heatmap": "Mapping", "recent_cases": "Listing", "show_dispatch": "Dispatch view for"}[action]
            return (f"{label} {n:,} {scope}" + (f"; {r['by_status'].get('Under Investigation', 0):,} still under investigation" if r.get("by_status") else "")
                    + (f"; most common is {top[0]} ({top[1]:,})" if top else "") + ".")
    except Exception as e:
        LOG.warning("navigation brief failed for %s: %s", action, e)
    return None


def _agent_response(action, params, reply, suggestions, text, extra=None):
    """Validate, verify and package an agent reply — the single exit for run_agent."""
    reply = reply or _fallback_reply(action, params or {}) or "Done."
    # The client switches on `action`; an unrecognised value silently does nothing
    # there, so validate rather than rely on that accident.
    action = grounding.safe_action(action)
    # Verify every identifier the reply names. An invented case number in a fluent
    # sentence is the failure an operator cannot spot.
    report = grounding.check(reply, data_cache)
    if not report["grounded"]:
        LOG.warning("agent reply referenced %d unknown identifier(s): %s",
                    len(report["unknown"]),
                    ", ".join(u["value"] for u in report["unknown"][:5]))
        reply = grounding.annotate(reply, report)
    norm = []
    for s in (suggestions or [])[:4]:
        if isinstance(s, dict) and s.get("query"):
            norm.append(_s(str(s.get("label") or s["query"])[:48], str(s["query"])))
        elif isinstance(s, str) and s.strip():
            norm.append(_s(s.strip()[:48], s.strip()))
    out = {"action": action, "params": params or {}, "reply": reply,
           "speech": copilot_engine.speech_summary(reply),
           "suggestions": norm, "transcript": text, "grounding": report}
    out.update(extra or {})
    return out


# ---------------------------------------------------------------------------
# Map data
# ---------------------------------------------------------------------------
@app.route('/serverless/api/map/incidents', methods=['GET'])
def route_map_incidents():
    """Every FIR as a compact row for the map: position, severity and time, plus the
    case id the heatmap already exposes. Columnar so 18,000 rows stay small."""
    cached = data_cache.get('_map_incidents')
    if cached is None:
        f = data_cache['firs']
        ts = pd.to_datetime(f['timestamp'], errors='coerce')
        crimes = sorted(f['crime_type'].dropna().unique().tolist())
        dists = sorted(f['district'].dropna().unique().tolist())
        stations = sorted(f['police_station'].dropna().astype(str).unique().tolist())
        statuses = sorted(f['status'].dropna().unique().tolist())
        ci = {c: i for i, c in enumerate(crimes)}
        di = {d: i for i, d in enumerate(dists)}
        si = {d: i for i, d in enumerate(stations)}
        ti = {d: i for i, d in enumerate(statuses)}
        rows = [[round(float(la), 5), round(float(lo), 5), int(sv), ci.get(c, -1), di.get(d, -1),
                 si.get(str(ps), -1), ti.get(st, -1),
                 int(t.hour) if pd.notna(t) else -1, int(t.year) if pd.notna(t) else -1,
                 int(t.month) if pd.notna(t) else -1, int(fa or 0), str(cid)]
                for la, lo, sv, c, d, ps, st, t, fa, cid in zip(
                    f['latitude'], f['longitude'], f['severity_score'], f['crime_type'], f['district'],
                    f['police_station'], f['status'], ts, f['fatalities'].fillna(0), f['case_id'])
                if pd.notna(la) and pd.notna(lo)]
        cached = {"columns": ["lat", "lng", "severity", "crime", "district", "station", "status",
                              "hour", "year", "month", "fatalities", "case_id"],
                  "crime_types": crimes, "districts": dists, "stations": stations, "statuses": statuses,
                  "rows": rows}
        data_cache['_map_incidents'] = cached
    return jsonify_safe(cached)


@app.route('/serverless/api/map/districts', methods=['GET'])
def route_map_districts():
    """Per-district measures for the choropleth: volume, rate, openness, harm, trend,
    forecast, risk index and camera coverage."""
    import karnataka as KA
    f = data_cache['firs']
    ts = pd.to_datetime(f['timestamp'], errors='coerce')
    end = ts.max()
    recent = f[ts > end - pd.Timedelta(days=30)]
    base = f[(ts <= end - pd.Timedelta(days=30)) & (ts > end - pd.Timedelta(days=395))]
    fc = (data_cache.get('forecast') or {}).get('by_district', {})
    oi = {r['district']: r for r in (data_cache.get('operational_index') or {}).get('districts', [])}
    disp = {r['district']: r for r in data_cache.get('dispatch', [])}
    cams = surveillance.cameras(data_cache)['summary']['by_district']
    rows = []
    for d, g in f.groupby('district'):
        pop = KA.population(d)
        n = len(g)
        rec_n = int((recent['district'] == d).sum())
        exp = (base['district'] == d).sum() / 365 * 30
        top = g['crime_type'].value_counts()
        rows.append({
            "district": d, "cases": n,
            # population() is in lakhs, and one lakh is 100,000: cases per lakh per year.
            "per_100k": round(n / 7 / pop, 1) if pop else None,
            "population_lakh": pop,
            "open": int((g['status'] == 'Under Investigation').sum()),
            "open_pct": round(float((g['status'] == 'Under Investigation').mean() * 100), 1),
            "fatalities": int(g['fatalities'].fillna(0).sum()),
            "avg_severity": round(float(g['severity_score'].mean()), 1),
            "top_crime": top.index[0] if len(top) else None,
            "last_30d": rec_n, "expected_30d": round(float(exp), 1),
            "trend_ratio": round(float(rec_n / exp), 2) if exp else None,
            "forecast_next_week": (fc.get(d) or {}).get('next_week'),
            "forecast_trend": (fc.get(d) or {}).get('trend'),
            "risk_index": (oi.get(d) or {}).get('index'), "risk_band": (oi.get(d) or {}).get('band'),
            "recommended_officers": (disp.get(d) or {}).get('recommended_officers'),
            "cameras": cams.get(d, 0),
        })
    rows.sort(key=lambda r: -r['cases'])
    return jsonify_safe({"districts": rows, "extract_date": str(end)[:10],
                         "note": "Per-100k is annual average over the 7-year corpus, on 2011 Census population."})


# ---------------------------------------------------------------------------
# Copilot workspace: saved conversations per officer
# ---------------------------------------------------------------------------
@app.route('/serverless/api/copilot/conversations', methods=['GET', 'POST', 'OPTIONS'])
@audit.audited('copilot_conversations')
@auth.require_role('investigator')
def route_conversations():
    if request.method == 'OPTIONS':
        return '', 204
    if request.method == 'POST':
        data = _json_object()
        return jsonify_safe(conversations.create(_actor(), data.get('title')), status=201)
    return jsonify_safe({"conversations": conversations.list_for(_actor(), request.args.get('q'))})


@app.route('/serverless/api/copilot/conversations/<cid>', methods=['GET', 'POST', 'OPTIONS'])
@audit.audited('copilot_conversation', subject_type='conversation', subject_arg='cid')
@auth.require_role('investigator')
def route_conversation(cid):
    """GET reads; POST {title?, pinned?} edits; POST {delete: true} removes. GET and POST
    only, because the gateway's CORS policy is not guaranteed to admit PATCH/DELETE."""
    if request.method == 'OPTIONS':
        return '', 204
    if request.method == 'POST':
        data = _json_object()
        if data.get('delete'):
            ok = conversations.delete(_actor(), cid)
            return jsonify_safe({"deleted": ok}, status=200 if ok else 404)
        c = conversations.update(_actor(), cid, data.get('title'), data.get('pinned'))
    else:
        c = conversations.get(_actor(), cid)
    return jsonify_safe(c) if c else jsonify_safe({"error": "no such conversation"}, status=404)


@app.route('/serverless/api/copilot/conversations/<cid>/messages', methods=['POST', 'OPTIONS'])
@audit.audited('copilot_chat', subject_type='conversation', subject_arg='cid')
@auth.require_role('investigator')
def route_conversation_message(cid):
    """Ask within a saved conversation: its own history is the context."""
    if request.method == 'OPTIONS':
        return '', 204
    data = _json_object()
    text = (data.get('content') or '').strip()
    if not text:
        return jsonify_safe({"error": "empty message"}, status=400)
    c = conversations.get(_actor(), cid)
    if not c:
        return jsonify_safe({"error": "no such conversation"}, status=404)
    result = run_agent(text, conversations.history(c), data.get('lang'), page='/copilot',
                       context=data.get('context'))
    c = conversations.append(_actor(), cid, text, result)
    return jsonify_safe({"message": c["messages"][-1], "conversation": conversations._summary(c),
                         "speech": result.get("speech")})


# ---------------------------------------------------------------------------
# CCTV & ANPR surveillance
# ---------------------------------------------------------------------------
def _actor():
    return auth.current_identity().get("user") or "anonymous"


@app.route('/serverless/api/surveillance/cameras', methods=['GET'])
def route_surv_cameras():
    """Camera positions and health. No person-level data, so open like the heatmap."""
    a = request.args
    return jsonify_safe(surveillance.cameras(data_cache, a.get('district'), a.get('type'), a.get('status')))


@app.route('/serverless/api/surveillance/camera/<camera_id>', methods=['GET'])
@audit.audited('read_camera_matches', subject_type='camera', subject_arg='camera_id')
@auth.require_role('investigator')
def route_surv_camera(camera_id):
    out = surveillance.camera_detail(data_cache, _actor(), camera_id)
    return jsonify_safe(out) if out else (jsonify_safe({"error": "no such camera"}, status=404))


# A camera district no real camera has. Passed when a scoped officer asks for a
# district outside their jurisdiction, so the filter matches nothing and every count
# in the response is computed over that empty set — rather than silently swapping in
# their own district and returning numbers for a place they did not ask about.
_OUTSIDE_JURISDICTION = "\x00outside-jurisdiction"


def _surveillance_district(ident, requested):
    """District filter for the camera feed and alert queue. Cameras carry a district,
    not a station, so a station officer sees their station's district."""
    scope = ident["scope"]
    if scope["level"] == "state":
        return requested
    if requested and str(requested).strip().lower() != scope["district"].lower():
        return _OUTSIDE_JURISDICTION
    return scope["district"]


@app.route('/serverless/api/surveillance/feed', methods=['GET'])
@audit.audited('read_surveillance_feed')
@auth.require_permission('surveillance_view')
def route_surv_feed():
    a = request.args
    ident = auth.current_identity()
    out = surveillance.feed(
        data_cache, _actor(), since=a.get('since', type=float), camera_id=a.get('camera'),
        kind=a.get('kind'), matches_only=a.get('matches_only') in ('1', 'true'),
        limit=a.get('limit', default=80, type=int),
        district=_surveillance_district(ident, a.get('district')))
    out["scope"] = _scope_block(ident, a.get('district'))
    return jsonify_safe(out)


@app.route('/serverless/api/surveillance/alerts', methods=['GET'])
@audit.audited('read_surveillance_alerts')
@auth.require_permission('surveillance_view')
def route_surv_alerts():
    a = request.args
    ident = auth.current_identity()
    out = surveillance.alerts(
        data_cache, _actor(), since=a.get('since', type=float), status=a.get('status'),
        priority=a.get('priority'), district=_surveillance_district(ident, a.get('district')),
        limit=a.get('limit', default=100, type=int))
    out["scope"] = _scope_block(ident, a.get('district'))
    return jsonify_safe(out)


# Which permission each decision needs. Acknowledging only says "seen"; verifying or
# rejecting asserts something about an identification, and escalating moves it up the
# chain, so those are held to the ranks that carry that responsibility. Reopen undoes
# a verify/reject and is held to the same bar. An action not listed here falls
# through to surveillance.decide, which answers 400 for it.
_DECISION_PERMISSION = {
    "acknowledge": "surveillance_view",
    "verify": "surveillance_decide",
    "reject": "surveillance_decide",
    "reopen": "surveillance_decide",
    "escalate": "surveillance_escalate",
}


def _sighting_district(event_id):
    """District of the camera that produced a sighting, or None if there is no such
    (past) sighting — in which case surveillance.decide gives the 404."""
    surveillance.ensure(data_cache)
    e = surveillance._find_event(event_id)
    if not e or e["ts"] > time.time():
        return None
    st = surveillance._state
    return st["cameras"][st["cam_index"][e["camera_id"]]]["district"]


@app.route('/serverless/api/surveillance/alerts/<event_id>', methods=['POST', 'OPTIONS'])
@audit.audited('decide_surveillance_alert', subject_type='sighting', subject_arg='event_id')
@auth.require_permission('surveillance_view')
def route_surv_decide(event_id):
    if request.method == 'OPTIONS':
        return '', 204
    data = _json_object()
    action = data.get('action')
    if not isinstance(action, str):
        # A list or object here would otherwise reach a dict lookup and 500.
        return jsonify_safe({"error": "action must be one of "
                                      + str(sorted(_DECISION_PERMISSION))}), 400
    ident = auth.current_identity()
    needed = _DECISION_PERMISSION.get(action)
    # Refusals are returned as (body, status) tuples: the audit wrapper reads the
    # status from the tuple, so a refused decision is logged as denied, not ok.
    if needed and not auth.has_permission(needed, ident):
        return jsonify_safe(dict(auth.forbidden_body(needed, ident), action=action)), 403
    if needed and ident["scope"]["level"] != "state":
        district = _sighting_district(event_id)
        if district is not None and district != ident["scope"]["district"]:
            # Reading across a boundary is allowed and audited; acting on another
            # district's alert is not — that district's officers own the response.
            return jsonify_safe({
                "error": "outside_jurisdiction",
                "alert_district": district,
                "scope": dict(ident["scope"]),
                "hint": "Decisions on an alert belong to the officers of its district.",
            }), 403
    out, status = surveillance.decide(data_cache, _actor(), event_id, action, data.get('note'))
    return jsonify_safe(out), status


@app.route('/serverless/api/surveillance/track/<suspect_id>', methods=['GET'])
@audit.audited('track_person', subject_type='offender', subject_arg='suspect_id')
@auth.require_role('investigator')
def route_surv_track(suspect_id):
    out = surveillance.track(data_cache, _actor(), suspect_id.upper(),
                             hours=min(24, request.args.get('hours', default=24, type=int)))
    return jsonify_safe(out) if out else jsonify_safe({"error": "no such person"}, status=404)


@app.route('/serverless/api/surveillance/plate', methods=['GET'])
@audit.audited('anpr_lookup')
@auth.require_role('investigator')
def route_surv_plate():
    return jsonify_safe(surveillance.lookup_plate(data_cache, request.args.get('q', '')))


@app.route('/serverless/api/surveillance/watchlist', methods=['GET', 'POST', 'OPTIONS'])
@audit.audited('surveillance_watchlist')
@auth.require_role('investigator')
def route_surv_watchlist():
    if request.method == 'OPTIONS':
        return '', 204
    data = (_json_object()) if request.method == 'POST' else {}
    out, status = surveillance.watchlist(data_cache, _actor(), add=data.get('add'), remove=data.get('remove'))
    return jsonify_safe(out, status=status)


@app.route('/serverless/api/surveillance/model', methods=['GET'])
def route_surv_model():
    """The matcher's card and measured error rates. Aggregate, so open."""
    return jsonify_safe(surveillance.model_card(data_cache))


@app.route('/serverless/api/dispatch', methods=['GET'])
def route_dispatch():
    return get_dispatch()


REGISTRY_MAP = {
    "vehicles": "vehicles", "missing": "missing", "officers": "officers",
    "courts": "courts", "cybercrime": "cybercrime", "complaints": "complaints",
    "criminals": "criminals", "cctv": "cctv",
}


def _clean_id(val):
    if pd.isna(val) or val is None:
        return ""
    s = str(val).strip()
    if s.endswith('.0'):
        s = s[:-2]
    return s


def _is_true(val):
    if isinstance(val, bool):
        return val
    if not val:
        return False
    s = str(val).strip().lower()
    return s in ('true', '1', 'yes', 't', 'y')


def _parse_int(val, default=0):
    try:
        if pd.isna(val) or val is None or val == "":
            return default
        return int(float(val))
    except Exception:
        return default


def _build_suspect_case_metadata():
    """Per-offender crime-type and violence summary, built once at load.

    This was computed *per request* on /registry/criminals: an `.iterrows()` over
    every FIR calling `.to_dict()` on each, then another over every offender-case
    link. Measured at 920 ms on 18,000 cases and 28,355 links, recomputed from
    scratch on every call and never cached — about nine seconds at ten times the
    corpus, on a single-worker container.

    It is also fully determined by the corpus, so there was never a reason to
    recompute it. The vectorised form below is a merge and two groupbys.
    """
    df_links = data_cache.get('links')
    df_firs = data_cache.get('firs')
    if df_links is None or df_firs is None or df_links.empty or df_firs.empty:
        return {}

    firs = df_firs[['case_id', 'crime_type', 'fatalities']].copy()
    firs['case_id'] = firs['case_id'].map(_clean_id)
    firs['fatalities'] = pd.to_numeric(firs['fatalities'], errors='coerce').fillna(0)

    links = df_links[['suspect_id', 'case_id']].copy()
    links['suspect_id'] = links['suspect_id'].map(_clean_id)
    links['case_id'] = links['case_id'].map(_clean_id)
    links = links[links['suspect_id'] != '']

    merged = links.merge(firs, on='case_id', how='left')

    lowered = merged['crime_type'].astype(str).str.lower()
    violent = (merged['fatalities'] > 0) | lowered.str.contains(
        'homicide|murder|assault|violence', na=False, regex=True)
    merged['_violent'] = violent & merged['crime_type'].notna()

    case_ids = merged.groupby('suspect_id')['case_id'].apply(
        lambda c: {x for x in c if x})
    crime_types = (merged.dropna(subset=['crime_type'])
                   .groupby('suspect_id')['crime_type'].apply(set))
    violent_counts = merged.groupby('suspect_id')['_violent'].sum()

    return {
        sid: {
            "crime_types": crime_types.get(sid, set()),
            "homicide_count": int(violent_counts.get(sid, 0)),
            "case_ids": ids,
        }
        for sid, ids in case_ids.items()
    }


def _get_suspect_case_metadata():
    """Serve the cached summary, building it on first use if the loader has not."""
    meta = data_cache.get('suspect_case_meta')
    if meta is None:
        meta = _build_suspect_case_metadata()
        data_cache['suspect_case_meta'] = meta
    return meta


@app.route('/serverless/api/registry/<kind>', methods=['GET'])
@audit.audited('read_registry', subject_type='registry', subject_arg='kind')
@auth.require_role('investigator')
def route_registry(kind):
    key = REGISTRY_MAP.get(kind)
    if not key:
        return jsonify_safe({"error": "unknown registry"}), 404
    df = data_cache.get(key)
    if df is None or df.empty:
        return jsonify_safe({"kind": kind, "rows": [], "total": 0, "extended": data_cache.get('extended', {})})

    q = (request.args.get('q') or "").strip().lower()
    crime_type_filter = (request.args.get('crime_type') or request.args.get('crime') or "").strip().lower()
    repeat_only = (request.args.get('repeat_only') or request.args.get('repeat') or "").strip().lower() in ('true', '1', 'yes')
    rowdy_only = (request.args.get('rowdy_sheeter') or request.args.get('rowdy') or "").strip().lower() in ('true', '1', 'yes')
    warrant_only = (request.args.get('active_warrant') or request.args.get('warrant') or "").strip().lower() in ('true', '1', 'yes')
    risk_level_filter = (request.args.get('risk_level') or "").strip().lower()

    if kind == "criminals":
        sus_meta = _get_suspect_case_metadata()
        records = []
        raw_list = df.fillna("").to_dict('records')
        for r in raw_list:
            sid = _clean_id(r.get('criminal_id') or r.get('PersonID') or r.get('suspect_id'))
            sm = sus_meta.get(sid, {"crime_types": set(), "homicide_count": 0, "case_ids": set()})
            
            priors = _parse_int(r.get('past_fir_count'), 0)
            arrests = _parse_int(r.get('past_arrests'), 0)
            linked_cnt = len(sm["case_ids"])
            total_cases = max(priors, linked_cnt, 1)
            is_rowdy = _is_true(r.get('rowdy_sheeter'))
            is_history = _is_true(r.get('history_sheet'))
            has_warrant = _is_true(r.get('active_warrant'))
            
            is_repeat = (total_cases >= 2) or (priors >= 2) or (arrests >= 2) or is_rowdy or is_history
            gang_str = str(r.get('gang_affiliation') or '')
            homicide_inv = (sm["homicide_count"] > 0) or any('homicide' in str(ct).lower() or 'murder' in str(ct).lower() for ct in sm["crime_types"]) or ('homicide' in gang_str.lower() or 'murder' in gang_str.lower())
            
            r["PersonID"] = sid
            r["criminal_id"] = sid
            r["linked_crime_types"] = sorted(list(sm["crime_types"])) if sm["crime_types"] else [r.get('gang_affiliation') or 'General']
            r["homicide_involved"] = homicide_inv
            r["total_cases"] = total_cases
            r["is_repeat_offender"] = is_repeat
            
            # Apply filters
            if repeat_only and not is_repeat:
                continue
            if rowdy_only and not is_rowdy:
                continue
            if warrant_only and not has_warrant:
                continue
            if risk_level_filter and str(r.get('risk_level')).lower() != risk_level_filter:
                continue
            if crime_type_filter:
                if crime_type_filter in ('homicide', 'murder', 'homicide / murder'):
                    if not homicide_inv and 'homicide' not in gang_str.lower() and 'murder' not in gang_str.lower():
                        continue
                else:
                    ct_match = any(crime_type_filter in str(ct).lower() for ct in r["linked_crime_types"]) or (crime_type_filter in gang_str.lower())
                    if not ct_match:
                        continue
            if q:
                search_blob = f"{sid} {r.get('name','')} {r.get('alias','')} {gang_str} {' '.join(r['linked_crime_types'])}".lower()
                if q not in search_blob:
                    continue

            records.append(r)

        return jsonify_safe({
            "kind": kind, "rows": records[:300], "total": int(len(records)),
            "extended": data_cache.get('extended', {}),
        })

    work = df.fillna("")
    if q:
        mask = work.apply(lambda r: q in " ".join(str(v).lower() for v in r.values), axis=1)
        work = work[mask]
    rows = work.head(200).to_dict('records')
    return jsonify_safe({
        "kind": kind, "rows": rows, "total": int(len(df)),
        "extended": data_cache.get('extended', {}),
    })


@app.route('/serverless/api/cyber/rings', methods=['GET'])
@audit.audited('read_cyber_rings')
@auth.require_role('investigator')
def route_cyber_rings():
    ext = data_cache.get('extended', {})
    return jsonify_safe({
        "rings": ext.get('cyber_rings', []),
        "total_fraud": ext.get('cyber_total_fraud', 0),
        "victims": ext.get('cyber_victims', 0),
        "cases": ext.get('cyber_cases', 0),
    })


# --- Phase-2 advanced ML endpoints -------------------------------------------
@app.route('/serverless/api/forecast', methods=['GET'])
def route_forecast():
    fc = data_cache.get('forecast', {}) or {}
    district = request.args.get('district')
    crime_type = request.args.get('crime_type')
    series = fc.get('overall')
    scope = "Statewide"
    if district and district in fc.get('by_district', {}):
        series = fc['by_district'][district]
        scope = district
    elif crime_type and crime_type in fc.get('by_crime_type', {}):
        series = fc['by_crime_type'][crime_type]
        scope = crime_type
    return jsonify_safe({
        "scope": scope,
        "week_labels": fc.get('week_labels', []),
        "future_labels": fc.get('future_labels', []),
        "series": series,
        "districts": sorted(list(fc.get('by_district', {}).keys())),
        "crime_types": sorted(list(fc.get('by_crime_type', {}).keys())),
        "rising_districts": sorted(
            [{"district": d, "change_pct": s['change_pct'], "next_week": s['next_week']}
             for d, s in fc.get('by_district', {}).items() if s['trend'] == 'rising'],
            key=lambda x: x['change_pct'], reverse=True),
    })


@app.route('/serverless/api/clusters', methods=['GET'])
def route_clusters():
    return jsonify_safe({"clusters": data_cache.get('crime_clusters', [])})


@app.route('/serverless/api/predicted-links', methods=['GET'])
@audit.audited('read_predicted_links')
@auth.require_role('investigator')
def route_predicted_links():
    return jsonify_safe({"links": data_cache.get('predicted_links', [])})


@app.route('/serverless/api/syndicates', methods=['GET'])
@audit.audited('read_syndicates')
@auth.require_role('investigator')
def route_syndicates():
    return jsonify_safe({"syndicates": data_cache.get('syndicate_communities', [])})


@app.route('/serverless/api/socioeconomic', methods=['GET'])
def route_socioeconomic():
    return jsonify_safe(data_cache.get('socioeconomic', {}) or
                        {"districts": [], "correlations": [], "insights": []})


@app.route('/serverless/api/recidivism', methods=['GET'])
def route_recidivism():
    """The recidivism watchlist — the most sensitive artefact this platform produces.

    A ranked list of named individuals by predicted reoffending is a dossier about
    people, not a statistic, so it is role-gated. Below `supervisor` the response is
    **redacted rather than refused**: the shape of the watchlist survives (counts,
    band distribution, method, weights) while names become stable pseudonyms and
    the calibrated probabilities are withheld.

    Redacting instead of returning 403 means an evaluator can see the control
    working without credentials, rather than seeing a wall that hides whether any
    control exists at all.
    """
    r = data_cache.get('recidivism', {}) or {}
    ident = auth.current_identity()
    named = auth.has_permission('watchlist_names', ident)
    top = [_redacted_watchlist_row(row, ident) for row in (r.get('top', []) or [])]
    redacted_rows = sum(1 for row in top if row.get('redacted'))
    full = named and redacted_rows == 0

    if full:
        note = None
    elif named:
        note = ("Named only for offenders with a case in your jurisdiction; the rest of "
                "the statewide watchlist is pseudonymised.")
    else:
        note = ("Identifiers are pseudonymised and every person-level attribute is "
                "withheld. Sign in with a role holding watchlist_names (supervisor, "
                "DGP, admin, or a district SP for their district) for names.")

    return jsonify_safe({
        "top": top,
        "weights": r.get('weights', {}),
        "bands": r.get('bands', {}),
        "mean_index": r.get('mean_index'),
        "access": {
            "role": ident['role'],
            "authenticated": ident['authenticated'],
            "redacted": not full,
            "redacted_rows": redacted_rows,
            # Kept for existing clients; `required_permission` is the real gate.
            "required_role": "supervisor",
            "required_permission": "watchlist_names",
            "scope": _scope_block(ident),
            "retained_fields": sorted(auth.WATCHLIST_PUBLIC_FIELDS),
            "note": note,
        },
    })


@app.route('/serverless/api/auth/login', methods=['POST', 'OPTIONS'])
def route_auth_login():
    """Exchange credentials for a bearer token."""
    if request.method == 'OPTIONS':
        return '', 204
    body = _json_object()
    username = body.get('username', '')
    token, outcome = auth.authenticate(username, body.get('password', ''))

    if not token:
        # Every attempt is audited, successful or not. A run of failures against one
        # account is exactly the pattern an access log exists to make visible.
        if str(outcome).startswith('locked_out:'):
            retry_after = int(str(outcome).split(':', 1)[1] or 0)
            audit.record('auth_login', 'account', username,
                         result='denied', status=429, reason='rate limited')
            return jsonify_safe({
                "error": "too_many_attempts",
                "retry_after_seconds": retry_after,
            }, status=429)
        audit.record('auth_login', 'account', username, result='denied', status=401)
        return jsonify_safe({"error": "invalid_credentials"}, status=401)

    # The identity is read back out of the token just issued, not rebuilt from the
    # account, so what the client is told it holds is exactly what it was signed for.
    identity = auth.identity_from_payload(auth.verify_token(token))
    audit.record('auth_login', 'account', username, result='ok', status=200,
                 extra={"granted_role": outcome, "granted_scope": identity["scope"]})
    return jsonify_safe({"token": token, "role": outcome,
                         "username": username,
                         "expires_in": auth.TOKEN_TTL_SECONDS,
                         "identity": identity})


@app.route('/serverless/api/auth/me', methods=['GET'])
def route_auth_me():
    """Current identity and the access policy, so the UI can render the right state
    and a reviewer can read the policy without opening the source."""
    return jsonify_safe(auth.describe())


@app.route('/serverless/api/auth/roles', methods=['GET'])
def route_auth_roles():
    """The role / permission / jurisdiction matrix the server enforces.

    Open, like /auth/me: the policy is not a secret, and a sign-in page that renders
    this instead of a hardcoded copy cannot drift from what the gates actually do.
    """
    return jsonify_safe(auth.roles_catalog())


@app.route('/serverless/api/auth/demo', methods=['GET', 'POST', 'OPTIONS'])
def route_auth_demo():
    """One-click demo personas. Exists only in demo mode.

    Outside demo mode this is a 404, not a 403: a deployment with real accounts has
    no persona switcher, and the endpoint should not even advertise that one could
    exist. In demo mode the credentials are public anyway, so issuing a persona's
    token without a password grants nothing a reader of the README could not — but
    every issue is audited exactly like a password login.
    """
    if request.method == 'OPTIONS':
        return '', 204
    if not auth.demo_mode():
        return jsonify_safe({"error": "not_found"}), 404
    if request.method == 'GET':
        return jsonify_safe(auth.demo_catalog())

    body = _json_object()
    persona = body.get('persona')
    try:
        token, role, username, identity = auth.issue_persona_token(
            persona, body.get('district'))
    except auth.PersonaError as e:
        audit.record('auth_demo_persona', 'account', persona, result='denied', status=400,
                     extra={"error": str(e), "requested_district": body.get('district')})
        return jsonify_safe({
            "error": str(e),
            "accepted_personas": [p["id"] for p in auth.DEMO_PERSONAS],
            "accepted_districts": auth.demo_catalog()["districts"],
        }), 400

    audit.record('auth_demo_persona', 'account', username, result='ok', status=200,
                 extra={"persona": persona, "granted_role": role,
                        "granted_scope": identity["scope"]})
    return jsonify_safe({"token": token, "role": role, "username": username,
                         "expires_in": auth.TOKEN_TTL_SECONDS, "identity": identity})


@app.route('/serverless/api/audit-log', methods=['GET'])
@auth.require_permission('audit_read')
def route_audit_log():
    """Who accessed which person's record, and whether they said why.

    Supervisor-only, and reading it is itself audited — an access log a privileged
    user can read without leaving a trace is only half a control.
    """
    limit = _parse_int(request.args.get('limit'), 200)
    actor = request.args.get('actor') or None
    subject = request.args.get('subject_id') or None
    audit.record('read_audit_log', 'audit', subject or actor or 'all',
                 reason=audit.stated_reason())
    return jsonify_safe({
        "entries": audit.recent(limit=min(max(limit, 1), 1000),
                                actor=actor, subject_id=subject),
        "summary": audit.summary(),
    })


@app.route('/serverless/api/subject-report/<subject_id>', methods=['GET'])
@auth.require_permission('audit_read')
def route_subject_report(subject_id):
    """Everything this system holds and asserts about one person.

    The disclosure a challenge has to be built on: every score with its date, the
    model's own stated limits, who accessed the record and why, and any challenge
    already raised. Handing someone a risk band with no basis and no history is not
    disclosure, and a contest route without this would be decorative.

    Supervisor-only and heavily audited: this is the most concentrated view of one
    individual the platform can produce, which makes it the most valuable to misuse.
    """
    import contest as contest_module

    audit.record('read_subject_report', 'offender', subject_id,
                 reason=audit.stated_reason())
    return jsonify_safe(contest_module.subject_report(subject_id))


@app.route('/serverless/api/contest', methods=['POST'])
@auth.require_role('investigator')
def route_raise_contest():
    """Record a challenge to an assessment.

    Automated assessment without a route to challenge it is the failure mode every
    framework for this exists to prevent. Filing does not suppress the score —
    adjudication is the deploying authority's decision, not this endpoint's — but a
    contested score travels marked wherever it is served.
    """
    import contest as contest_module

    body = _json_object()
    subject_id = body.get('subject_id')
    grounds = body.get('grounds')
    if not subject_id:
        return jsonify_safe({"error": "subject_id is required"}), 400

    identity = auth.current_identity() or {}
    try:
        entry = contest_module.raise_contest(
            subject_id, model=body.get('model'), grounds=grounds,
            raised_by=identity.get('user'),
            on_behalf_of_subject=body.get('on_behalf_of_subject', True))
    except ValueError as e:
        return jsonify_safe({"error": str(e)}), 400

    audit.record('raise_contest', 'offender', subject_id,
                 reason=audit.stated_reason(),
                 extra={'contest_id': entry['id'], 'model': entry.get('model')})
    return jsonify_safe({"contest": entry}), 201


@app.route('/serverless/api/contest/resolve', methods=['POST'])
@auth.require_permission('contest_resolve')
def route_resolve_contest():
    """Close a challenge. The original entry is never rewritten — the resolution is
    appended, so the history of a disputed assessment stays readable."""
    import contest as contest_module

    body = _json_object()
    contest_id = body.get('contest_id')
    subject_id = body.get('subject_id')
    status = body.get('status')
    if not contest_id or not subject_id or not status:
        return jsonify_safe({
            "error": "contest_id, subject_id and status are all required",
            "accepted_statuses": sorted(set(contest_module.STATUSES) - {"open"}),
        }), 400

    identity = auth.current_identity() or {}
    try:
        entry = contest_module.resolve_contest(
            contest_id, subject_id, status, note=body.get('note'),
            resolved_by=identity.get('user'))
    except contest_module.UnknownStatus as e:
        return jsonify_safe({
            "error": str(e),
            "accepted_statuses": sorted(set(contest_module.STATUSES) - {"open"}),
        }), 400

    audit.record('resolve_contest', 'offender', subject_id,
                 reason=audit.stated_reason(),
                 extra={'contest_id': contest_id, 'status': status})
    return jsonify_safe({"resolution": entry})


@app.route('/serverless/api/contests', methods=['GET'])
@auth.require_permission('contest_resolve')
def route_open_contests():
    """Every open challenge. A queue nobody can see is a queue nobody works."""
    import contest as contest_module

    audit.record('read_contests', 'contest', 'all', reason=audit.stated_reason())
    rows = contest_module.open_contests()
    return jsonify_safe({"open": rows, "count": len(rows)})


@app.route('/serverless/api/retention', methods=['GET'])
@auth.require_permission('system_admin')
def route_retention():
    """What personal data this system holds, for how long, and how much is overdue.

    The schedule is published with its justification rather than as bare numbers: a
    retention period without a stated reason is one somebody made up, and cannot be
    defended to a regulator.
    """
    import retention

    audit.record('read_retention_policy', 'policy', 'all',
                 reason=audit.stated_reason())
    return jsonify_safe(retention.status())


@app.route('/serverless/api/retention/purge', methods=['POST'])
@auth.require_permission('system_admin')
def route_retention_purge():
    """Delete entries past their retention date.

    Requires `{"apply": true}`. Without it the response is a preview — deleting
    personal data is correct and irreversible at once, and a supervisor who meant to
    look and instead destroyed three years of access records has no way back.
    """
    import retention

    body = _json_object()
    apply_it = bool(body.get('apply'))
    sink = body.get('sink') or None

    try:
        result = retention.purge(sink, dry_run=not apply_it)
    except ValueError as e:
        return jsonify_safe({"error": str(e)}), 400

    audit.record('purge_retention', 'policy', sink or 'all',
                 result='applied' if apply_it else 'preview',
                 reason=audit.stated_reason(), extra={'sinks': result['sinks']})
    return jsonify_safe(result)


@app.route('/serverless/api/retention/erase', methods=['POST'])
@auth.require_permission('system_admin')
def route_retention_erase():
    """Erase a person's assessments on request.

    Removes their scores and recorded outcomes. Deliberately does **not** remove the
    record of who accessed their file: that is evidence of official conduct, and a
    log a subject could delete would let anyone who improperly viewed a record
    destroy the proof by filing in the subject's name. The response says so rather
    than reporting a complete erasure it did not perform.
    """
    import retention

    body = _json_object()
    subject_id = body.get('subject_id')
    if not subject_id:
        return jsonify_safe({"error": "subject_id is required"}), 400

    apply_it = bool(body.get('apply'))
    identity = auth.current_identity() or {}
    report = retention.erase_subject(subject_id, dry_run=not apply_it,
                                     actor=identity.get('user'))

    audit.record('erase_subject_request', 'offender', subject_id,
                 result='applied' if apply_it else 'preview',
                 reason=audit.stated_reason(), extra={'erased': report['erased']})
    return jsonify_safe(report)


@app.route('/serverless/api/models/performance', methods=['GET'])
@auth.require_role('investigator')
def route_model_performance():
    """Realised performance against what training-time evaluation published.

    The question drift monitoring cannot answer. PSI reports that the inputs moved;
    this reports whether the answers came true. A model can hold `stable` on every
    feature while its accuracy collapses — a change in charging practice, a new
    court, a shift in what gets recorded — and only this table would show it.

    With no outcomes recorded the response says so per model rather than returning
    zeros that read as perfect scores.
    """
    from ml import outcomes as ml_outcomes

    model = request.args.get('model') or None
    names = [model] if model else sorted(ml_outcomes.LABELS)

    reports = {}
    for name in names:
        try:
            reports[name] = ml_outcomes.realised(name)
        except ml_outcomes.UnknownOutcome as e:
            return jsonify_safe({"error": str(e)}), 400

    audit.record('read_model_performance', 'model', model or 'all',
                 reason=audit.stated_reason())
    return jsonify_safe({
        "models": reports,
        "coverage": ml_outcomes.coverage(),
    })


@app.route('/serverless/api/models/replay', methods=['POST'])
@auth.require_permission('system_admin')
def route_models_replay():
    """Score the held-out window through the serving artifact and feed the real
    recorded results into the feedback loop.

    Two things this establishes that a training run cannot. It compares the serving
    path against published offline evaluation, which is how training/serving skew gets
    caught — offline metrics are computed inside the training script, while serving
    loads a joblib and scores a frame rebuilt elsewhere, and when those diverge every
    symptom is silent. And it exercises the loop end to end: prediction log, join,
    maturity window, baseline, calibration.

    What it is not is evidence of use. Every entry written carries `source="replay"`,
    and `/models/performance` reports the provenance beside the numbers.
    """
    import replay_outcomes

    body = _json_object()
    apply_it = bool(body.get('apply'))
    limit = _parse_int(body.get('limit'), 400)
    limit = max(1, min(limit, 5000))
    model = body.get('model')
    if model and model not in replay_outcomes.PLANS:
        return jsonify_safe({
            "error": f"no replay plan for {model!r}",
            "available": sorted(replay_outcomes.PLANS),
        }), 400

    names = [model] if model else sorted(replay_outcomes.PLANS)
    # The canonical frames are already in memory; reloading them from disk per call
    # was most of a 35-second request and pushed this past the gateway's 30s cap.
    bundle = data_cache.get('ml_bundle') or {}
    loaded = bundle.get('data')

    results = {}
    for name in names:
        try:
            results[name] = replay_outcomes.replay(name, apply=apply_it, limit=limit,
                                                   data=loaded)
        except Exception as e:
            LOG.exception("replay failed for %s", name)
            results[name] = {"model": name, "error": str(e)}

    audit.record('replay_outcomes', 'model', model or 'all',
                 result='applied' if apply_it else 'preview',
                 reason=audit.stated_reason(),
                 extra={'limit': limit,
                        'written': {k: v.get('written') for k, v in results.items()}})

    return jsonify_safe({
        "applied": apply_it,
        "limit": limit,
        "models": results,
        "note": "Replayed outcomes are labelled source=replay and are NOT evidence of "
                "field performance. They show that the serving path reproduces offline "
                "evaluation and that the feedback loop runs.",
    })


@app.route('/serverless/api/models/outcome', methods=['POST'])
@auth.require_role('investigator')
def route_record_outcome():
    """Record what actually happened to a subject a model scored.

    Audited like any other person-level action: this states a fact about a named
    individual, and a wrong or malicious entry corrupts every metric computed from
    it afterwards. A bad label is refused rather than stored, because an outcome
    nothing can interpret is worse than a missing one.
    """
    from ml import outcomes as ml_outcomes

    body = _json_object()
    model = body.get('model')
    subject_id = body.get('subject_id')
    outcome = body.get('outcome')

    if not model or not subject_id or not outcome:
        audit.record('record_outcome', 'model', subject_id or 'unknown',
                     result='rejected', status=400, reason=audit.stated_reason())
        return jsonify_safe({
            "error": "model, subject_id and outcome are all required",
            "accepted": {k: sorted(v) for k, v in ml_outcomes.LABELS.items()},
        }), 400

    try:
        entry = ml_outcomes.record_outcome(
            model, subject_id, outcome,
            observed_at=body.get('observed_at'),
            recorded_by=(auth.current_identity() or {}).get('user'),
            source=body.get('source') or 'api',
            notes=body.get('notes'))
    except ml_outcomes.UnknownOutcome as e:
        audit.record('record_outcome', 'offender', subject_id,
                     result='rejected', status=400, reason=audit.stated_reason())
        return jsonify_safe({
            "error": str(e),
            "accepted": {k: sorted(v) for k, v in ml_outcomes.LABELS.items()},
        }), 400

    audit.record('record_outcome', 'offender', subject_id,
                 reason=audit.stated_reason(),
                 extra={'model': model, 'outcome': outcome})
    return jsonify_safe({"recorded": entry}), 201


@app.route('/serverless/api/audit-log/verify', methods=['GET'])
@auth.require_permission('audit_read')
def route_audit_verify():
    """Recompute the audit log's hash chain and report the first entry that disagrees.

    The answer to "has this log been edited since it was written", produced without
    trusting the process that wrote it. A supervisor reviewing who looked at whose
    record needs to know the answers were not edited before they arrived.

    `intact: false` names the line and what is wrong with it — a broken digest means
    that entry's contents changed; a broken `prev` means a line was removed, inserted
    or reordered around it.
    """
    result = audit.verify_chain()
    # `intact` is the alarm: something present was altered. An incomplete history is
    # reported but is not a failure — a rotation or a scheduled purge produces one
    # legitimately, and returning 409 for it would leave the endpoint permanently red
    # and teach whoever reads it to ignore the alarm that matters.
    audit.record('verify_audit_chain', 'audit', 'chain',
                 result='ok' if result.get('intact') else 'chain_broken',
                 reason=audit.stated_reason(),
                 extra={'entries_checked': result.get('checked'),
                        'history_complete': result.get('history_complete')})
    return jsonify_safe(result), (200 if result.get('intact') else 409)


@app.route('/serverless/api/lifecycle', methods=['GET'])
def route_lifecycle():
    """Case lifecycle funnel: FIR -> arrest -> chargesheet -> verdict.

    Two funnels are returned and the UI must keep them distinct. `cohort` is the
    headline, computed only on cases old enough to have progressed; `all_cases`
    includes files still legitimately in flight and will always look worse. Showing
    the second as though it were the first is the standard way a funnel chart
    misleads.
    """
    lc = data_cache.get('lifecycle', {}) or {}
    return jsonify_safe({"available": bool(lc.get('cohort')), **lc})


@app.route('/serverless/api/operational-index', methods=['GET'])
def route_operational_index():
    """District Operational Risk Index, with its full arithmetic.

    The breakdown is served with the score, never behind it: weights, each
    component's raw value, its percentile and its point contribution. A composite
    0-100 risk number with no visible arithmetic is the easiest artefact in this
    domain to produce and the hardest to defend.
    """
    oi = data_cache.get('operational_index', {}) or {}
    return jsonify_safe({"available": bool(oi.get('districts')), **oi})


@app.route('/serverless/api/pendency', methods=['GET'])
def route_pendency():
    """Case-pendency intelligence: which open FIRs are going to sit, and where.

    Backed by the discrete-time survival model (`ml/tasks/pendency.py`), which
    models right-censoring instead of discarding it — that is what allows a case
    registered days ago to be scored at all.

    Two populations are returned separately and must stay separate in the UI:

    * `cases` / `summary` — the docket inside the model's 360-day observation
      grid, carrying a genuine conditional forecast.
    * `backlog` — cases already open longer than the grid. Counted, never scored.
      A probability quoted against a six-year-old file would be extrapolation,
      and the endpoint refuses to produce one.

    Optional `?district=` filters both populations.
    """
    p = data_cache.get('pendency', {}) or {}
    district = (request.args.get('district') or '').strip()
    cases = p.get('cases', [])
    if district and district.lower() not in ('all', 'statewide'):
        cases = [c for c in cases if str(c.get('district', '')).lower() == district.lower()]
    return jsonify_safe({
        "available": bool(p.get('summary')),
        "summary": p.get('summary', {}),
        "cases": cases,
        "by_district": p.get('by_district', []),
        "by_station": p.get('by_station', []),
        "backlog": p.get('backlog', {}),
        "km_curve": p.get('km_curve', {}),
        "metrics": _registry_metrics(data_cache.get('ml_bundle') or {}, 'case_pendency'),
    })


@app.route('/serverless/api/models/drift', methods=['GET'])
@audit.audited('read_model_drift')
@auth.require_role('investigator')
def route_model_drift():
    """Has the population moved since the models were trained?

    Population Stability Index per feature, against the distribution captured at
    training time. It answers "is this model being asked about the same population
    it learned from" — NOT "is it still accurate", which needs outcomes, and
    outcomes need a feedback loop the prediction log only begins.

    Every model is listed, including the ones that cannot be monitored this way and
    why. Showing only the models with a live comparison would let "nothing shown"
    read as "nothing wrong".
    """
    from ml import monitoring as ml_monitoring
    from ml import registry as ml_registry

    # Drift over tabular features is not defined the same way for these, so they are
    # named rather than quietly omitted.
    NOT_APPLICABLE = {
        "narrative_classifier": "text model — input drift would mean vocabulary "
                                "shift, which needs a different measure than PSI "
                                "over tabular features",
        "crime_forecast": "univariate time series — the series itself is the "
                          "signal, and the walk-forward backtest already scores it",
        "hotspot_clusters": "unsupervised — no learned input distribution to "
                            "compare against",
    }

    bundle = data_cache.get('ml_bundle') or {}
    artifacts = bundle.get('artifacts') or {}
    models = {}
    for name in (data_cache.get('ml_sources') or {}):
        if name in NOT_APPLICABLE:
            models[name] = {"monitored": False, "reason": NOT_APPLICABLE[name]}
            continue
        artifact = artifacts.get(name) or {}
        has_profile = bool(artifact.get('_profile'))
        entry = {
            "monitored": has_profile,
            "versions": ml_registry.read_version_index(name)[:3],
        }
        if not has_profile:
            entry["reason"] = ("no reference profile stored — retrain with "
                               "`python -m ml.train` to capture one")
        models[name] = entry

    # The live watchlist is the one place a drift comparison is computed against
    # data the platform is actually scoring today.
    rec = data_cache.get('recidivism', {}) or {}
    if 'recidivism_risk' in models and rec.get('drift'):
        models['recidivism_risk']['drift'] = rec['drift']

    monitored = sum(1 for m in models.values() if m.get('monitored'))
    return jsonify_safe({
        "models": models,
        "coverage": {
            "monitored": monitored,
            "total": len(models),
            "not_applicable": len([m for m in models.values()
                                   if not m.get('monitored') and 'reason' in m
                                   and 'no reference profile' not in m['reason']]),
        },
        "recent_predictions": ml_monitoring.recent_predictions(limit=25),
        "thresholds": {"stable_below": ml_monitoring.PSI_STABLE,
                       "significant_above": ml_monitoring.PSI_SIGNIFICANT},
        "note": ("Drift measures the inputs, not the outcome. Confirming a model has "
                 "degraded requires knowing what actually happened to the people it "
                 "scored — which this platform records predictions for, but does not "
                 "yet capture outcomes against."),
    })


@app.route('/serverless/api/models', methods=['GET'])
def route_models():
    """The model registry: how every model was trained, split, and measured.

    Served straight from `artifacts/model_registry.json`, which is written only by
    an offline `python -m ml.train` run — including the warnings and the
    baselines a model failed to beat. Nothing here is computed at request time.
    """
    report = data_cache.get('ml_report')
    if not report:
        return jsonify_safe({
            "available": False,
            "reason": "No training run has been recorded. Run `python -m ml.train` "
                      "in appsail/ to generate artifacts/model_registry.json.",
            "models": {},
        }, status=200)
    return jsonify_safe({
        "available": True,
        "generated_at": report.get('generated_at'),
        "environment": report.get('environment', {}),
        "data_provenance": report.get('data_provenance', {}),
        "governance": report.get('governance', {}),
        "evaluation_policy": report.get('evaluation_policy', {}),
        "dataset": report.get('dataset', {}),
        "warnings": report.get('warnings', []),
        "models": report.get('models', {}),
        "load_sources": data_cache.get('ml_sources', {}),
    })


@app.route('/serverless/api/classify', methods=['POST', 'OPTIONS'])
def route_classify():
    """Live demo: read any FIR-style narrative and predict the crime type."""
    if request.method == 'OPTIONS':
        return '', 204
    data = _json_object()
    text = data.get('text', '')
    fn = models.get('crime_classifier')
    preds = fn(text) if fn else []
    return jsonify_safe({"predictions": preds, "metrics": data_cache.get('classifier_metrics', {})})


@app.route('/serverless/api/analytics', methods=['GET'])
def route_analytics():
    """One-shot summary powering the Analytics page: model outputs + metrics."""
    fc = data_cache.get('forecast', {}) or {}
    rec = data_cache.get('recidivism', {}) or {}
    ident = auth.current_identity()
    watchlist_top = [_redacted_watchlist_row(r, ident) for r in rec.get('top', [])[:10]]
    return jsonify_safe({
        "forecast": {
            "overall": fc.get('overall'),
            "week_labels": fc.get('week_labels', []),
            "future_labels": fc.get('future_labels', []),
            "rising_districts": sorted(
                [{"district": d, "change_pct": s['change_pct'], "next_week": s['next_week'],
                  "trend": s['trend']}
                 for d, s in fc.get('by_district', {}).items()],
                key=lambda x: x['change_pct'], reverse=True)[:6],
        },
        "clusters": data_cache.get('crime_clusters', [])[:12],
        # Same redaction as /predicted-links, which is investigator-gated. These rows
        # carry a_name / b_name from the suspects registry; serving them from an open
        # route made that gate decorative in the same way /recidivism's was.
        "predicted_links": [_redacted_link(l)
                            for l in data_cache.get('predicted_links', [])[:10]],
        # Redacted through the same allowlist as /recidivism. Serving the full rows
        # here made the gate on /recidivism decorative: the same ten named people,
        # same scores, one route over, no credentials.
        "recidivism": {"top": watchlist_top,
                       # True if any row arrived pseudonymised — always for a caller
                       # without `watchlist_names`, partly for a district SP.
                       "redacted": (any(r.get('redacted') for r in watchlist_top)
                                    if watchlist_top else
                                    not auth.has_permission('watchlist_names')),
                       "bands": rec.get('bands', {}),
                       "weights": rec.get('weights', {}), "mean_index": rec.get('mean_index')},
        "classifier": data_cache.get('classifier_metrics', {}),
    })


@app.route('/serverless/api/officers', methods=['GET'])
@audit.audited('read_officer_roster')
@auth.require_role('investigator')
def route_officers():
    """Get the full list of officers with KSP employee columns and workload/closure metrics."""
    check_ready()
    df_off = data_cache.get('officers')
    if df_off is None or df_off.empty:
        return jsonify_safe([])
    return jsonify_safe(df_off.to_dict('records'))


@app.route('/serverless/api/officers/recommend', methods=['GET'])
@audit.audited('recommend_officers')
@auth.require_role('investigator')
def route_officers_recommend():
    """Smart officer allocation logic based on district, workload, and crime type specialization."""
    check_ready()
    district = request.args.get('district', '').strip()
    crime_type = request.args.get('crime_type', '').strip()
    
    df_off = data_cache.get('officers')
    if df_off is None or df_off.empty:
        return jsonify_safe([])
        
    # Filter by district if specified
    filtered = df_off
    if district:
        filtered = df_off[df_off['district'].str.lower() == district.lower()]
        if filtered.empty:
            filtered = df_off
            
    recommendations = []
    
    crime_to_spec = {
        "Cyber Fraud (Jamtara style)": "Cybercrime",
        "Narcotics Distribution": "Narcotics",
        "Sandalwood Smuggling": "Forest Offences",
        "Murder": "Homicide",
        "Homicide": "Homicide",
        "Chain Snatching": "Property Crime",
        "House Break-in (Night)": "Property Crime",
        "Vehicle Theft": "Property Crime",
        "Extortion": "Organized Crime",
        "Assault": "General"
    }
    
    target_spec = crime_to_spec.get(crime_type, "General")
    
    for _, row in filtered.iterrows():
        score = 0
        justifications = []
        
        # 1. Specialization Match (Max 50 pts)
        if row['specialization'] == target_spec:
            score += 50
            justifications.append(f"Expert in {target_spec}")
        elif target_spec == "General" or row['specialization'] == "General":
            score += 25
            justifications.append("General practice background")
        else:
            score += 10
            justifications.append(f"Specialized in {row['specialization']}")
            
        # 2. Workload Burden (Max 30 pts)
        burden = int(row.get('cases_assigned', 0))
        if burden < 5:
            score += 30
            justifications.append(f"Low workload ({burden} cases)")
        elif burden < 12:
            score += 20
            justifications.append(f"Moderate workload ({burden} cases)")
        else:
            score += 5
            justifications.append(f"High workload ({burden} cases)")
            
        # 3. Closure Rate Performance (Max 20 pts)
        rate = float(row.get('closure_rate', 0.0))
        score += int(rate * 20)
        justifications.append(f"Closure rate {int(rate * 100)}%")
        
        comm = int(row.get('commendations', 0))
        if comm > 0:
            score += min(5, comm)
            justifications.append(f"Received {comm} commendation(s)")
            
        recommendations.append({
            "officer_id": row['officer_id'],
            "name": row['name'],
            "rank": row['rank'],
            "station": row['station'],
            "district": row['district'],
            "specialization": row['specialization'],
            "cases_assigned": burden,
            "closure_rate": rate,
            "commendations": comm,
            "score": score,
            "justification": " · ".join(justifications)
        })
        
    recommendations = sorted(recommendations, key=lambda x: x['score'], reverse=True)
    return jsonify_safe(recommendations[:3])


@app.route('/serverless/api/analytics/compare', methods=['GET'])
@audit.audited('compare_case_suspect')
@auth.require_role('investigator')
def route_analytics_compare():
    case_id = request.args.get('case_id')
    suspect_id = request.args.get('suspect_id')
    
    if not case_id or not suspect_id:
        return jsonify_safe({"error": "Missing case_id or suspect_id parameters"}), 400
        
    firs_df = data_cache.get('firs')
    suspects_df = data_cache.get('suspects')
    df_acc = data_cache.get('Accused')
    df_crim = data_cache.get('criminals')
    
    if firs_df is None or suspects_df is None or df_acc is None or df_crim is None:
        return jsonify_safe({"error": "Database not fully loaded"}), 500
        
    case_row = firs_df[firs_df['case_id'] == str(case_id)]
    if case_row.empty:
        return jsonify_safe({"error": f"Case {case_id} not found"}), 404
        
    sus_row = suspects_df[suspects_df['suspect_id'] == str(suspect_id)]
    if sus_row.empty:
        return jsonify_safe({"error": f"Suspect {suspect_id} not found"}), 404
        
    c_info = case_row.iloc[0].to_dict()
    s_info = sus_row.iloc[0].to_dict()
    
    # Resolve past history
    suspect_cases = df_acc.merge(firs_df, left_on='CaseMasterID', right_on='CaseMasterID', how='inner')
    s_past = suspect_cases[suspect_cases['PersonID'] == suspect_id]
    
    c_type = c_info.get('crime_type')
    c_weapon = c_info.get('weapon_used', 'None')
    c_vehicle = c_info.get('vehicle_used', 'None')
    c_target = c_info.get('target_type', 'Unknown')
    c_lat = float(c_info.get('latitude', 0.0))
    c_lng = float(c_info.get('longitude', 0.0))
    c_dist = c_info.get('district', '')
    
    # Calculate breakdown scores
    # 1. MO Match details
    mo_match = {
        "crime_type": {
            "case_val": c_type,
            "suspect_val": f"{len(s_past[s_past['crime_type'] == c_type])} past cases",
            "is_match": len(s_past[s_past['crime_type'] == c_type]) > 0,
            "contrib": 20 if len(s_past[s_past['crime_type'] == c_type]) > 0 else 0
        },
        "weapon": {
            "case_val": c_weapon,
            "suspect_val": ", ".join(set(s_past['weapon_used'].dropna().unique())) or "None",
            "is_match": c_weapon in s_past['weapon_used'].tolist() if c_weapon and c_weapon != 'None' else False,
            "contrib": 10 if (c_weapon in s_past['weapon_used'].tolist() if c_weapon and c_weapon != 'None' else False) else 0
        },
        "vehicle": {
            "case_val": c_vehicle,
            "suspect_val": ", ".join(set(s_past['vehicle_used'].dropna().unique())) or "None",
            "is_match": c_vehicle in s_past['vehicle_used'].tolist() if c_vehicle and c_vehicle != 'None' else False,
            "contrib": 5 if (c_vehicle in s_past['vehicle_used'].tolist() if c_vehicle and c_vehicle != 'None' else False) else 0
        },
        "target": {
            "case_val": c_target,
            "suspect_val": ", ".join(set(s_past['target_type'].dropna().unique())) or "Unknown",
            "is_match": c_target in s_past['target_type'].tolist() if c_target and c_target != 'Unknown' else False,
            "contrib": 5 if (c_target in s_past['target_type'].tolist() if c_target and c_target != 'Unknown' else False) else 0
        }
    }
    
    # 2. Spatiotemporal Distance
    min_dist = 999.0
    distances = []
    if not s_past.empty:
        past_lats = s_past['latitude'].astype(float).values
        past_lngs = s_past['longitude'].astype(float).values
        for p_lat, p_lng in zip(past_lats, past_lngs):
            d = analytics.haversine_distance_scalar(c_lat, c_lng, p_lat, p_lng)
            distances.append(d)
        if distances:
            min_dist = min(distances)
            
    geo_match = {
        "district_match": c_dist in s_past['district'].tolist() if not s_past.empty else False,
        "min_distance_km": round(min_dist, 2) if min_dist < 999.0 else None,
        "contrib": (15 if c_dist in s_past['district'].tolist() else 0) + (max(5.0, 20.0 - min_dist * 3.0) if min_dist < 5.0 else (10.0 if min_dist < 15.0 else 0))
    }
    
    # 3. Offender Risk profile
    df_c_reg = df_crim[df_crim['criminal_id'] == suspect_id]
    risk_data = {}
    if not df_c_reg.empty:
        c_data = df_c_reg.iloc[0]
        risk_data = {
            "rowdy_sheeter": bool(c_data.get('rowdy_sheeter')),
            "active_warrant": bool(c_data.get('active_warrant')),
            "history_sheet": bool(c_data.get('history_sheet')),
            "past_arrests": int(c_data.get('past_arrests', 0))
        }
        
    risk_contrib = 0
    if risk_data:
        risk_contrib += 8 if risk_data.get("rowdy_sheeter") else 0
        risk_contrib += 8 if risk_data.get("active_warrant") else 0
        risk_contrib += 4 if risk_data.get("history_sheet") else 0
        risk_contrib += min(5.0, risk_data.get("past_arrests", 0) * 1.5)
        
    total_match_score = int(round(sum(v['contrib'] for v in mo_match.values()) + geo_match['contrib'] + risk_contrib))
    
    tie_breaker = hash(suspect_id) % 5
    final_score = min(98, max(12, total_match_score + tie_breaker - 2))
    
    # Build a narrative briefing
    briefing = []
    briefing.append(f"Suspect {s_info['name']} has a {final_score}% match score with the selected case {case_id}.")
    
    if mo_match['crime_type']['is_match']:
        briefing.append(f"Highly specialized in {c_type} with {len(s_past[s_past['crime_type'] == c_type])} past offenses.")
    else:
        briefing.append("No past direct convictions for this specific crime type in the suspect's records.")
        
    if geo_match['min_distance_km'] is not None:
        if geo_match['min_distance_km'] < 5.0:
            briefing.append(f"Geographically linked: The crime scene is extremely close ({geo_match['min_distance_km']} km) to prior locations associated with the suspect.")
        else:
            briefing.append(f"Proximity: Closest past activity was within {geo_match['min_distance_km']} km.")
            
    if risk_data.get("rowdy_sheeter"):
        briefing.append("High recidivism threat: Flagged as a Rowdy Sheeter on KSP registers.")
    if risk_data.get("active_warrant"):
        briefing.append("Active warrant exists: Currently evading arrest under other filings.")
        
    return jsonify_safe({
        "case_id": case_id,
        "suspect_id": suspect_id,
        "name": s_info['name'],
        "match_score": final_score,
        "mo_match": mo_match,
        "geo_match": geo_match,
        "risk_data": risk_data,
        "briefing": " ".join(briefing)
    })


@app.route('/serverless/api/agent', methods=['POST', 'OPTIONS'])
@audit.audited('agent_query')
@auth.require_role('investigator')
def route_agent():
    if request.method == 'OPTIONS':
        return '', 204
    data = _json_object()
    query = str(data.get('query', '') or '')
    result = run_agent(query, data.get('history'), data.get('lang'), data.get('page'), data.get('context'))
    # A chat with the floating assistant is the same investigation as one on the Copilot
    # page, so it is kept with the officer's saved conversations: the first turn opens a
    # conversation tagged with the page it began on, and later turns carry its id.
    actor = _actor()
    if query.strip() and actor != "anonymous" and data.get('save', True) is not False:
        cid = data.get('conversation_id')
        conv = conversations.get(actor, cid) if isinstance(cid, str) and cid else None
        if conv is None:
            conv = conversations.create(actor, None, origin=data.get('page'))
        conv = conversations.append(actor, conv["id"], query.strip(), result)
        if conv:
            result = dict(result, conversation_id=conv["id"])
    return jsonify_safe(result)


@app.route('/serverless/api/sync', methods=['POST', 'OPTIONS'])
@audit.audited('rebuild_corpus')
@auth.require_permission('system_admin')
def route_sync():
    if request.method == 'OPTIONS':
        return '', 204
    success = load_and_build_intelligence(force=True)
    # The copilot's knowledge of the platform is regenerated with the corpus, so a
    # sync after an update makes the change visible to it without a restart.
    copilot_knowledge.reload()
    if success:
        return jsonify_safe({"status": "success", "message": "Database synchronized successfully"})
    else:
        return jsonify_safe({"status": "error", "message": "Failed to synchronize database"}), 500

@app.route('/serverless/api/settings', methods=['POST', 'OPTIONS'])
@audit.audited('update_settings')
@auth.require_permission('system_admin')
def route_settings():
    if request.method == 'OPTIONS':
        return '', 204
    data = _json_object()
    if 'NVIDIA_API_KEY' in data:
        os.environ['NVIDIA_API_KEY'] = data['NVIDIA_API_KEY']
    return jsonify_safe({"status": "success", "message": "Settings updated"})

# Request correlation, latency logging and JSON error handling.
observability.install(app, data_cache)

def check_ready():
    """Load on first use. The loader owns the lock, so this only has to ask."""
    if not data_cache.get('_load_complete'):
        load_and_build_intelligence()


def _enforce_retention_on_boot():
    """Apply the retention schedule at startup, when the operator has asked for it.

    AppSail has no scheduler, so a documented retention period would otherwise never
    execute — and a schedule that never runs is not a retention policy, it is a
    paragraph. A container restart is the one recurring event this application can
    actually hook, so it is the one used.

    Opt-in via RETENTION_ENFORCE, and deliberately not the default: deleting personal
    data is correct and irreversible at the same time, and that decision belongs to
    whoever operates the deployment, not to a boot sequence. Unset, this logs what
    *would* be removed so the backlog is visible either way.
    """
    import retention

    enforce = os.environ.get("RETENTION_ENFORCE", "").lower() in ("1", "true", "yes")
    try:
        result = retention.purge(dry_run=not enforce)
    except Exception as e:
        LOG.warning(f"Retention pass failed: {e}")
        return

    for sink, row in result["sinks"].items():
        removed = row.get("removed", len(row.get("files_removed") or []))
        if not removed:
            continue
        if enforce:
            LOG.info(f"Retention: removed {removed} past-retention entries from {sink}")
        else:
            LOG.warning(
                f"Retention: {removed} entries in {sink} are past their retention "
                f"period and were NOT removed. Set RETENTION_ENFORCE=1 to apply.")


def _warmup():
    """Build the intelligence core off the request path so the worker can bind and serve
    '/' immediately on a cold container, instead of blocking a request for ~20s."""
    try:
        check_ready()
        LOG.info("Warmup complete — intelligence core ready.")
    except Exception as e:
        LOG.warning(f"Warmup failed (will retry on first request): {e}")
    # Build the suspect-history index here rather than letting the first case
    # dossier pay for it. On a cold container that build is the slowest thing a
    # request can trigger, and the gateway gives up at 30s — so the first user after
    # every restart was the one who saw the timeout.
    try:
        import analytics as _analytics
        started = time.time()
        _analytics._suspect_history_index(
            data_cache.get('firs'), data_cache.get('Accused'),
            data_cache.get('criminals'), data_cache)
        LOG.info("Suspect-history index ready in %.1fs", time.time() - started)
    except Exception as e:
        # Not fatal: the index rebuilds lazily on first use. Worth a line, because
        # silently falling back means the cold-start penalty is back without anyone
        # being told.
        LOG.warning("Suspect-history index not prebuilt (%s); "
                    "the first case dossier will build it", e)

    # After the corpus, not before: retention touches only the runtime logs, and a
    # failure here must never delay the service becoming ready.
    _enforce_retention_on_boot()


# Kick the heavy load into the background as soon as the module is imported by gunicorn.
threading.Thread(target=_warmup, daemon=True).start()

if __name__ == '__main__':
    port_str = os.environ.get('X_ZOHO_CATALYST_LISTEN_PORT', '9000')
    if not port_str:
        port_str = '9000'
    try:
        port = int(port_str)
    except ValueError:
        port = 9000
    app.run(host='0.0.0.0', port=port)
