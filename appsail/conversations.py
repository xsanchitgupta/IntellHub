"""Saved copilot conversations, per officer.

The floating assistant keeps eight turns in the browser and forgets them on reload. An
investigation is not eight turns long, and an officer coming back to it tomorrow needs
the thread they left. Conversations are stored server-side under the signed-in user,
so they follow the officer across devices, and nobody else can list or read them.

Storage is one JSON file written atomically. That is enough for a single AppSail
container; a multi-instance deployment would put this in the platform database.
Transcripts name people, so the file is gitignored and covered by the retention
schedule like the other runtime records.
"""
import json
import os
import threading
import time
import uuid

PATH = os.environ.get("COPILOT_CONVERSATIONS_PATH",
                      os.path.join(os.path.dirname(os.path.abspath(__file__)), "copilot_conversations.json"))
MAX_CONVERSATIONS = 200
MAX_MESSAGES = 400

_lock = threading.Lock()
_data = None


def _load():
    global _data
    if _data is None:
        try:
            with open(PATH, encoding="utf-8") as fh:
                _data = json.load(fh)
        except (OSError, ValueError):
            _data = {}
    return _data


def _save():
    tmp = f"{PATH}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_data, fh, ensure_ascii=False)
        os.replace(tmp, PATH)
    except OSError:
        pass


def _summary(c):
    last = next((m for m in reversed(c["messages"]) if m["role"] == "assistant"), None)
    return {"id": c["id"], "title": c["title"], "created": c["created"], "updated": c["updated"],
            "pinned": c.get("pinned", False), "messages": len(c["messages"]), "origin": c.get("origin"),
            "preview": (last["content"][:140] if last else "")}


def list_for(actor, q=None):
    with _lock:
        convs = list((_load().get(actor) or {}).values())
    if q:
        ql = q.lower()
        convs = [c for c in convs if ql in c["title"].lower()
                 or any(ql in m["content"].lower() for m in c["messages"])]
    convs.sort(key=lambda c: (not c.get("pinned", False), -c["updated"]))
    return [_summary(c) for c in convs]


def create(actor, title=None, origin=None):
    """`origin`: the page a conversation began on when the floating assistant opened it
    rather than the Copilot page, so the list can say where it came from."""
    now = time.time()
    origin = str(origin)[:80] if origin and not str(origin).startswith("/copilot") else None
    c = {"id": uuid.uuid4().hex[:12], "title": (title or "New investigation").strip()[:80],
         "created": now, "updated": now, "messages": [], "auto_title": not title, "origin": origin}
    with _lock:
        mine = _load().setdefault(actor, {})
        mine[c["id"]] = c
        if len(mine) > MAX_CONVERSATIONS:
            for old in sorted(mine.values(), key=lambda x: x["updated"])[: len(mine) - MAX_CONVERSATIONS]:
                if not old.get("pinned"):
                    mine.pop(old["id"], None)
        _save()
    return c


def get(actor, cid):
    with _lock:
        return (_load().get(actor) or {}).get(cid)


def update(actor, cid, title=None, pinned=None):
    with _lock:
        c = (_load().get(actor) or {}).get(cid)
        if not c:
            return None
        if title is not None and title.strip():
            c["title"] = title.strip()[:80]
            c["auto_title"] = False
        if pinned is not None:
            c["pinned"] = bool(pinned)
        _save()
        return c


def delete(actor, cid):
    with _lock:
        ok = (_load().get(actor) or {}).pop(cid, None) is not None
        if ok:
            _save()
        return ok


def _title_from(question):
    q = " ".join(question.strip().split())
    q = q[0].upper() + q[1:] if q else q
    return (q[:57] + "…") if len(q) > 58 else q.rstrip("?") or "New investigation"


def append(actor, cid, user_text, assistant):
    """Record one exchange. `assistant` is the agent's response dict."""
    now = time.time()
    with _lock:
        c = (_load().get(actor) or {}).get(cid)
        if not c:
            return None
        c["messages"].append({"role": "user", "content": user_text, "ts": now})
        c["messages"].append({
            "role": "assistant", "content": assistant.get("reply", ""), "ts": time.time(),
            "trace": assistant.get("trace") or [], "mode": assistant.get("mode"),
            "suggestions": assistant.get("suggestions") or [],
            "action": assistant.get("action"), "params": assistant.get("params") or {},
            "grounded": (assistant.get("grounding") or {}).get("grounded", True),
            "interpreted_as": assistant.get("interpreted_as"),
        })
        c["messages"] = c["messages"][-MAX_MESSAGES:]
        if c.get("auto_title") and sum(m["role"] == "user" for m in c["messages"]) == 1:
            c["title"] = _title_from(user_text)
        c["updated"] = now
        _save()
        return c


def history(c, turns=10):
    """The last few turns in the shape run_agent expects."""
    return [{"role": m["role"], "content": m["content"]} for m in c["messages"][-turns * 2:]]
