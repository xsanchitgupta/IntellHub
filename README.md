# KSP IntelHub — Karnataka State Police SCRB AI Intelligence Platform

> **State Crime Records Bureau (SCRB), Karnataka State Police (KSP)**  
> Next-Generation AI & Analytical Ecosystem for Proactive Policing, Network Link Prediction, and Automated Crime Intelligence.

---

## 📌 Executive Summary

The **Karnataka State Police (KSP)** maintains extensive crime records capturing incidents, offenders, victims, vehicles, and judicial statuses across Karnataka. **KSP IntelHub** transitions SCRB operations from manual, siloed Excel reporting to an automated, AI-driven intelligence platform.

### Core Problems Addressed:
1. **Data Silos & Manual Reporting**: Unified 26 relational database tables and registries into a single real-time intelligence core.
2. **Lack of Advanced Analytics**: Integrated machine learning models (KDE hotspots, DBSCAN crime zones, Ridge+Fourier forecasting, Adamic-Adar network link prediction, TF-IDF narrative classification).
3. **Reactive vs. Proactive Policing**: Automated patrol dispatch recommendations based on predicted weekly crime spikes and high-risk offender recidivism indices.
4. **Deep Relational Intelligence**: Cross-domain synthesis connecting suspects, co-offenders, stolen vehicles, cyber fraud rings, and Rowdy-Sheeter registries.

---


## 🚀 Live Deployment

| Surface | URL |
| :--- | :--- |
| **Web client** | https://ksp-intelligence-60073837993.development.catalystserverless.in/app/index.html |
| **API** | https://ksp-intelhub-core-50043016098.development.catalystappsail.in |

Both live. `/serverless/api/health` reports 18,000 cases, 9,000 offenders and a
27,959-node / 61,567-edge knowledge graph, and all seven models load from
committed artifacts rather than retraining.

### Deploying — read this first

Two things AppSail needs are **gitignored**, so a clean checkout deploys
"successfully" and then refuses to start with a message that names neither:

    Execution failed. Please check the startup command or port.

* **`appsail/_vendor/`** — Catalyst does *not* install `requirements.txt`
  server-side. Dependencies must be vendored as Linux wheels. Without it the
  container cannot import flask.
* **`appsail/app-config.json`** — holds live keys, so only
  `app-config.example.json` is committed. The `command` must keep its
  `PYTHONPATH=_vendor` prefix.

**Pinned versions are load-bearing.** The registry refuses any artifact built by
a different scikit-learn than the runtime and retrains in-process instead. Seven
models retraining inside a 512 MB container exhausts it and the container
thrashes. So `requirements.txt` pins exactly what the artifacts were trained
with — Python 3.13 / scikit-learn 1.7.2 / numpy 2.2.6 / pandas 2.3.2 — and
`/serverless/api/models` shows `"source": "artifact"` for all seven when this is
right.

```bash
# 1. vendor Linux wheels matching the stack in app-config.json
python -m pip install -r appsail/requirements.txt -t appsail/_vendor     --platform manylinux2014_x86_64 --python-version 3.13     --implementation cp --only-binary=:all:

# 2. preflight — checks _vendor, wheel tags, version drift and the command
cd appsail && python predeploy_check.py

# 3. deploy
catalyst deploy --only appsail
catalyst deploy --only client      # bump web/client-package.json version first
```

The client and the API are on **different Catalyst domains** — the client domain
does not proxy `/serverless/*` — so `web/.env.production` pins the API origin.
CORS across the two is handled by the gateway.

### Access control and audit

Two shapes of response, chosen to fit what is being asked for:

| Surface | Signed out | Rationale |
| :--- | :--- | :--- |
| Forecasts, hotspots, district indices, lifecycle funnel, model registry | **Open** | Management statistics. They identify nobody, and the platform has to be legible to an evaluator with no credentials |
| Aggregates that *contain* a person-level block (`/stats`, `/analytics`) | **Pseudonymised** | The block keeps its shape — band distribution, counts — with names replaced by secret-derived pseudonyms. The control is visible rather than hidden behind a 403 |
| Dossiers, registries, co-offender graph, officer roster, copilot | **403** | There is no partial version of an individual's file worth rendering |
| `/sync`, `/settings`, `/audit-log*`, `/retention/*`, `/subject-report/<id>`, `/contests` | **Supervisor** | Administrative mutation and access review |

Enforcement is a decorator applied at the route, plus one property test
(`tests/test_api.py::test_no_offender_name_leaks_to_anonymous_callers`) that sweeps
every route without credentials and fails if any real registry name appears in any
response body. A per-route assertion only covers the routes someone remembered to
list; the sweep keeps holding as routes are added.

**Every person-level access is audited** — actor, role, the *subject's* identifier,
the stated reason, IP, result — to stdout as JSON and to `audit.jsonl`. Reading the
audit log is itself audited. Callers may supply `X-Access-Reason`; accesses without
one are recorded as `reason_given: false` so unjustified lookups are visible in
review rather than indistinguishable. `AUDIT_REQUIRE_REASON=1` makes it mandatory.

**Person names never reach the LLM providers.** The copilot injects real records
into prompts sent to Groq/NVIDIA — third-party services outside India. Names are
replaced with per-request aliases (`[PERSON_1]`) before egress and restored locally
in the answer, so the model keeps referential consistency and the provider never
sees a person. See `appsail/privacy.py` for the limits this does *not* cover.

Demo accounts are active only while `AUTH_ACCOUNTS` is unset. Passwords are stored
as scrypt hashes, `/auth/login` locks out after 8 failures in 5 minutes, and the
API does **not** serve credentials — the passwords below are documented here for
reviewers and nowhere else:

