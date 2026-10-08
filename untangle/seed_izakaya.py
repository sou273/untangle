"""デモ用の架空データ: 居酒屋 とり松の「予約の受け方」。

記録も規程も無いところから、店主の一言メモとアルバイトとのやり取りで溜めていく型。すべて架空。
仕込んであること:
  - 金土・19時以降・6〜10名: 断った2件と受けた2件（揺れ）。違いは「2回転目の予約」の有無で、
    記録には残っているが、型の見分け方にはまだ入っていない（店主が答えると見つかる）
  - 子連れは座敷、が3件（答えられない場面が溜まった → 条文の案）
  - 平日・19時前・5名以下は受ける、が6件（同上）
  - 理由つきの単発（持ち込みのケーキ・お酒、平日夜、カウンター）は聞き返さない
"""
from __future__ import annotations

from datetime import date, timedelta

from .engine import Store
from .models import CompletionCriteria, Decider, Scene, Source, Theme, ThemeQuestion

SHOP = "居酒屋 とり松"
THEME_ID = "yoyaku"
Q_ACCEPT, Q_SEAT, Q_BRING = "accept", "seat", "bring"

THEME = Theme(
    id=THEME_ID,
    name="予約の受け方",
    shop=SHOP,
    questions=[
        ThemeQuestion(id=Q_ACCEPT, title="受けるか断るか", key_attributes=["曜日", "時間帯", "人数"],
                      answers=["受ける", "断る", "店主に回す"]),
        ThemeQuestion(id=Q_SEAT, title="どの席にするか", key_attributes=["子連れ", "人数"],
                      answers=["カウンター", "テーブル", "座敷"]),
        ThemeQuestion(id=Q_BRING, title="持ち込みを受けるか", key_attributes=["持ち込むもの"],
                      answers=["受ける（持ち込み料あり）", "断る"]),
    ],
    vocabulary={
        "曜日": "平日・金土",
        "時間帯": "19時前・19時以降",
        "人数": "5名以下・6〜10名・11名以上",
        "子連れ": "あり・なし",
        "持ち込むもの": "ケーキ・お酒・その他",
        "2回転目": "同じ席に後の予約が入っているか（あり・なし）",
    },
    criteria=CompletionCriteria(coverage_min=1.0, recent_window=10, max_mismatches=0,
                                novelty_window=5, max_new_types=0),
)


