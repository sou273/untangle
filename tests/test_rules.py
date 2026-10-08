from datetime import date, timedelta

from untangle.models import (
    ESCALATE, Article, ArticleStatus, CompletionCriteria, Condition, Decider, Op,
    Question, QuestionKind, Scene, SceneFlag, Source, Theme, ThemeQuestion,
)
from untangle.rules import Outcome, evaluate, run_question_test, theme_status

Q = "count"
THEME = Theme(
    id="dantai", name="団体予約の受け方",
    questions=[ThemeQuestion(id=Q, title="体験人数の数え方", key_attributes=["団体種別", "立場", "記載"])],
    criteria=CompletionCriteria(coverage_min=0.95, recent_window=30, novelty_window=5),
)


def art(n, conds, decision, priority=100, status=ArticleStatus.PASSED):
    return Article(id=f"a{n}", theme_id="dantai", question_id=Q, number=n,
                   conditions=[Condition(**c) for c in conds], decision=decision,
                   priority=priority, status=status)


ARTICLES = [
    art(1, [{"attribute": "立場", "op": Op.IN, "value": ["先生", "添乗員", "ガイド"]}], "数えない"),
    art(11, [{"attribute": "立場", "value": "先生"}, {"attribute": "記載", "value": "先生も体験"}], "大人"),
    art(2, [{"attribute": "団体種別", "op": Op.IN, "value": ["小学校", "中学校"]}, {"attribute": "立場", "value": "生徒"}], "子供"),
    art(4, [{"attribute": "人数", "op": Op.GT, "value": 100}], ESCALATE, priority=1),
]


def scene(i, attrs, decision, day=0, **kw):
    return Scene(id=f"s{i}", theme_id="dantai", question_id=Q, occurred_on=date(2026, 4, 1) + timedelta(days=day),
                 situation="", attributes=attrs, decision=decision, source=Source.CORRECTION,
                 decider=Decider.OWNER, decider_role="決裁者", **kw)


def test_more_specific_article_wins_as_exception():
    ev = evaluate(ARTICLES, Q, {"立場": "先生", "記載": "先生も体験"})
    assert ev.decision == "大人" and ev.article.number == 11
    assert evaluate(ARTICLES, Q, {"立場": "先生"}).decision == "数えない"


def test_priority_escalation_beats_everything():
    ev = evaluate(ARTICLES, Q, {"団体種別": "小学校", "立場": "生徒", "人数": 120})
    assert ev.decision == ESCALATE


def test_conflict_when_same_rank_disagrees():
    arts = [art(1, [{"attribute": "x", "value": 1}], "A"), art(2, [{"attribute": "y", "value": 1}], "B")]
    ev = evaluate(arts, Q, {"x": 1, "y": 1})
    assert ev.decision is None and len(ev.conflict) == 2


def test_not_a_rule_is_ignored():
    arts = [art(1, [{"attribute": "x", "value": 1}], "A", status=ArticleStatus.NOT_A_RULE)]
    assert not evaluate(arts, Q, {"x": 1}).covered


def test_outcomes_are_separated():
    scenes = [
        scene(1, {"団体種別": "小学校", "立場": "生徒"}, "子供", 0),
        scene(7, {"団体種別": "小学校", "立場": "生徒"}, "子供", 0),
        scene(2, {"団体種別": "小学校", "立場": "生徒"}, "大人", 1),          # 揺れ（1・7と同じ条件で2件ずつ割れる）
        scene(8, {"団体種別": "小学校", "立場": "生徒"}, "大人", 1),
        scene(3, {"団体種別": "中学校", "立場": "生徒"}, "大人", 2,           # 読み取りの外れ
              verified_attributes={"団体種別": "高校", "立場": "生徒"}),
        scene(4, {"団体種別": "保育園", "立場": "園児"}, "子供", 3),          # 条文なし
        scene(5, {"団体種別": "中学校", "立場": "生徒"}, "数えない", 4),       # 規則の外れ
        scene(6, {"団体種別": "小学校", "立場": "生徒"}, "大人", 5, flags=[SceneFlag.REGISTRATION_ERROR]),
    ]
    arts = ARTICLES + [art(20, [{"attribute": "団体種別", "value": "高校"}], "大人")]
    out = {r.scene.id: r.outcome for r in run_question_test(THEME, Q, arts, scenes)}
    assert out == {"s1": Outcome.MATCH, "s7": Outcome.MATCH, "s2": Outcome.WAVER, "s8": Outcome.WAVER, "s3": Outcome.EXTRACTION_MISS,
                   "s4": Outcome.UNCOVERED, "s5": Outcome.RULE_MISS}   # s6 は母数から外れる


def test_theme_completes_only_when_all_four_checks_pass():
    base = [scene(i, {"団体種別": "小学校", "立場": "生徒"}, "子供", i) for i in range(10)]
    teachers = [scene(100 + i, {"団体種別": "小学校", "立場": "先生"}, "数えない", i) for i in range(10)]
    scenes = base + teachers
    open_q = Question(id="q1", theme_id="dantai", kind=QuestionKind.NEW_TYPE, to=Decider.OWNER,
                      text="?", scene_ids=[], choices=["a"])
    st = theme_status(THEME, ARTICLES, scenes, [open_q])
    assert [c.ok for c in st.checks] == [True, True, True, False]
    open_q.answer = "a"
    assert theme_status(THEME, ARTICLES, scenes, [open_q]).complete


def test_new_type_in_recent_window_blocks_completion():
    scenes = [scene(i, {"団体種別": "小学校", "立場": "生徒"}, "子供", i) for i in range(10)]
    scenes.append(scene(99, {"団体種別": "中学校", "立場": "生徒"}, "子供", 50))
    st = theme_status(THEME, ARTICLES, scenes, [])
    assert not st.checks[2].ok and st.checks[2].answer == "最近1件出ました"


def test_single_outlier_is_not_a_waver():
    scenes = [scene(i, {"団体種別": "小学校", "立場": "生徒"}, "子供", i) for i in range(5)]
    scenes.append(scene(9, {"団体種別": "小学校", "立場": "生徒"}, "大人", 9))
    out = {r.scene.id: r.outcome for r in run_question_test(THEME, Q, ARTICLES, scenes)}
    assert out["s9"] == Outcome.RULE_MISS