| Username | Password | Role |
| :--- | :--- | :--- |
| `analyst` | `scrb-analyst` | analyst — aggregate only |
| `io` | `scrb-io` | investigator — adds case and dossier access |
| `sp` | `scrb-sp` | supervisor — full watchlist and audit log |
| `dgp@ksp.gov.in` | `scrb-dgp` | State DGP — statewide, every permission |
| `admin@ksp.gov.in` | `scrb-admin` | Admin — platform settings, sync, retention |
| `sp.bengaluru@ksp.gov.in` | `scrb-sp` | District SP (Bengaluru Urban) — district cases, CCTV decisions, in-district watchlist names |
| `dsp.mysuru@ksp.gov.in` | `scrb-dsp` | Deputy SP (Mysuru) — district cases, verify/escalate CCTV matches |
| `sho.koramangala@ksp.gov.in` | `scrb-sho` | SHO, Koramangala PS — station cases, verify CCTV matches |
| `si.koramangala@ksp.gov.in` | `scrb-si` | SI, Koramangala PS — station cases, acknowledge alerts |
| `analyst@ksp.gov.in` | `scrb-analyst` | SCRB Analyst — aggregates only |

The web client now opens on the **sign-in page** (`/`), with one-click demo personas;
the command overview moved to `/overview`. Each role carries a tier, a list of
permissions and a jurisdiction (state / district / station) inside its signed token:
list views, Kavya's answers, CCTV alerts and FIR registration are all cut to that
jurisdiction server-side. `GET /serverless/api/auth/roles` publishes the matrix.

**New in this release**

* **Register FIR** (`/fir/new`, `POST /serverless/api/fir/register`) — a four-step
  CCTNS-schema form. Every code is checked against the master tables; Crime No. /
  Case No. follow the corpus format (category · district · station · year · serial);
  accused can be linked to the offender registry; registrations persist to
  `FIR_REGISTRY_PATH` (outside `data/`, so trained models stay valid) and appear at
  once in cases, the network graph, analytics and Kavya.
* **Crime Trend Forecast** (Analytics) — annual FIRs per district and year from a
  partial-pooling ridge regression, backtested against "same as last year", the
  district mean and a per-district line, with an 80% interval whose coverage is
  measured out of sample. Picking a past year shows the backtest.
* **When crime happens** (Overview) — hour-of-day × day-of-week heatmap with a
  Holm-corrected chi-square test, so an even spread is reported as one.
* **Live News** (`/news`) — Karnataka crime headlines from GDELT and Google News
  (plus `NEWSAPI_KEY` / `GNEWS_API_KEY` when set), tagged by district and crime type.
* **By occupation** (Analytics) and the **case-flow Sankey** (Lifecycle).
* **Network** rebuilt: search, filters, path finding, syndicates, 2D/3D.
* **Live CCTV**: attach a real HLS / WebRTC (WHEP) / MJPEG / YouTube live source to
  any camera (`CAMERA_STREAMS` or `CAMERA_STREAMS_PATH`); a tile says LIVE only when
  frames arrive, and falls back to its recording otherwise.
* **Kavya**: pause/resume mic, stop speaking, click outside to close; answers
  annual forecasts and time-of-day questions.

**Before any deployment holding real records**, set `AUTH_ACCOUNTS`
(`user:scrypt$salt$key:role`, generate with `python -c "import auth;
print(auth.hash_password('...'))"`), `AUTH_SECRET`, and `AUDIT_HMAC_KEY` (a long
random value — without it the audit chain is unkeyed and someone with write access to
the log could recompute it). All three are declared empty in
`app-config.example.json`, and `predeploy_check.py` warns when `AUDIT_HMAC_KEY` is
missing. `/auth/me` reports
`insecure_cleartext_accounts` if any configured account still stores a plain
password. The LLM copilot also needs real `GROQ_API_KEY` / `NVIDIA_API_KEY` values
in `app-config.json`; the placeholders ship disabled.

**Does the model still work?** Drift monitoring (`/api/models/drift`) answers a
narrower question than it appears to: it reports that the *inputs* moved, never that
the *answers* got worse. A model can hold `stable` on every feature while its
accuracy collapses — a change in charging practice, a new court, a shift in what gets
recorded.

The only way to answer the real question is to write down what was predicted, wait,
and write down what happened. `POST /api/models/outcome` records a result;
`GET /api/models/performance` joins it to the score that preceded it and reports
realised Brier, per-band calibration, and a skill score against the only model that
needs no data — always predicting the base rate. Outcomes are matched to the most
recent prediction *at or before* the observation, never a later one, because scoring
a subject again after their case resolved and then measuring against that score
manufactures accuracy out of hindsight.

**No outcome from operational use has been recorded.** What the loop does contain is
a replay: `python replay_outcomes.py --apply` scores the held-out window through the
serving artifact and feeds its real recorded results back in. That is worth doing for
a reason beyond the demo — offline evaluation scores a model inside the training
script, while serving loads a joblib and scores a frame rebuilt elsewhere, and those
two paths can silently diverge. Comparing them catches training/serving skew:
measured, both models agree with their published ROC-AUC to +0.0000.

Every replayed entry carries `source="replay"`, `realised()` reports the breakdown by
source, and the models page says so above the numbers — so a replayed figure can
never be read as field performance.

**Audit integrity.** Every entry carries its sequence number, the digest of the entry
before it, and its own digest, so an edit, reorder or deletion breaks every digest
that follows. `GET /api/audit-log/verify` (supervisor-only, and itself audited)
recomputes the chain and names the first line that disagrees. Set `AUDIT_HMAC_KEY`
and the digests are keyed, so someone with file access cannot recompute the chain;
without it the chain still catches corruption and careless edits, and says so.

**What this is not.** A chain cannot detect its own truncation — dropping the newest
lines leaves a shorter chain that verifies perfectly — so that is checked against the
writer's own entry count while the process lives, and against the forwarded stdout
copy across restarts. Nor does any of it defend against a compromised application
process, which can still write false entries. A real deployment must still forward
those lines to an append-only store outside the application's trust boundary.