def _rows() -> list[dict]:
    def accept(day, time, size, decision, reason=None, extra=None, by=Decider.OWNER, text=""):
        attrs = {"曜日": day, "時間帯": time, "人数": size, **(extra or {})}
        return dict(q=Q_ACCEPT, attrs=attrs, decision=decision, reason=reason, decider=by, text=text)

    rows = [
        accept("金土", "19時以降", "6〜10名", "断る", extra={"2回転目": "あり"},
               text="店主メモ：［土日・秋］20時に8名の電話、断った。"),
        accept("平日", "19時前", "5名以下", "受ける", text="［平日・秋］18時に3名、受けた。"),
        dict(q=Q_SEAT, attrs={"子連れ": "あり", "人数": "5名以下"}, decision="座敷",
             reason="子供が走り回るとカウンターの人に悪いから", decider=Decider.OWNER,
             text="［ホール担当A］：子連れ4名、座敷空いてますか？／店主：座敷で。"),
        accept("金土", "19時以降", "6〜10名", "受ける", extra={"2回転目": "なし"},
               text="店主メモ：［土日・秋］19時半に7名、受けた。"),
        accept("平日", "19時前", "5名以下", "受ける", text="［平日・秋］17時半に2名、受けた。"),
        dict(q=Q_BRING, attrs={"持ち込むもの": "ケーキ"}, decision="受ける（持ち込み料あり）",
             reason="誕生日は断りにくい。持ち込み料は1台500円", decider=Decider.OWNER,
             text="［ホール担当B］：誕生日ケーキの持ち込みOKですか？／店主：いいよ、500円もらって。"),
        accept("平日", "19時以降", "5名以下", "受ける", reason="平日の夜は空いているから",
               text="店主メモ：［平日・秋］20時に4名、受けた。平日の夜は空いてるし。"),
        accept("平日", "19時前", "5名以下", "受ける", text="［平日・秋］18時に4名、受けた。"),
        dict(q=Q_SEAT, attrs={"子連れ": "なし", "人数": "5名以下"}, decision="カウンター",
             reason="少人数はカウンターから埋める", decider=Decider.OWNER,
             text="店主メモ：2名はカウンターに。少人数はカウンターから埋める。"),
        accept("金土", "19時以降", "6〜10名", "断る", extra={"2回転目": "あり"},
               text="店主メモ：［土日・秋］20時に9名、断った。"),
        dict(q=Q_SEAT, attrs={"子連れ": "あり", "人数": "5名以下"}, decision="座敷", decider=Decider.STAFF,
             text="［ホール担当A］：子連れ3名を座敷に案内しました。"),
        accept("平日", "19時前", "5名以下", "受ける", text="［平日・秋］18時半に5名、受けた。"),
        accept("金土", "19時前", "5名以下", "受ける", reason="早い時間は回転するから",
               text="店主メモ：［土日・秋］17時に3名、受けた。早い時間は回る。"),
        dict(q=Q_BRING, attrs={"持ち込むもの": "お酒"}, decision="断る",
             reason="うちはお酒で利益を出しているから", decider=Decider.OWNER,
             text="［ホール担当B］：日本酒の持ち込み希望の方が…／店主：お酒はお断りして。"),
        accept("平日", "19時前", "5名以下", "受ける", text="［平日・秋］17時に2名、受けた。"),
        accept("金土", "19時以降", "6〜10名", "受ける", extra={"2回転目": "なし"},
               text="店主メモ：［土日・秋］19時に8名、受けた。"),
        dict(q=Q_SEAT, attrs={"子連れ": "あり", "人数": "5名以下"}, decision="座敷", decider=Decider.STAFF,
             text="［ホール担当B］：子連れ2名、座敷にしました。"),
        accept("平日", "19時前", "5名以下", "受ける", text="［平日・秋］18時に2名、受けた。"),
        dict(q=Q_SEAT, attrs={"子連れ": "なし", "人数": "5名以下"}, decision="カウンター",
             reason="少人数はカウンターから埋める", decider=Decider.STAFF,
             text="［ホール担当A］：1名様、カウンターにご案内しました。"),
        dict(q=Q_SEAT, attrs={"子連れ": "なし", "人数": "5名以下"}, decision="カウンター",
             reason="少人数はカウンターから埋める", decider=Decider.STAFF,
             text="［ホール担当B］：2名様、カウンターにしました。"),
    ]
    return rows


def build(store: Store | None = None) -> Store:
    store = store or Store()
    store.themes[THEME_ID] = THEME.model_copy(deep=True)
    start = date(2026, 9, 1)
    for i, r in enumerate(_rows()):
        sid = f"t{i + 1:03d}"
        store.scenes[sid] = Scene(
            id=sid, theme_id=THEME_ID, question_id=r["q"], occurred_on=start + timedelta(days=i),
            situation=r["text"], attributes=r["attrs"], decision=r["decision"], reason=r.get("reason"),
            source=Source.CONVERSATION, decider=r["decider"],
            decider_role="店主" if r["decider"] == Decider.OWNER else "アルバイト",
        )
    return store


SAMPLES = [
    ("土曜8名を断った（理由つき）", "店主メモ：土曜20時に8名の電話、断った。2回転目の予約が入ってるから。"),
    ("子連れの席（理由つき）", "ホール木村：来週金曜、子連れ4名で座敷空いてますか？\n店主：座敷で。子供が走り回るとカウンターの人に悪いから。"),
    ("11名の宴会（初めての型）", "ホール木村：金曜19時半に12名の宴会の問い合わせです。\n店主：うーん、受けといて。"),
]
