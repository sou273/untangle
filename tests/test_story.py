"""動画の筋書きどおりに進むか: 質問に答えていくと「団体予約の受け方」が完了する。"""
from untangle import seed
from untangle.engine import CHOICE_ACCEPT_RULE
from untangle.models import ArticleStatus, QuestionKind


def test_sokichi_story_reaches_completion():
    st = seed.build()
    assert len(st.scenes) == 160

    status = st.status(seed.THEME_ID)
    assert not status.complete
    assert status.checks[2].ok, status.checks[2]          # 直近20件に新しい型はない
    assert not status.checks[0].ok and status.checks[0].answer == "4件残っています"

    qs = st.refresh_questions(seed.THEME_ID)
    kinds = sorted(q.kind.value for q in qs)
    assert kinds == sorted([QuestionKind.INCONSISTENT, QuestionKind.NEW_TYPE, QuestionKind.UNCOVERED,
                            QuestionKind.AGAINST_RULE, QuestionKind.FACT]), kinds
    assert not st.status(seed.THEME_ID).checks[3].ok      # 質問が残っている

    by = {q.kind: q for q in qs}
    st.answer(by[QuestionKind.INCONSISTENT].id, "子供が正しい", reason="小中学生は子供料金の対象だから")
    st.answer(by[QuestionKind.NEW_TYPE].id, CHOICE_ACCEPT_RULE, reason="未就学児も子供料金だから")
    st.answer(by[QuestionKind.UNCOVERED].id, CHOICE_ACCEPT_RULE, reason="支援学校も子供料金")
    st.answer(by[QuestionKind.AGAINST_RULE].id, CHOICE_ACCEPT_RULE, reason="先生も体験するなら大人")
    st.answer(by[QuestionKind.FACT].id, "事実を直す", fact={"団体種別": "高校"})

    # 答えたことで新しい質問が出ないこと（同じことを二度聞かない）
    assert st.refresh_questions(seed.THEME_ID) == []

    status = st.status(seed.THEME_ID)
    assert status.complete, [(c.label, c.answer, c.detail) for c in status.checks]
    assert st.release(seed.THEME_ID) == 1
    art2 = st.articles["art2"]
    assert art2.status == ArticleStatus.PASSED and art2.reason


def test_new_articles_get_distinct_numbers():
    st = seed.build()
    for q in st.refresh_questions(seed.THEME_ID):
        if q.kind in (QuestionKind.NEW_TYPE, QuestionKind.UNCOVERED, QuestionKind.AGAINST_RULE):
            st.answer(q.id, CHOICE_ACCEPT_RULE, reason="r")
    nums = [a.number for a in st.theme_articles(seed.THEME_ID)]
    assert len(nums) == len(set(nums)), nums