**Contestability.** `GET /api/subject-report/<id>` assembles everything the system
holds and asserts about one person — every score with its date, each model's stated
limits, who accessed the record and why, and any challenge raised. `POST /api/contest`
records a challenge (grounds required); the register is consulted on the serving path,
so a contested score reaches the officer marked. Filing does not suppress the score:
adjudication is the deploying authority's decision, and GOVERNANCE.md lists it as the
open gap.

**Retention and erasure.** `GET /api/retention` publishes what personal data is held,
for how long, and why. `POST /api/retention/erase` removes a subject's scores and
recorded outcomes; `POST /api/retention/purge` drops anything past its schedule. Both
preview by default and require `{"apply": true}` to write. Erasure deliberately does
not touch the access log — that records who viewed a file, and a log the subject could
delete would let anyone who improperly viewed a record destroy the proof by filing in
their name. A retention pass runs on every container start when
`RETENTION_ENFORCE=1`, and logs the backlog even when it is unset — AppSail has no
scheduler, so a restart is the only recurring event this application can hook.
GOVERNANCE.md lists what that still leaves open.

## ⚖️ Governance

What this platform is for, what it must not be used for, and the limits it operates
under are stated in **[GOVERNANCE.md](GOVERNANCE.md)** — and, more usefully, served
next to the numbers they qualify. Every model carries a card in the registry giving
its intended use, out-of-scope use, the threshold anything acts on, what to monitor,
its retrain cadence, owner and review date. The `/models` page renders them beside
the metrics, because governance in a separate document drifts from the system it
governs.

The machine-readable source is `MODEL_CARDS` / `GOVERNANCE` in
[`appsail/ml/config.py`](appsail/ml/config.py); a model published without a card
raises a warning in its own training report.

Three things are worth stating here too:

* **No output is evidence.** Risk scores, conviction outlooks and link predictions
  allocate attention. None may be presented to a court as such.
* **No output triggers an action.** Every threshold orders a queue for a person to
  work; none authorises an action against an individual.
* **Known gaps are listed, not omitted** — challenge adjudication, scheduled
  retention enforcement, recorded outcomes and DPIA sign-off are all absent, and
  GOVERNANCE.md says so. `DPIA.md` carries the assessment as far as the code allows
  and marks the rest `[AUTHORITY]` rather than inventing it.

---

## 🚀 Key Platform Features & ML Models

Every figure below is measured on a **held-out window later in time** than everything
the model learned from, and reported next to the trivial baseline it has to beat.
The full evidence — split dates, confusion matrices, calibration curves, feature
importances and the warnings each run raised — is served at `/models` and lives in
[`appsail/artifacts/model_registry.json`](appsail/artifacts/model_registry.json).

> **Read this before quoting a number.** The corpus is CCTNS-schema-shaped
> *simulated* data with a documented data-generating process
> ([`simulate_outcomes.py`](appsail/simulate_outcomes.py)). These scores show that the
> pipeline can recover a structure we deliberately injected — the right thing to
> verify before real records exist — and are **not** claims of field performance on
> live KSP data. The same pipeline runs unchanged on a real extract by pointing
> `ml/dataset.py` at it.

| Model | Task | Result | Beats |
| :--- | :--- | :--- | :--- |
| FIR Narrative Classifier | 10-class text classification | **accuracy 0.808**, macro-F1 0.808 | majority class 0.103 |
| Crime Volume Forecast | 8-week ahead weekly counts | **MASE 0.640**, 77% interval coverage | naive 0.911, seasonal-naive 0.686 — *still loses to* a 4-week moving average (0.634) |
| Recidivism Risk | re-offence within 365 days | **ROC-AUC 0.709**, PR-AUC 0.616 | base rate 0.500 |
| Trial Outcome | conviction vs acquittal | **ROC-AUC 0.674**, PR-AUC 0.600 | base rate 0.500 |
| Case Progression | chargesheet within 180 days | **ROC-AUC 0.646** | base rate 0.500 |
| Case Pendency (survival) | days to chargesheet, right-censored | **C-index 0.611**, integrated Brier 0.209 | Kaplan-Meier IBS 0.241 |
| Geospatial Hotspots | unsupervised clustering | **silhouette 0.638**, 60 zones, Davies-Bouldin 0.362 | — (no ground truth) |

> **Several of these numbers went *down* in the 31-district rebuild, and that is the
> point.** The earlier corpus had 800 offenders carrying 5,000 cases, so 98% of the
> registry were repeat offenders and "will this person reoffend?" was nearly always
> yes — an easy question that flattered the score. The rebuilt corpus has 9,000
> offenders, 29% of whom appear exactly once, which is the shape of a real docket.
> Recidivism fell from 0.758 to 0.709 and trial outcome from 0.780 to 0.674 because
> the task got harder and more honest, not because the models got worse. The
> forecast *improved* (MASE 0.766 -> 0.640) on the larger corpus.

### 0. 🗺️ Administrative Geography — All 31 Karnataka Districts

The first credibility test this platform has to pass is not a model score. It is
whether a SCRB officer scrolling the district filter sees their own district.

The corpus previously ran on **ten** districts, and two of the ten were not
districts at all:

* **Mangaluru** is the headquarters of Dakshina Kannada and a separate police
  commissionerate. Listing it *beside* Dakshina Kannada double-counted the same
  territory.
* **Hubballi-Dharwad** is a commissionerate covering the twin cities. The revenue
  district is **Dharwad**.

[`appsail/karnataka.py`](appsail/karnataka.py) is now the single source of truth,
and every consumer — the generator, the outcome simulator, the socio-economic
overlay — reads from it, so the geography cannot drift between them.

