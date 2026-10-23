"""Pre-deploy preflight for AppSail.

Run this before `catalyst deploy --only appsail`:

    cd appsail && python predeploy_check.py

Why it exists
-------------
The two things AppSail needs most are both **gitignored**, so a clean checkout
looks complete and deploys "successfully" while the container refuses to start:

* `_vendor/` — Catalyst's managed Python runtime does **not** install
  `requirements.txt` server-side. Dependencies must be vendored into the build
  directory and placed on `PYTHONPATH`. Without it the container cannot import
  flask, and AppSail reports only:

      Execution failed. Please check the startup command or port.

  which says nothing about missing dependencies and sends you to inspect the
  startup command instead. This cost an afternoon of deploy cycles once —
  entrypoint form, memory tier, package size and the Dockerfile were each ruled
  out before the real cause surfaced.

* `app-config.json` — holds live API keys, so only `app-config.example.json` is
  committed. The real file drifts, and the drift that matters is the `command`
  losing its `PYTHONPATH=_vendor` prefix.

Exits non-zero on any hard failure so it can gate a deploy script.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "_vendor")
CONFIG = os.path.join(HERE, "app-config.json")
EXAMPLE = os.path.join(HERE, "app-config.example.json")

def _stack_pyver():
    try:
        stack = json.load(open(CONFIG, encoding="utf-8")).get("stack", "python_3_13")
    except Exception:
        stack = "python_3_13"
    return stack.replace("python_", "").replace("_", ".")


VENDOR_CMD = (
    "python -m pip install -r appsail/requirements.txt -t appsail/_vendor "
    "--platform manylinux2014_x86_64 --python-version " + _stack_pyver() + " "
    "--implementation cp --only-binary=:all:"
)

REQUIRED_MODULES = ["flask", "pandas", "numpy", "sklearn", "scipy",
                    "networkx", "gunicorn", "joblib", "openai"]

problems, warnings = [], []


def _has(name):
    return any(e.lower().startswith(name.lower()) for e in os.listdir(VENDOR))


# ---------------------------------------------------------------- vendored deps
if not os.path.isdir(VENDOR):
    problems.append(
        "appsail/_vendor/ is missing. Catalyst does NOT install requirements.txt "
        f"server-side; the container will fail to start.\n      Fix: {VENDOR_CMD}")
else:
    entries = os.listdir(VENDOR)
    missing = [m for m in REQUIRED_MODULES if not _has(m)]
    if missing:
        problems.append(f"_vendor/ is missing: {', '.join(missing)}\n      Fix: {VENDOR_CMD}")

    # openai 1.x passes a `proxies=` kwarg that httpx 0.28 removed, so that pair
    # must not be mixed. Derived from the pins rather than hardcoded, because the
    # constraint disappears once openai 2.x is used.
    pins = {}
    for line in open(os.path.join(HERE, "requirements.txt"), encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "==" in line:
            name, version = line.split("==", 1)
            pins[name.strip().lower().replace("-", "_")] = version.strip()

    def _vendored_version(dist):
        for e in entries:
            if e.lower().startswith(dist.lower() + "-") and e.endswith(".dist-info"):
                return e[len(dist) + 1:-len(".dist-info")]
        return None

    openai_pin = pins.get("openai", "")
    httpx_version = _vendored_version("httpx")
    if openai_pin.startswith("1.") and httpx_version and httpx_version >= "0.28":
        problems.append(
            f"openai {openai_pin} with httpx {httpx_version}: 0.28 removed the "
            "proxies= kwarg that openai 1.x passes, which breaks every LLM call "
            "on the server while local dev keeps working.")

    # The vendored tree must match the pins the artifacts were trained against —
    # a drift here makes the registry refuse every .joblib and forces an
    # in-process retrain that does not fit in the container.
    for dist, pin in pins.items():
        got = _vendored_version(dist)
        if got and got != pin:
            problems.append(f"_vendor has {dist} {got}, requirements pins {pin}.")

    # Windows extension modules in a Linux container are silently useless.
    pyds, sos = [], []
    for root, _, files in os.walk(VENDOR):
        pyds += [f for f in files if f.endswith(".pyd")]
        sos += [f for f in files if f.endswith(".so")]
        if len(pyds) > 3 and len(sos) > 3:
            break
    if pyds:
        problems.append(
            f"_vendor contains Windows extension modules ({pyds[0]} ...). "
            "Re-vendor with the --platform manylinux2014_x86_64 flags above.")

    # The interpreter tag in the vendored wheels has to match the configured
    # stack, or the container imports nothing.
    try:
        stack = json.load(open(CONFIG, encoding="utf-8")).get("stack", "")
    except Exception:
        stack = ""
    # python_3_13 -> "3.13" -> the "cpython-313" tag CPython puts in .so names.
    pyver = stack.replace("python_", "").replace("_", ".")
    want = "cpython-" + pyver.replace(".", "")
    if want and sos and not any(want in f for f in sos):
        problems.append(
            f"_vendor wheels are not {want} (stack is {stack!r}). Sample: {sos[0]}. "
            f"Re-vendor with --python-version {pyver}.")

# ---------------------------------------------------------------- app-config
if not os.path.isfile(CONFIG):
    problems.append("appsail/app-config.json is missing (gitignored). "
                    "Copy app-config.example.json and fill in the real keys.")
else:
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    command = cfg.get("command", "")
    if "PYTHONPATH=_vendor" not in command:
        problems.append(
            "app-config.json `command` does not put _vendor on PYTHONPATH, so the "
            "vendored dependencies will not be importable.\n"
            f"      Expected: {json.load(open(EXAMPLE, encoding='utf-8'))['command']}")
    if cfg.get("memory", 0) > 512:
        problems.append(f"memory is {cfg['memory']}; 512 is the maximum this plan accepts.")
    env = cfg.get("env_variables") or {}
    for key, value in env.items():
        if "REPLACE_WITH_YOUR_KEY" in str(value):
            warnings.append(f"{key} is still a placeholder — LLM features will be disabled.")

    # The audit chain still works unkeyed, but unkeyed digests can be recomputed by
    # anyone who can write to the file — which is precisely the person the chain is
    # meant to catch. Deploying without this is a real weakening, not a nicety.
    if not str(env.get("AUDIT_HMAC_KEY") or "").strip():
        warnings.append(
            "AUDIT_HMAC_KEY is not set — the audit chain will be unkeyed, so someone "
            "with write access to the log could recompute it and hide a tampering. "
            "Set it to a long random value for any deployment holding real records.")

    # Without it the signing key is drawn fresh on every start, so each restart — and
    # AppSail restarts a container that touches its memory limit — signs every officer
    # out mid-session and their next request is refused.
    if not str(env.get("AUTH_SECRET") or "").strip():
        warnings.append(
            "AUTH_SECRET is not set — every container restart invalidates every "
            "sign-in. Set it to a long random value.")

    # A single writer is a hard requirement of the chain, not a tuning choice.
    if "--workers" in cfg.get("command", ""):
        after = cfg["command"].split("--workers", 1)[1].strip().split()[0]
        if after.isdigit() and int(after) > 1:
            problems.append(
                f"--workers {after}: the audit hash chain requires a single writer "
                "process. Two workers keep separate chain heads and their writes "
                "interleave, so /audit-log/verify would fail on every other line. "
                "Use --workers 1 --threads N, or move the chain behind a shared "
                "sequencer first.")

# ---------------------------------------------------------------- report
for w in warnings:
    print(f"  warn  {w}")
for p in problems:
    print(f"  FAIL  {p}")

if problems:
    print(f"\n{len(problems)} blocking problem(s). Deploying now would produce a container "
          "that fails to start.")
    sys.exit(1)

size_mb = sum(
    os.path.getsize(os.path.join(r, f))
    for r, _, fs in os.walk(VENDOR) for f in fs
) / 1e6
print(f"  ok    _vendor present ({size_mb:.0f} MB), httpx pinned, command puts it on PYTHONPATH")
print("\nPreflight passed — safe to run: catalyst deploy --only appsail")
