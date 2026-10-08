"""エージェントに持たせる道具の範囲（統治）を固定するテスト。LLMは呼ばない。"""
from untangle import seed
from untangle.agent import AgentRun


def _run():
    st = seed.build()
    return st, AgentRun(st, seed.THEME_ID, "test")


def test_agent_has_no_tool_to_confirm_rules_or_answer():
    _, run = _run()
    names = {f.__name__ for f in run.tools()}
    assert names == {"get_theme_status", "review_new_scenes", "find_what_to_ask", "rewrite_question", "test_rules"}
    assert not any(k in n for n in names for k in ("answer", "confirm", "release", "delete", "set_article"))


def test_rewrite_keeps_choices_and_refuses_answered_questions():
    st, run = _run()
    tools = {f.__name__: f for f in run.tools()}
    created = tools["find_what_to_ask"]()["created"]
    q = st.questions[created[0]["question_id"]]
    choices = list(q.choices)
    assert tools["rewrite_question"](q.id, "小学校の生徒をどちらで数えるか、決めてください。")["ok"]
    assert q.choices == choices and q.original_text and q.text.startswith("小学校")
    st.answer(q.id, q.choices[0])
    assert not tools["rewrite_question"](q.id, "書き換え")["ok"]
    assert [s["tool"] for s in run.steps][-1] == "rewrite_question"