| | Before | Now |
| :--- | :--- | :--- |
| Districts | 10 (2 of them not districts) | **31**, the real set incl. Vijayanagara |
| Police commissionerates | not modelled | **8**, kept separate from the district list |
| Police stations | 28 | **140**, real town/locality names |
| District case volume | `random.choice()` — uniform | **population-weighted** (2011 Census) |
| Cases | 5,000 | **18,000** |
| Offenders | 800 (98% repeat offenders) | **9,000** (29% appear exactly once) |

**Why the weighting matters.** Drawing districts uniformly gave Kodagu (5.5 lakh
people) the same annual caseload as Bengaluru Urban (96 lakh). Every per-capita
rate, district ranking and hotspot comparison computed on top of that was
meaningless. Case volume now tracks population at r > 0.9, and the largest
district carries 18.8× the smallest — matching the 17.4× population ratio.

**What is real vs. modelled**, stated per column rather than blurred:

* **Real (2011 Census / KSP structure):** district names and count, the 8
  commissionerates, population, urbanisation, literacy. Yadgir genuinely has the
  lowest literacy in Karnataka (51.8%) and Dakshina Kannada among the highest
  (88.6%).
* **Approximate:** district-HQ coordinates, accurate to roughly a kilometre. They
  anchor the incident scatter; they are not survey-grade boundaries and the
  platform never claims they are.
* **Derived estimates:** per-capita income, unemployment, police density, youth
  share and slum share, computed from the census series by documented formulas in
  [`generate_socioeconomic.py`](appsail/generate_socioeconomic.py). Internally
  coherent and correctly ordered, but not measured figures.

A guard test asserts each district's incident centroid is nearest **its own**
headquarters — the property that actually matters — rather than a fixed distance
threshold, because real geography defeats thresholds: Koppal and Hosapete
(Vijayanagara) are genuinely ~20 km apart.

### 1. 📈 Crime Volume Forecasting (RidgeCV + Fourier Seasonality)
* Weekly incident counts up to 8 weeks ahead, state-wide, per district and per crime type.
* **Walk-forward backtested** across 6 expanding-window origins — the model is re-fitted
  at each origin and scored only on the weeks after it, the way a forecast would
  actually have been issued.
* Prediction intervals are built from **out-of-sample backtest residuals at each
  horizon step**, not from in-sample residuals, so the stated 80% is measurable —
  realised coverage is 77%.
* Reported honestly: it beats naive, seasonal-naive and drift, but **does not beat a
  4-week moving average**. At this weekly volume the series is close to
  noise-dominated, and the registry says so.

### 2. 📍 Geospatial Hotspot Zones (Haversine DBSCAN, parameters selected)
* `eps` and `min_samples` are **chosen by a 32-point sweep** scored on silhouette and
  noise fraction, not hard-coded. The full sweep is published so the choice is auditable.
* Cluster count and zone radius are constrained to what a command centre can staff:
  silhouette alone rewards both hundreds of four-incident micro-clusters *and* one
  district-sized blob, neither of which is a patrol plan.
* Incidents left unclustered are reported as genuinely dispersed rather than forced
  into an invented hotspot.

### 3. 🕸️ Link Analysis, Syndicate Detection & Link Prediction
* Force-directed network across suspects, FIRs, officers, cyber identifiers and vehicles,
  with 1st/2nd-degree sub-graph extraction.
* **Adamic-Adar link prediction** surfaces emerging co-offender alliances.
* **Louvain community detection** runs on the *offender projection* of the graph and
  requires repeat co-offending, so a "syndicate" is a set of people who work together
  more than once — not a set of case nodes.

### 4. ⚖️ Recidivism Risk (supervised, calibrated)
* Predicts whether an offender is named in a **new case within 365 days** of an
  observation date. Training and test panels are observed a year apart so their
  outcome windows never overlap.
* Undated snapshot columns (`past_fir_count`, `risk_level`, `active_warrant`) are
  **excluded** — they may already count the offences being predicted. Prior counts are
  recomputed from dated case and arrest records. An ablation quantifies exactly what
  those columns would have added.
* The watchlist index is a **percentile rank** with the calibrated probability shown
  alongside, and never displays 0% or 100%.
* Reported honestly: the previous hand-weighted rubric, restricted to the same dated
  information, scores 0.708 — statistically indistinguishable from the learned model's
  0.709. Reading the undated snapshot columns lifts that same rubric to 0.772, which is
  exactly the inflation those columns were excluded to prevent.

### 5. 📝 FIR Narrative Crime Classifier (TF-IDF + Logistic Regression)
* Reads FIR free text and assigns a crime head, so uncategorised reports can be triaged.
* Hyper-parameters are grid-searched **inside the training window only**; the test
  window is touched once.
* A **leakage probe** flags any token that appears in only one class, and its findings
  are published rather than suppressed.
* Errors fall where a human clerk's would: Murder↔Homicide↔Assault, and the property
  trio. Cyber fraud separates cleanly (precision 1.00, recall 0.99) because its
  vocabulary genuinely differs.

### 6. ⚖️ Trial Outcome & Case Progression
* **Trial outcome** predicts conviction vs acquittal for a named accused, split so no
  individual appears on both sides *and* the test half is later in time. Performance is
  broken out by age band and gender so a model that works overall but fails a subgroup
  cannot pass unnoticed.
* **Case progression** predicts a chargesheet within 180 days of registration — a
  time-boxed target using real chargesheet dates, replacing the undefined
  `status == 'Closed'` snapshot. Officer `closure_rate`, `cases_assigned` and
  `commendations` are **banned features**: they are aggregates of case outcomes and
  contained the label.

### 6b. ⏳ Case Pendency — Survival Analysis on the Live Docket
* **Models right-censoring instead of discarding it.** The binary case-progression
  model above must exclude every case registered in the last 180 days of the extract,
  because those cases have not had time to answer "chargesheet within 180 days?".
  Those are precisely the files sitting open on a desk today — it is structurally
  incapable of scoring the live docket. **1,314 cases** on this corpus, and **56.5% of
  the test window**, are censored rows that it either drops or mislabels.
