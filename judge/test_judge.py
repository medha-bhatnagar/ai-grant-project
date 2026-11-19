"""Checks that need no LLM and no database.   Run:  pytest   (in the judge folder)"""
import json

import pytest
from fastapi.testclient import TestClient

import judge
import server


def test_read_queries_are_accepted_and_capped():
    assert judge.check_query({"op": "collections"}) == {"op": "collections"}
    assert judge.check_query({"op": "find", "collection": "budgets", "limit": 500})["limit"] == judge.MAX_DOCS


@pytest.mark.parametrize("args", [
    {"op": "drop", "collection": "x"},
    {"op": "find", "collection": "x; db.dropDatabase()"},
    {"op": "aggregate", "collection": "x", "pipeline": [{"$out": "copy"}]},
    {"op": "find", "collection": "x", "filter": {"$where": "sleep(1000)"}},
])
def test_writes_and_code_are_refused(args):
    with pytest.raises(judge.QueryError):
        judge.check_query(args)


def test_agents_json_is_complete():
    agents = judge.load_agents()
    assert agents["judge"]["model"] and agents["judge"]["instructions"]
    for name, spec in agents["specialists"].items():
        assert spec["model"] and spec["description"] and spec["instructions"], name


def test_judge_routes_and_the_specialist_answer_goes_straight_back(monkeypatch):
    db_model = judge.load_agents()["specialists"]["database"]["model"]
    judge_model = judge.load_agents()["judge"]["model"]
    replies = iter([
        {"action": "tool", "tool": "ask_database", "args": {"question": "How many requests?"}, "answer": ""},
        {"action": "tool", "tool": "count", "args": {"collection": "requests"}, "answer": ""},
        {"action": "answer", "tool": "", "args": {}, "answer": "There are 3 requests."},
    ])
    models = []
    monkeypatch.setattr(judge, "chat_json", lambda model, *a: models.append(model) or next(replies))
    monkeypatch.setattr(judge, "run_query", lambda args, **k: "3")
    steps = []
    assert judge.Judge(on_event=steps.append).reply("How many requests?") == "There are 3 requests."
    assert models == [judge_model, db_model, db_model]   # the Judge doesn't reword the answer
    assert steps == ['judge uses ask_database {"question": "How many requests?"}',
                     'database specialist uses count {"collection": "requests"}']


def test_request_questions_use_computed_facts(monkeypatch):
    records = ('{"requests": [{"request_id": "X-1", "items": [{"name": "Budget", "done": false}]}],'
               ' "budgets": [{"year": 1, "personnel": 150000, "equipment": 60000}],'
               ' "opportunity": {"opportunity_id": "O", "budget_rules": "no more than $200,000 in any one year"}}')
    facts = judge.request_facts(records)
    assert "Items still missing: Budget" in facts
    assert "Year 1: $210,000 is OVER the $200,000 yearly cap" in facts
    seen = {}
    monkeypatch.setattr(judge, "request_records", lambda *texts: ("X-1", records))
    monkeypatch.setattr(judge, "chat_text", lambda model, system, prompt: seen.update(prompt=prompt) or "Over by $10,000.")
    answer = judge.ask_specialist("budget", "Is X-1 within the limit?", on_event=lambda t: None)
    assert answer == "Over by $10,000." and isinstance(answer, judge.Final)
    assert "OVER the $200,000 yearly cap" in seen["prompt"]


def test_specialist_asks_which_request_when_none_is_named(monkeypatch):
    monkeypatch.setattr(judge, "request_records", lambda *texts: None)
    monkeypatch.setattr(judge, "request_ids", lambda: ["SAMPLE-001", "SAMPLE-002"])
    answer = judge.ask_specialist("requirements", "check the requirements", on_event=lambda t: None)
    assert "ask the user which request" in answer and not isinstance(answer, judge.Final)


def test_server_passes_what_the_user_is_viewing(monkeypatch):
    seen = []
    monkeypatch.setattr(judge, "chat_json", lambda model, system, messages: seen.append(messages[-1]["content"])
                        or {"action": "answer", "tool": "", "args": {}, "answer": "Hi!"})
    r = TestClient(server.app).post("/chat", json={"session_id": "t", "message": "hello", "context": "SAMPLE-001"})
    assert r.json()["reply"] == "Hi!"
    assert seen == ["(The user is viewing: SAMPLE-001)\nhello"]


def test_text_matches_ignore_capitals_and_tiny_limits_are_raised():
    q = judge.check_query({"op": "find", "collection": "documents", "limit": 1,
                           "filter": {"request_id": "SAMPLE-001", "section": "budget justification", "order": 2}})
    assert q["limit"] == judge.MIN_DOCS
    assert q["filter"]["section"] == {"$regex": "^budget\ justification$", "$options": "i"}
    assert q["filter"]["order"] == 2


def test_formatting_is_read_from_word_files_and_checked():
    from pathlib import Path
    samples = Path(__file__).resolve().parent.parent / "samples"
    good = judge.read_docx_formatting(samples / "good_grant.docx")
    bad = judge.read_docx_formatting(samples / "bad_grant.docx")
    assert good["fonts"] == ["Arial"] and good["font_sizes"] == [11.0]
    assert bad["fonts"] == ["Times New Roman"] and bad["margins_in"]["left"] == 0.3
    rules = {"allowed_fonts": ["Arial"], "min_font_size": 11, "min_margin_in": 0.5}
    records = lambda fmt: json.dumps({"requests": [{"items": []}], "opportunity": {"formatting_rules": rules},
                                      "documents": [{"document": "g.docx", "formatting": fmt}]})
    assert "Formatting problems: none" in judge.request_facts(records(good))
    facts = judge.request_facts(records(bad))
    assert "Times New Roman is not allowed" in facts and "10 pt is below" in facts and "0.5 inch minimum" in facts


