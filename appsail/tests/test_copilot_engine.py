"""The copilot's retrieval engine and tool-calling loop.

What is pinned here is what made the old copilot "dumb": it could not see a record.
Each test asks the kind of question an investigator asks and checks the answer was
read from the corpus — the right case, the right people, the right count — rather
than whether it sounds plausible. The LLM loop is exercised against a scripted
provider, so the test covers tool dispatch, alias masking and restoration without a
network call.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copilot_engine as E  # noqa: E402
import copilot_knowledge  # noqa: E402
import main  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def loaded():
    main.load_and_build_intelligence()


@pytest.fixture
def cache():
    return main.data_cache


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(main, "llm_available", lambda: False)


def _ask(q, history=None, context=None):
    with main.app.test_request_context("/"):
        return main.run_agent(q, history=history, context=context)


def _a_linked_case(cache):
    links = cache["links"]
    return str(links["case_id"].value_counts().index[0])


# ------------------------------------------------------------------ resolution
def test_every_case_reference_shape_resolves_to_the_same_case(cache):
    idx = E._index(cache)
    row = cache["firs"].iloc[5]
    cid, cm = str(row["case_id"]), int(row["CaseMasterID"])
    for ref in (cid, f"FIR-{cm}", str(cm), f"case {cm}"):
        assert E.resolve_case(ref, idx) == cid, ref


def test_an_invented_case_number_resolves_to_nothing(cache):
    assert E.resolve_case("999999999999999999", E._index(cache)) is None


def test_a_full_name_in_free_text_is_found(cache):
    row = next(r for r in cache["criminals"].to_dict("records")
               if len(E._index(cache).names[r["name"].lower()]) == 1)
    found = E.find_names(f"what do we know about {row['name']} lately", E._index(cache))
    assert any(row["criminal_id"] in [i for _, i in hits] for _, hits in found)


def test_a_pronoun_resolves_to_the_subject_of_the_last_answer_not_a_listed_associate(cache):
    idx = E._index(cache)
    history = [{"role": "assistant", "content": "**X (SUS-8286)** ... Co-offenders: Y (SUS-2575); Z (SUS-7049)"}]
    ents = E.extract_entities("does she have a warrant?", idx, history=history)
    assert ents["suspects"] == ["SUS-8286"]


def test_it_on_a_case_page_means_that_case(cache):
    cid = _a_linked_case(cache)
    ents = E.extract_entities("what is its court status?", E._index(cache), context={"case_id": cid})
    assert ents["cases"] == [cid]


def test_it_does_not_hijack_a_district_question(cache):
    cid = _a_linked_case(cache)
    ents = E.extract_entities("is it rising in Mysuru?", E._index(cache), context={"case_id": cid})
    assert ents["cases"] == []


# ------------------------------------------------------------------ tools
def test_case_dossier_carries_the_people_and_the_progress(cache):
    cid = _a_linked_case(cache)
    c = E.tool_get_case(cache, case_ref=cid)
    linked = {s for s, _ in E._index(cache).links_by_case[cid]}
    assert {a["suspect_id"] for a in c["accused"]} == linked
    for key in ("narrative", "victim", "investigating_officer", "arrests", "similar_cases"):
        assert key in c, key


def test_person_profile_lists_every_case_they_are_named_in(cache):
    p = E.tool_get_person(cache, person_ref="SUS-8286")
    expected = int((cache["links"]["suspect_id"] == "SUS-8286").sum())
    assert p["cases_in_corpus"] == expected
    assert p["co_offenders"], "a 44-case offender with no co-offenders means the join broke"


def test_a_shared_name_is_reported_as_ambiguous_not_guessed(cache):
    idx = E._index(cache)
    name = next(n for n, hits in idx.names.items() if sum(k == "offender" for k, _ in hits) > 2)
    p = E.tool_get_person(cache, person_ref=name)
    assert p.get("ambiguous") and len(p["candidates"]) > 1


def test_filtered_search_counts_match_pandas(cache):
    f = cache["firs"]
    ts = __import__("pandas").to_datetime(f["timestamp"], errors="coerce")
    expected = int(((f["district"] == "Mysuru") & (f["crime_type"] == "Murder") & (ts.dt.year == 2024)).sum())
    r = E.tool_search_cases(cache, district="Mysuru", crime_type="murder", year=2024)
    assert r["total_matching"] == expected


def test_aggregate_ranks_by_the_requested_metric(cache):
    r = E.tool_aggregate(cache, group_by="district", metric="fatalities", top_n=3)
    expected = cache["firs"].groupby("district")["fatalities"].sum().sort_values(ascending=False)
    assert list(r["groups"])[0] == expected.index[0]


def test_network_finds_a_chain_or_says_there_is_none(cache):
    r = E.tool_network(cache, entity="SUS-1001", target="SUS-8286")
    assert "connected" in r
    if r["connected"]:
        assert r["path"][0]["id"] == "SUS-1001" and r["path"][-1]["id"] == "SUS-8286"


def test_people_search_honours_registry_flags(cache):
    r = E.tool_search_people(cache, rowdy_sheeter=True, active_warrant=True)
    crim = cache["criminals"]
    expected = int((crim["rowdy_sheeter"].map(E._truthy) & crim["active_warrant"].map(E._truthy)).sum())
    assert r["total_matching"] == expected


def test_worst_officers_come_lowest_first_above_a_caseload_floor(cache):
    r = E.tool_search_officers(cache, order="asc")
    rates = [o["closure_rate"] for o in r["officers"]]
    assert rates == sorted(rates)
    assert all(o["cases_assigned"] >= E.MIN_CASES_FOR_RANKING for o in r["officers"])
    assert r["caution"] and r["state_avg_closure_rate"]


def test_optional_tool_arguments_accept_null(cache):
    # Groq validates tool calls server-side and drops the whole turn when a model says
    # "no filter" as null on a plain string field.
    spec = next(t["function"] for t in E.tool_specs() if t["function"]["name"] == "search_officers")
    assert "null" in spec["parameters"]["properties"]["district"]["type"]
    assert E.run_tool("search_officers", {"district": None, "order": "asc"}, cache)["total_matching"] > 0


def test_every_tool_survives_empty_arguments(cache):
    for name, (fn, _d, _p, required) in E.TOOLS.items():
        if fn is None:
            continue
        args = {k: "x" for k in required}
        out = E.run_tool(name, args, cache)
        assert isinstance(out, dict), name


def test_knowledge_answers_a_definition_with_the_definition():
    secs = copilot_knowledge.lookup("what is a rowdy sheeter")
    assert "rowdy register" in secs[0].get("excerpt", secs[0]["text"]).lower()


# ------------------------------------------------------------------ agent, offline
@pytest.mark.parametrize("q,expect", [
    ("who are the accused in FIR-20345?", "accused"),
    ("how many murders in Mysuru in 2024?", "Mysuru"),
    ("which police station has the most chain snatching?", "PS"),
    ("which crimes are spiking?", "trend"),
    ("what is a rowdy sheeter?", "register"),
    ("how accurate is the recidivism model?", "roc_auc"),
    ("which officer is doing the worst? who should I suspend?", "lowest closure rate first"),
])
def test_offline_answers_are_specific(offline, q, expect):
    r = _ask(q)
    assert expect.lower() in r["reply"].lower(), r["reply"]
    assert r["grounding"]["grounded"], r["reply"]


def test_a_follow_up_question_uses_the_conversation(offline):
    first = _ask("who is SUS-8286")
    history = [{"role": "user", "content": "who is SUS-8286"},
               {"role": "assistant", "content": first["reply"]}]
    second = _ask("does she have an active warrant?", history=history)
    assert "SUS-8286" in second["reply"]


def test_an_open_command_says_what_is_on_the_screen(offline):
    r = _ask("open FIR-20345")
    assert r["action"] == "open_case"
    assert "accused" in r["reply"].lower() or "no accused" in r["reply"].lower()


def test_an_out_of_scope_question_does_not_navigate(offline):
    r = _ask("what is the weather today")
    assert r["action"] == "answer"


def test_every_reply_has_a_short_spoken_form(offline):
    r = _ask("tell me about SUS-1001")
    assert r["speech"] and len(r["speech"]) <= 260 and "**" not in r["speech"]


# ------------------------------------------------------------------ agent, LLM loop
def _msg(content=None, calls=None):
    return SimpleNamespace(content=content, tool_calls=calls)


def _call(i, name, args):
    return SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def test_llm_loop_calls_tools_masks_names_and_restores_them(cache, monkeypatch):
    sent = []
    real_name = cache["criminals"].set_index("criminal_id").loc["SUS-1001", "name"]

    def fake_chat(providers, messages, tools, deadline=None):
        sent.append(json.dumps(messages, default=str))
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            return _msg(calls=[_call(1, "get_case", {"case_ref": "FIR-20345"}),
                               _call(2, "navigate", {"action": "open_suspect", "suspect_id": "SUS-1001"})]), "fake"
        alias = next(a for a in __import__("re").findall(r"\[PERSON_\d+\]", sent[0]))
        return _msg(content=f"{alias} (SUS-1001) is profiled.\nFOLLOWUPS: who else? | show network"), "fake"

    monkeypatch.setattr(E, "_chat", fake_chat)
    with main.app.test_request_context("/"):
        r = E.llm_answer("tell me about SUS-1001", cache, [{"name": "fake"}])
    assert real_name not in "".join(sent), "a registry name reached the provider"
    assert "[PERSON_" not in r["reply"] and real_name in r["reply"]
    assert r["followups"] == ["who else?", "show network"]
    assert r["nav"] == {"action": "open_suspect", "params": {"suspect_id": "SUS-1001"}}
    assert any(t["tool"] == "get_case" for t in r["trace"])


def test_a_failing_provider_falls_back_to_the_deterministic_engine(monkeypatch):
    monkeypatch.setattr(main, "llm_available", lambda: True)
    monkeypatch.setattr(E, "_chat", lambda *a, **k: (None, None))
    r = _ask("how many murders in Mysuru in 2024?")
    assert r["mode"] == "deterministic" and "Mysuru" in r["reply"]


def test_a_rate_limited_model_hands_over_to_the_next_without_waiting(monkeypatch):
    """Groq meters tokens per model: on a 429 the next model is asked at once, and the
    limited one is left alone until its wait is over."""
    import openai
    asked = []

    class Limited(Exception):
        status_code = 429

    class FakeClient:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, model, **kw):
            asked.append(model)
            if model == "first":
                raise Limited("Rate limit reached ... Please try again in 7.5s.")
            return SimpleNamespace(choices=[SimpleNamespace(message=_msg(content="ok"))])

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setenv("FAKE_KEY", "k")
    monkeypatch.setenv("FAKE_MODEL", "first")
    monkeypatch.setenv("FAKE_FALLBACK", "second")
    monkeypatch.setattr(E, "_cooldown", {})
    p = [{"name": "fake", "base_url": "x", "key_env": "FAKE_KEY", "model_env": "FAKE_MODEL",
          "model_default": "first", "fallback_env": "FAKE_FALLBACK"}]
    msg, _ = E._chat(p, [{"role": "user", "content": "hi"}], None)
    assert msg.content == "ok" and asked == ["first", "second"]
    E._chat(p, [{"role": "user", "content": "hi"}], None)
    assert asked == ["first", "second", "second"], "the limited model was asked again inside its wait"


def test_a_path_between_two_people_never_runs_through_an_officer(cache):
    r = E.tool_network(cache, entity="SUS-7403", target="SUS-8286")
    assert r.get("connected") and all(n["type"] != "Officer" for n in r["path"])


def test_a_case_says_which_code_was_in_force_on_the_offence_date(cache):
    r = E.tool_get_case(cache, case_ref="100050027202600005")
    assert r["code_in_force"] == "BNS"
    assert any("IPC 457" in f and "BNS" in f for f in r["statute_findings"])


def test_two_districts_in_one_question_are_both_answered(cache):
    idx = E._index(cache)
    q = "How many murders in Mysuru in 2024, and what about Belagavi?"
    calls = E.plan(q, idx, E.extract_entities(q, idx))
    assert [a.get("district") for n, a in calls if n == "search_cases"] == ["Mysuru", "Belagavi"]


def test_a_warrant_question_is_answered_about_warrants_without_the_ai(cache):
    r = E.offline_answer("Who are the accused in FIR-20345 and do any have warrants?", cache)
    assert "active warrant**" in r["reply"].split("\n")[0]