* **Discrete-time survival via person-period expansion** — sklearn only, no new
  dependency. Each case becomes one row per 30-day interval it survived into; a
  classifier on those rows *is* an estimator of the discrete hazard `h(k | x)`, and
  chaining gives the survival curve `S(t | x)`.
* **One model answers every horizon.** The 60 / 90 / 180 / 360-day figures are read
  off the same curve rather than coming from four separate classifiers that could
  contradict each other.
* **Beaten against Kaplan-Meier**, the covariate-free curve — the only honest
  reference for a survival model. C-index 0.611 against a coin-flip floor of 0.500,
  and integrated Brier 0.209 against the baseline's 0.241. It has to win on *both*
  the ranking metric and the proper scoring rule, and it does.
* **Conditional on time already served.** Risk is quoted from today, not from
  registration, and is interpolated *within* the 30-day interval — otherwise a case
  open 35 days reads as certain to still be open at day 60 purely because both fall
  in the same grid cell.
* **It refuses to score 88% of the open docket.** Cases open longer than the model's
  360-day observation grid are reported as a counted structural backlog and are
  deliberately **not** given a probability: no comparable training case was still
  unresolved at that age, so a number there would be extrapolation dressed up as a
  measurement. Printing "97% risk" against a six-year-old file launders an
  administrative fact as a model output. The refusal is the feature.
* **Triage ranks on expected remaining days, not breach probability.** A case at day
  359 scores ~100% against its 360-day horizon simply because one day is left — true,
  but useless. Remaining time discriminates across the whole docket (167–338 days on
  this corpus) and surfaces newly-registered cases predicted to drag, while
  intervening still changes the outcome.
* Served at `/serverless/api/pendency` and rendered at [`/pendency`](web/src/app/pendency/page.tsx).

### 7. 🤖 Kavya — AI Investigative Copilot (tool-calling retrieval over the whole corpus)
* **It reads the records instead of a summary.** `appsail/copilot_engine.py` gives the copilot
  retrieval tools over every table: a full case dossier (narrative, accused with priors and flags,
  victim, complainant, IO, arrests, chargesheet, court stage, stolen vehicles, cyber identifiers,
  CCTV, Dial-112, pendency score, similar-MO cases), a person profile (every case they are named in,
  co-offenders, syndicates, recidivism drivers, court outcomes), officers, every side registry,
  filtered counts and rankings by district / station / crime / year / month / time of day / weapon,
  shortest paths through the link graph, every ML output, and the platform's own documentation
  (`appsail/copilot_knowledge.py`).
* **LLM tool-calling loop.** Groq `llama-3.3-70b-versatile` (NVIDIA fallback) decides which tools to
  call and chains them. Records the question names are resolved and pre-fetched first, so a typical
  answer costs one round trip.
* **Works with no API key.** The same entity resolution and tools drive a deterministic engine that
  composes the answer when no provider is configured or all of them fail.
* **Knows the platform as it is today.** `appsail/build_project_knowledge.py` indexes every
  Markdown document, backend module docstring, API route (with the role it requires) and web page
  into `appsail/artifacts/project_knowledge.json`. The copilot searches it, rebuilds it in memory
  in a full checkout whenever a source is newer, and reads the running system directly for live
  routes and tables. **After changing docs, routes or pages, run
  `python appsail/build_project_knowledge.py` and commit the result** — CI fails otherwise.
* **Scope.** It searches only IntelHub's own role-gated, audited records — never the internet,
  social media, Aadhaar or any outside database.
* **Conversation memory.** "this case", "he", "her" resolve to the record on screen or the subject of
  the previous answer.
* **Same safeguards as before, applied to more data.** Names in every tool result are aliased before
  egress and restored locally; every case, offender and officer id in a reply is verified against
  the corpus; questions of guilt, requests for action against a person, and watchlists of names are
  refused.
* **Siri-style voice UI.** A live waveform that follows your microphone level and each word Kavya
  speaks, an animated orb, a screen-edge glow in voice mode, a conversation transcript with clickable
  case / offender ids, and a strip showing which records each answer was read from. English and Kannada.
* **Answers open questions and keeps the thread.** "What's going on in Bangalore right now?" gets a live
  situation brief (last 7/30 days, crime mix, spikes, forecast, patrol risk, live camera alerts). City
  names people actually say — Bangalore, Mysore, Hubli, Mangalore — resolve to districts, so "show the
  latest chain snatching cases in Bangalore" opens the Bengaluru list. Follow-ups are rewritten against
  the conversation: "what about Belagavi?", "and 2023?", "show them on the map".
* **Copilot tab (`/copilot`).** A full-page workspace: conversations saved to the officer's account
  (search, pin, rename, delete), each thread is its own context, answers carry the records they used and a
  one-click "open this" action, voice dictation, English / ಕನ್ನಡ.

### 8. 🗺️ Crime Map — deck.gl command map: 3D / heatmap / districts / satellite
* deck.gl draws every layer over keyless CARTO basemaps (dark or light) or Esri imagery for satellite —
  no API key. Neighbouring states are dimmed and Karnataka carries a glowing outline, so the state is the
  subject of the screen.
* **3D:** tilted hexagonal crime columns whose height and colour encode density (square-root scaled, so
  Bengaluru does not flatten every other district); the bin size adapts to zoom and the columns rise when
  they appear. **Heatmap:** severity-weighted heat, individual FIRs, DBSCAN zones. **Districts:** a
  choropleth of all 31 districts — real boundaries, extruded by the chosen metric (cases, per-100k, open,
  deaths, last 30 days, risk index, cameras), labels de-cluttered so the busiest district wins.