def test_people_file_lookup():
    found = json.loads(judge.find_person("Dr. Herrera"))
    assert [p["id"] for p in found] == ["P009"]
    assert "No one matching" in judge.find_person("Nobody Atall")


def test_add_team_member_checks_before_writing(monkeypatch):
    def no_writes(*a, **k):
        raise AssertionError("should not reach the database")
    monkeypatch.setattr(judge.subprocess, "run", no_writes)
    assert "role must be one of" in judge.add_team_member("SAMPLE-001", "P001", "Boss")
    assert "no person with id" in judge.add_team_member("SAMPLE-001", "P999", "PI")
    assert "can't be a PI" in judge.add_team_member("SAMPLE-001", "P007", "PI")   # a postdoc
    assert "request id must look like" in judge.add_team_member("bad id!", "P001", "PI")


def test_team_databases_are_never_written(monkeypatch):
    def no_writes(*a, **k):
        raise AssertionError("should not reach the database")
    monkeypatch.setattr(judge.subprocess, "run", no_writes)
    for name in ("grant_rag", "affiliation", "GRANT_RAG"):
        monkeypatch.setenv("MONGO_DB", name)
        assert "Refusing to write" in judge.add_team_member("SAMPLE-001", "P001", "PI")
        with pytest.raises(SystemExit, match="Refusing to write"):
            judge.setup_demo()
    monkeypatch.setenv("MONGO_DB", "grants")
    assert judge.write_db() == "grants"


def test_library_is_read_only_and_searches_plain_text(monkeypatch):
    q = judge.check_query({"op": "library_search", "collection": "past_submissions", "q": "aims (1)"})
    assert q["q"] == r"aims\ \(1\)"            # a pattern can't sneak in
    with pytest.raises(judge.QueryError):
        judge.check_query({"op": "library_read", "collection": "past_submissions", "q": ""})
    monkeypatch.setenv("LIBRARY_DB", "team44_shc22039_demo")
    monkeypatch.setenv("MONGO_DB", "team44_shc22039_demo")   # pointing our writes at the library
    with pytest.raises(PermissionError):
        judge.write_db()
    seen = {}
    monkeypatch.setattr(judge, "run_query", lambda args, max_chars=0, db=None: seen.update(db=db) or "[]")
    judge.list_past_grants()
    assert seen["db"] == "team44_shc22039_demo"


def test_past_grants_specialist_gets_the_library_tools():
    spec = judge.load_agents()["specialists"]["past_grants"]
    assert spec["library"] and not spec.get("database")
    assert "ask_past_grants" in judge.Judge(on_event=lambda t: None).agent.tools


def test_llms_are_added_and_removed_by_file(tmp_path, monkeypatch):
    import shutil
    shutil.copytree(judge.AGENTS_DIR, tmp_path / "agents")
    monkeypatch.setattr(judge, "AGENTS_DIR", tmp_path / "agents")
    llms = tmp_path / "agents" / "llms"
    assert "_TEMPLATE" not in judge.load_agents()["specialists"]        # the template is ignored
    shutil.copy(llms / "_TEMPLATE.json", llms / "biosketch.json")       # add one
    (llms / "budget.json").unlink()                                     # remove one
    tools = judge.Judge(on_event=lambda t: None).agent.tools
    assert "ask_biosketch" in tools and "ask_budget" not in tools
    (llms / "biosketch.json").write_text('{"model": "x",}', encoding="utf-8")   # a typo
    with pytest.raises(SystemExit, match="biosketch.json has a typo on line 1"):
        judge.load_agents()


def test_cv_status_finds_3_of_5():
    s = json.loads(judge.cv_status())
    assert s["team_size"] == 5
    assert {p["name"]: p["cv_file"] for p in s["submitted"]} == {
        "George Liu": "Liu_CV.pdf", "James Slaughter": "Slaughter_CV.pdf", "C.Creech": "Creech_CV.pdf"}
    assert [p["name"] for p in s["not_submitted"]] == ["Chih-Ming Tsai", "Issac Thomasen"]
    assert "ask_cv_checker" in judge.Judge(on_event=lambda t: None).agent.tools
    with pytest.raises(SystemExit, match="Unknown tool"):
        judge._named_tools({"tools": ["delete_everything"]})


def test_budget_spreadsheet_from_the_prompt_example(tmp_path):
    import copy
    import budget_sheet
    from openpyxl import load_workbook
    example = budget_sheet.load_prompt()["example"]["response"]
    s = budget_sheet.make_budget(data=example, out_dir=tmp_path)
    assert [y["total"] for y in s["per_year"]] == [166500, 166500] and s["warnings"] == []
    ws = load_workbook(tmp_path / s["file"])["Budget"]
    formulas = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=")]
    assert any("CEILING(" in f for f in formulas) and any("INDEX(" in f for f in formulas)   # real formulas
    wrong = copy.deepcopy(example)
    wrong["totals"]["total"] = [999, 999]                    # an AI arithmetic slip is caught
    assert budget_sheet.make_budget(data=wrong, out_dir=tmp_path)["warnings"]
    wrong["personnel"][0]["calendar_months"] = 14            # impossible effort is refused
    with pytest.raises(budget_sheet.BudgetError, match="calendar months"):
        budget_sheet.make_budget(data=wrong, out_dir=tmp_path)
    assert "ask_budget_builder" in judge.Judge(on_event=lambda t: None).agent.tools


def test_judge_has_the_team_tools():
    assert {"find_person", "list_team", "add_team_member"} <= set(judge.Judge(on_event=lambda t: None).agent.tools)
