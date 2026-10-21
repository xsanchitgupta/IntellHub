"""Keep the test suite out of the runtime sinks.

The suite was writing to the real `audit.jsonl`, `predictions.jsonl`,
`outcomes.jsonl` and `contests.jsonl`. Two consequences, both of which cost real time
before the cause was found:

1. **Order-dependent failures.** `tests/test_api.py` imports `main`, and `main`
   populates missing env vars from `app-config.json` so a local run behaves like the
   deployed one — including `AUDIT_HMAC_KEY`. Test modules that do not import `main`
   run without it. Both append to the same audit log, so the file ends up holding
   keyed and unkeyed digests, and `verify_chain` correctly reports it as broken. The
   chain was right every time; the suite was the thing at fault.

2. **A polluted developer log.** A day of running tests left thousands of synthetic
   accesses in the same file that is supposed to record who read whose record.

Redirecting the sinks per session fixes both. This runs at collection time, before any
test module imports `audit` or `main`, which is the only point at which it can work —
all four modules read their path from the environment once, at import.
"""
import os
import tempfile

_SINK_DIR = tempfile.mkdtemp(prefix="ksp-test-sinks-")

# A key of its own, so the suite never depends on whether app-config.json happens to
# carry one — that difference is exactly what made the failures look intermittent.
# No test may reach a live news source.
os.environ.setdefault("NEWS_FETCH", "off")
os.environ.setdefault("AUDIT_HMAC_KEY", "test-suite-key-not-a-real-secret")

for _var, _name in (
    ("AUDIT_LOG_PATH", "audit.jsonl"),
    ("PREDICTION_LOG_PATH", "predictions.jsonl"),
    ("OUTCOME_LOG_PATH", "outcomes.jsonl"),
    ("CONTEST_LOG_PATH", "contests.jsonl"),
    ("SURVEILLANCE_LOG_PATH", "surveillance_decisions.jsonl"),
    ("COPILOT_CONVERSATIONS_PATH", "copilot_conversations.json"),
    ("FIR_REGISTRY_PATH", "registered_firs.jsonl"),
    ("FIR_PHOTO_DIR", "fir_photos"),
    ("CAMERA_STREAMS_PATH", "camera_streams.json"),
):
    os.environ[_var] = os.path.join(_SINK_DIR, _name)