* **Full screen for presenting:** the map and its insight panel go full screen together (button or `F`);
  Esc or the red *Exit full screen* button leaves, the panel can be hidden, and the state is re-framed
  whenever the stage changes size.
* Pulsing KDE predicted hotspots, emerging-spike markers, live camera alerts, a numbered movement trail
  for a tracked person (`/map?track=SUS-…`), a 24-hour time-of-day playback, and a cinematic tour.
* "This view" analyses whatever is on screen: concentration, peak hour, busiest window, open share.
* Boundaries are Census 2011 district polygons; **Vijayanagara** (2021) is built from its five constituent
  2011 taluks — see the `source` field in `web/public/geo/karnataka-districts.geojson` for the one
  limitation (Kampli).

### 9. 📹 CCTV & ANPR Surveillance Command (`/surveillance`)
* A simulated network of 590 cameras across all 31 districts, Bengaluru-heavy, placed at named public
  landmarks (transit hubs, junctions, toll plazas, markets) — **not** the KSP Safe City inventory.
* **Face matching:** every sighting is matched 1:N against the 9,000-person offender gallery (cosine
  similarity, top-3, threshold + margin). The threshold was chosen for < 0.5 % false matches on
  look-alikes; measured rates are served at `/surveillance/model` and shown on the page, including the
  drop at night and on low-resolution cameras.
* **ANPR:** reads are matched to the stolen-vehicle registry exactly and with an OCR-aware edit distance
  (0/O, 1/I, 8/B, 5/S … cost less), so a mangled read still finds the car.
* **Alerts, not actions:** P1 (warrant, exact stolen plate, watchlist) and P2 matches queue for an officer,
  who must write what they checked to verify or reject; every decision is logged. Personal watchlists,
  sound / desktop notifications and a nav bell with toasts. Faces with no registry match are counted and
  discarded, never identified.
* **Feeds play real footage with real detections.** Each camera plays a 20-second window of street
  video recorded in Bengaluru in 2024–25 (Wikimedia Commons, CC BY-SA 4.0 / CC BY 3.0, credited in the
  camera view and under the wall); cameras named after a place we have footage of — Brigade Road, MG Road,
  Hebbal, Banashankari, Shivajinagar, Mekhri Circle — show that place. The boxes are YOLO11s detections
  with ByteTrack tracking computed on that footage (`web/public/cctv/tracks/`), replayed in step with the
  video, with live vehicle / people counts. Registry matches appear as an alert strip on the tile and are
  never drawn on anyone in the picture — nobody filmed is a registry person. An officer can still point any
  camera at a real stream URL.

### 10. 🔎 Investigation Workbench (`/investigate`, `appsail/investigation.py`)
Every finding is computed from the records and cites the FIR or person it rests on — nothing is
generated by the language model.
* **Record integrity (18 checks).** Dates that contradict each other (incident after registration,
  arrest before the offence, chargesheet before the FIR), status that disagrees with the chargesheet or
  verdict, overdue hearings, a recovered vehicle reported before its theft, one person on file with
  two ages, two people sharing a fingerprint record, and **the wrong penal code for the offence date**.
  Each issue comes with the question the IO should answer. About 11,000 FIRs in the corpus carry one.
* **IPC → BNS cross-walk (`appsail/statutes.py`).** The BNS replaced the IPC on 1 July 2024 and the date
  of the offence decides which applies. The table is checked against the CAPT Bhopal correspondence
  table; FIR registration refuses a section from the wrong code and names the right one
  ("Book it as BNS 303(2)").
* **Cross-station crime series.** FIRs of one crime type that share at least 3 of weapon, vehicle,
  target and time of day within 90 days and 50 km (union-find), or that name the same accused, across
  at least two police stations — 276 MO series and 2,257 offender series on the shipped corpus.
* **Geographic profile (Rossmo).** For a series, a 60×60 probability surface of where the offender is
  likely based (f = g = 1.2, buffer = half the mean nearest-neighbour distance), computed on the densest
  40 km cluster and refused when the sites are too scattered to mean anything.
* **Competing hypotheses (Heuer ACH).** For each FIR, the named accused and the strongest unnamed
  candidates scored against the evidence — MO history, district, crime type, time of day, custody on the
  date — ranked by the least inconsistent evidence, never by demographics.
* **Under-reporting.** Districts recording far less crime than comparable districts predict
  (leave-one-out ridge regression on socio-economic indicators, z ≤ −1.5), and Dial-112 calls about a
  cognizable offence that never became an FIR (Wilson 95 % interval against the state rate).
* **Who should investigate.** A shortlist of IOs for a new FIR by specialisation, posting, closure rate
  and open caseload, with the reasons, shown on the FIR form. Gender, age and disability are excluded.
* **One person across every register.** Arrests, FIRs, chargesheets, hearings, camera sightings and
  vehicle records on one timeline, with identity conflicts flagged.
* **Court-ready brief (`/case/brief`).** A printable sheet for the prosecutor: particulars, sections
  against the code in force, facts, parties (no caste or religion), proceedings, electronic evidence with
  a draft BSA §63 certificate (Part A / Part B, clearly marked as a draft), and the items to resolve.
* Kavya can call all of it (`investigate` tool), and a chat with the floating assistant on any page is
  saved to the officer's Copilot history, tagged with the page it began on.

---

## 📉 Runtime model operations

Offline evaluation describes a model on the day it was trained. These describe it
afterwards — the gap that matters more, because a model quietly applied to a
population it never learned from keeps returning confident numbers.

* **Prediction logging.** Every score handed to a person is recorded — model,
  subject, value, time — to stdout and `predictions.jsonl` (rotated at 16 MB). This
  is the raw material for a feedback loop: without a record of what was predicted,
  "was it right?" cannot be asked.
