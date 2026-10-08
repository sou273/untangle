"""作業場の保存形式: 書き出して読み戻すと同じ状態になる（Firestore は使わない）。"""
import json

from untangle import persist, seed, seed_izakaya
from untangle.engine import CHOICE_HIDDEN


def test_dump_and_load_round_trip_keeps_state_and_asked_keys():
    st = seed.build(); seed_izakaya.build(st)
    for tid in (seed.THEME_ID, seed_izakaya.THEME_ID):
        st.refresh_questions(tid)
    waver = next(q for q in st.questions.values() if q.theme_id == "yoyaku" and q.kind.value == "inconsistent")
    st.answer(waver.id, CHOICE_HIDDEN, reason="2回転目の予約")
    st.dictionary.append({"surface": "佐藤", "replacement": "ショップ担当A", "kind": "スタッフ"})

    back = persist.load(json.loads(json.dumps(persist.dump(st), ensure_ascii=False)))
    assert back.seq == st.seq and back.dictionary == st.dictionary
    assert back.asked == st.asked                       # 同じことを二度聞かない
    assert back.refresh_questions("dantai") == [] and back.refresh_questions("yoyaku") == []
    assert [c.answer for c in back.status("yoyaku").checks] == [c.answer for c in st.status("yoyaku").checks]
    assert back.next_id("x") == st.next_id("x")
