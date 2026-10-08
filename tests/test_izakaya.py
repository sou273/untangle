"""居酒屋: 店主が「条件が違った」と答えると、隠れていた条件（2回転目）が見つかり条文が分かれる。"""
from untangle import seed_izakaya as iz
from untangle.engine import CHOICE_ACCEPT_RULE, CHOICE_HIDDEN
from untangle.models import ArticleStatus, QuestionKind
from untangle.rules import Outcome


def test_hidden_condition_splits_the_waver_into_two_articles():
    st = iz.build()
    qs = st.refresh_questions(iz.THEME_ID)
    kinds = sorted(q.kind.value for q in qs)
    assert kinds == ["inconsistent", "uncovered", "uncovered", "uncovered"], kinds
    waver = next(q for q in qs if q.kind == QuestionKind.INCONSISTENT)
    assert CHOICE_HIDDEN in waver.choices

    # 一言が無いと受け付けない
    try:
        st.answer(waver.id, CHOICE_HIDDEN)
        raise AssertionError("一言なしで通ってしまった")
    except ValueError:
        pass

    st.answer(waver.id, CHOICE_HIDDEN, reason="2回転目の予約が入っているかどうか")
    accept_q = next(t for t in st.themes[iz.THEME_ID].questions if t.id == iz.Q_ACCEPT)
    assert "2回転目" in accept_q.key_attributes
    split = [a for a in st.theme_articles(iz.THEME_ID) if any(c.attribute == "2回転目" for c in a.conditions)]
    assert sorted(a.decision for a in split) == ["受ける", "断る"]
    assert all(a.status == ArticleStatus.PASSED for a in split)   # 過去の4件で外れ0

    for q in qs:
        if q.kind == QuestionKind.UNCOVERED:
            st.answer(q.id, CHOICE_ACCEPT_RULE, reason="r")
    res = st.status(iz.THEME_ID).results
    assert not any(r.outcome in (Outcome.WAVER, Outcome.RULE_MISS) for r in res)
    # 理由つきの単発（ケーキ・お酒・平日夜・金土の早い時間）はまだ規程に無い＝集めている途中
    assert not st.status(iz.THEME_ID).complete


def test_adding_a_key_attribute_does_not_re_ask_answered_types():
    st = iz.build()
    qs = st.refresh_questions(iz.THEME_ID)
    waver = next(q for q in qs if q.kind == QuestionKind.INCONSISTENT)
    st.answer(waver.id, CHOICE_HIDDEN, reason="2回転目の予約")
    assert st.refresh_questions(iz.THEME_ID) == []