* **Drift detection.** Each tabular model stores the distribution of its own inputs
  and outputs at training time, and `GET /serverless/api/models/drift` reports
  Population Stability Index per feature against it — the standard credit-risk
  measure. Below 0.10 stable, 0.10–0.25 moderate, above 0.25 significant. The
  endpoint lists **every** model including the ones it cannot monitor (the text
  classifier, the forecast, the clustering) and why, so that silence never reads as
  safety.
* **Versioned artifacts and rollback.** `save_artifact` used to overwrite in place,
  so a bad training run replaced the working model with no way back. Every save now
  keeps the previous five under `artifacts/versions/<model>/`, and
  `registry.rollback(name)` steps back through them.

What this deliberately does **not** claim: drift shows the *inputs* moved, never
that the answers got worse. That needs outcomes, and nothing yet records what
actually happened to the people scored. It is listed as the largest open gap in
[GOVERNANCE.md](GOVERNANCE.md).

---

## 📤 Taking work out of the platform

Case registry and pendency docket views export to **CSV** or a **printable briefing**
(the browser's own print dialogue, so "Save as PDF" is already there — no 300 KB PDF
library). Both carry a provenance header recording when the extract was pulled, the
filters that produced it, and that the corpus is simulated. An extract that arrives
in a meeting with no indication of where it came from is exactly the artefact the
model registry exists to prevent.

---

## 🌐 Bilingual interface

Karnataka Police work in Kannada. The UI carries an EN / ಕನ್ನಡ switch, a real Kannada
webface (`Noto Sans Kannada`, previously absent so Kannada text fell back to whatever
the OS had), and `<html lang>` kept in sync with the choice — it was hardcoded to
`en`, which made screen readers announce Kannada with English phonemes.

**The dictionary is deliberately small.** It covers navigation and ordinary UI
chrome. Police terminology has conventions that general translation gets wrong, and
machine-translating legal vocabulary into a police tool and calling it localised
would be the same overclaim this codebase has spent its effort removing. A missing
key falls back to English rather than a placeholder, and `missingKeys()` reports
what is uncovered. Domain vocabulary needs a Kannada-speaking officer before any
real deployment.

---

## 🔬 Training & Evaluation Pipeline

Models are trained **offline**, not at server boot. One command produces both the
fitted estimators and the evidence:

```bash
cd appsail
python -m ml.train                      # all models -> artifacts/ + model_registry.json
python -m ml.train --only recidivism_risk   # partial run; other entries carried forward
python -m ml.train --data-dir ../real_extract
python -m pytest tests -q               # 331 guard tests
```

```
appsail/ml/
├── dataset.py     adapter: raw tables -> canonical frames  ← the only file real KSP data touches
├── splits.py      temporal / grouped / walk-forward splitters
├── baselines.py   the "could a trivial rule do this?" reference models
├── evaluate.py    metrics, confusion, calibration, lift
├── registry.py    artifact persistence + the portable JSON evidence file
├── serving.py     what Flask loads at boot
├── train.py       CLI
└── tasks/         one module per model
```

**Why the split matters.** Crime records are events on a timeline. A random
`train_test_split` lets a model learn from cases registered *after* the ones it is
scored on, which silently inflates every metric. Every supervised model here is split
on time instead.

**Artifacts vs evidence.** `*.joblib` files are a boot-time speed optimisation and are
refused if the runtime's scikit-learn differs from the training build — in which case
the app retrains in-process from the same code, so the two paths cannot disagree.
`model_registry.json` is plain, portable JSON, is committed, and is what the UI
renders; it is never recomputed at request time.

### What the evaluation actually found

Every model is scored on a held-out window **later in time** than everything it
learned from, and printed next to the trivial rule it has to beat. A model that
cannot beat its baseline is not quietly dropped — it ships with the finding attached.

| Model | Result | Baseline it had to beat |
|---|---|---|
| FIR narrative classifier | accuracy **0.808** | 0.103 majority class — the clearest win in the set |
| Recidivism risk | ROC-AUC **0.709** | 0.500 coin flip; in line with COMPAS at 0.68–0.71 |
| Case pendency (survival) | IBS **0.209**, C-index 0.611 | 0.241 Kaplan–Meier with no covariates |
| Conviction outcome | ROC-AUC **0.675** | 0.500 coin flip |
| Case progression | ROC-AUC **0.646** | 0.500 coin flip |
| Hotspot clustering | silhouette **0.638**, 60 zones | unsupervised; 21.7% left as noise rather than forced into a cluster |
| Weekly crime forecast | MASE **0.640** | 0.911 naive — **but see below** |

**Two findings we publish rather than bury.**

*The forecast is not as good as its headline.* MASE 0.640 beats the naive forecast,
but a paired bootstrap against a four-week moving average puts the 95% confidence
interval on the difference across zero. The series carries little forecastable signal
beyond its local level, and the registry says so in those words. A registry that never
warns is a registry nobody reads.

*We rejected a baseline that beat our own model.* A legacy-style scoring rubric scored
0.772 against our recidivism model's 0.709 — and we did not ship it. It reads
current-state columns that already reflect the outcome being predicted. Strip those
and the same rubric drops to 0.708. 0.709 is the honest number.

**Why this matters operationally.** Every one of these scores orders a queue — which
case to work next, which docket is about to breach its deadline, where to put a
patrol. None of them authorises an action against a named person, and the copilot
refuses in code when asked to.

The full registry — split dates, calibration curves, confusion matrices, per-horizon
metrics and the six warnings the training run raised — is committed at
`appsail/artifacts/model_registry.json` and served at `/api/models`.

---

## 🗄️ Relational Database Schema & Domain Registries

The platform integrates data across **26 relational database tables & registries**:

| Registry / Table | Description & Key Columns |
| :--- | :--- |
| **FIRs / Cases** | `case_id`, `crime_type`, `district`, `station`, `timestamp`, `latitude`, `longitude`, `severity_score`, `status` (`Under Investigation`, `Chargesheeted`, `Closed`), `fatalities`, `injured` |
| **Criminals Registry** | `criminal_id`, `name`, `age`, `gender`, `rowdy_sheeter` (boolean), `history_sheet` (boolean), `active_warrant` (boolean), `gang_affiliation`, `known_associates` |
| **Courts & Trials** | `case_id`, `chargesheet_status` (`Filed`, `Pending`), `conviction_status` (`Pending Trial`, `Convicted`, `Acquitted`), `court_name` |
| **Cybercrime** | `case_id`, `fraud_amount`, `cyber_identifier` (IP/IMEI/Bank Account), `victim_count` |
| **Stolen Vehicles** | `registration_no`, `vehicle_type`, `make_model`, `stolen_status` (`Stolen`, `Recovered`), `linked_case_id` |
| **Officers** | `badge_no`, `name`, `rank`, `station`, `district`, `cases_assigned`, `solve_rate` |
| **Missing Persons** | `person_id`, `name`, `age`, `district`, `missing_date`, `found_status` (`Missing`, `Found`) |
| **Complaints & CCTV** | Public petitions, Dial-112 logs, and CCTV feed coordinates |

> ℹ️ **Police Terminology Note**: A **Rowdy Sheeter** is an official legal classification maintained by police stations under Sections 95 & 96 of the *Karnataka Police Act & Police Manual* for habitual offenders involved in violence, extortion, or gang activities. Rowdy Sheeters are explicitly flagged via the `rowdy_sheeter` boolean attribute in the database.

---

## 🛠️ System Architecture

```
Datathon/
├── appsail/                 # Python Flask REST API & ML Intelligence Core (Port 9000)
│   ├── main.py              # Flask server, NetworkX graph engine, Copilot handler
│   ├── analytics.py         # ML pipeline: Ridge, DBSCAN, Adamic-Adar, TF-IDF, Recidivism
│   ├── requirements.txt     # Python dependencies
│   └── Dockerfile           # Backend container setup
│
├── web/                     # Next.js 16 / React 19 Frontend Dashboard (Port 3000)
│   ├── src/app/             # App Router pages (Overview, Cases, Map, Network, Analytics, Registry, Officers, Dispatch)
│   ├── src/components/      # UI components (FluidNav, AgentConsole, ForceGraph, Heatmap)
│   ├── package.json         # Node.js dependencies
│   └── Dockerfile           # Frontend container setup
│
└── docker-compose.yml       # One-command orchestration for full stack deployment
```

---

## ⚡ Quick Start Instructions

### Option A: Local Native Execution (Development)

#### 1. Backend Setup (Flask API Engine)
```bash
# Navigate to the backend directory
cd appsail

# Install Python dependencies
pip install -r requirements.txt

# (Optional) Set API key for AI Copilot
# Windows PowerShell:
$env:GROQ_API_KEY="your-groq-api-key"
# Linux/macOS:
export GROQ_API_KEY="your-groq-api-key"

# Start the Flask backend server on port 9000
python main.py
```
*Backend API will run at `http://127.0.0.1:9000`.*

#### 2. Frontend Setup (Next.js Dashboard)
```bash
# Open a new terminal and navigate to the frontend directory
cd web

# Install Node.js dependencies
npm install

# Start the Next.js development server
npm run dev
```
*Frontend dashboard will be accessible at `http://localhost:3000`.*

---

### Option B: Containerized Execution (Docker & Docker Compose)

To run the entire platform (Backend + Frontend) in isolated Docker containers with a single command:

#### Prerequisites:
* Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) with Docker Compose.

#### Launching the Stack:
```bash
# From the project root directory
docker-compose up --build
```

#### Stopping the Stack:
```bash
docker-compose down
```

* **Frontend Dashboard**: `http://localhost:3000`
* **Backend REST API**: `http://localhost:9000`

---

## 📡 API Reference Endpoint Overview

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/serverless/api/health` | System status, total loaded records & graph stats |
| `GET` | `/serverless/api/stats` | High-level KPI aggregations & crime category trends |
| `GET` | `/serverless/api/cases` | Paginated list of FIR cases with filtering & search |
| `GET` | `/serverless/api/network` | Focused network graph nodes & edges (`?suspect_id=`, `?case_id=`, `?q=`) |
| `GET` | `/serverless/api/heatmap` | Spatial crime coordinates, KDE hotspots, and DBSCAN zones |
| `GET` | `/serverless/api/analytics` | Returns ML predictions: forecast, DBSCAN zones, link predictions, recidivism |
| `POST` | `/serverless/api/chat` | AI Copilot query endpoint with dynamic 26-table database retrieval |
| `POST` | `/serverless/api/classify` | FIR narrative text crime category classifier |
| `GET` | `/serverless/api/investigate/case/<id>` | Integrity issues, IO questions, competing hypotheses, series and statute check for one FIR |
| `GET` | `/serverless/api/investigate/integrity` | Record-integrity audit, statewide or by district / check |
| `GET` | `/serverless/api/investigate/series[/<id>]` | Cross-station crime series; one series with members and geographic profile |
| `GET` | `/serverless/api/investigate/person/<id>/timeline` | One person across every register |
| `GET` | `/serverless/api/investigate/underreporting` | District under-recording and Dial-112 call → FIR conversion |
| `GET` | `/serverless/api/investigate/recommend-io` | IO shortlist for a station and crime type, with reasons |
| `GET` | `/serverless/api/statutes/crosswalk` | IPC ↔ BNS cross-walk; `?act=&section=&date=` looks one up |

---

## 🛡️ License & Attributions

Developed for the **Karnataka State Police (KSP) State Crime Records Bureau (SCRB)** Datathon. Built with Python, Flask, NetworkX, Scikit-Learn, Next.js, Tailwind CSS, Recharts, deck.gl and MapLibre.
