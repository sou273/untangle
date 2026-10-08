"""デモ用の架空データ: 切子体験工房（創吉）の「団体予約の受け方」。

すべて架空。実在の学校名・人名は使わない。状況文は伏せた後の形だけを持つ。
仕込んであること（動画の筋書きと同じ）:
  - 小学校の生徒: 45件は子供、30件は大人で登録されている（揺れ）
  - 保育園の園児 1件（初めての型・理由なし）
  - 支援学校の生徒 3件（答えられない場面が溜まった）
  - 「先生も体験」と書いてあり先生を大人にした 4件（規程と違う判断）
  - 高校を中学校と読み違えた 1件（スタッフへの事実確認）
  - 100名を超える修学旅行 3件（決裁者に回す）
直近20件は既に知っている型だけにしてある（「もう新しい話は出てこないか」を満たす）。
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from .engine import Store
from .models import (
    ESCALATE, Article, ArticleStatus, CompletionCriteria, Condition, Decider, Op, Scene, Source, Theme,
    ThemeQuestion,
)

THEME_ID = "dantai"
Q_COUNT, Q_PAY, Q_ACCEPT = "count", "payment", "accept"

THEME = Theme(
    id=THEME_ID,
    name="団体予約の受け方",
    shop="切子体験工房（創吉）",
    questions=[
        ThemeQuestion(id=Q_COUNT, title="体験人数の数え方", key_attributes=["団体種別", "立場", "記載"],
                      answers=["子供", "大人", "数えない"]),
        ThemeQuestion(id=Q_PAY, title="支払い方法", key_attributes=["支払い記載"], answers=["現金", "クレジット"]),
        ThemeQuestion(id=Q_ACCEPT, title="受けるか断るか", key_attributes=["規模"], answers=["受ける", "断る"]),
    ],
    vocabulary={
        "団体種別": "小学校・中学校・高校・幼稚園・保育園・支援学校・専門学校・旅行会社",
        "立場": "生徒・園児・先生・添乗員・ガイド",
        "記載": "申込書に書かれた補足（例: 先生も体験）",
        "支払い記載": "添乗員払い・旅行会社名など",
        "人数": "団体の総人数",
        "規模": "100名以下・100名超",
    },
    criteria=CompletionCriteria(coverage_min=1.0, recent_window=30, max_mismatches=0,
                                novelty_window=20, max_new_types=0),
)


def _art(n: int, q: str, conds: list[dict], decision: str, reason: str | None,
         status: ArticleStatus, priority: int = 100) -> Article:
    return Article(id=f"art{n}", theme_id=THEME_ID, question_id=q, number=n,
                   conditions=[Condition(**c) for c in conds], decision=decision, reason=reason,
                   status=status, priority=priority)


def initial_articles() -> list[Article]:
    return [
        _art(1, Q_COUNT, [{"attribute": "立場", "op": Op.IN, "value": ["先生", "添乗員", "ガイド"]}],
             "数えない", "見ているだけで体験しないから", ArticleStatus.PASSED),
        _art(2, Q_COUNT, [{"attribute": "団体種別", "op": Op.IN, "value": ["小学校", "中学校"]},
                          {"attribute": "立場", "value": "生徒"}],
             "子供", None, ArticleStatus.DRAFT),
        _art(3, Q_PAY, [{"attribute": "支払い記載", "value": "添乗員払い"}],
             "現金", "添乗員がその場で現金で払う慣習だから", ArticleStatus.PASSED),
        _art(4, Q_ACCEPT, [{"attribute": "規模", "value": "100名超"}],
             ESCALATE, "部屋と講師の組み替えが要るから", ArticleStatus.PASSED, priority=1),
        _art(5, Q_PAY, [{"attribute": "支払い記載", "value": "JR東海ツアーズ"}],
             "クレジット", "旅行会社の取り決め", ArticleStatus.PASSED),
        _art(6, Q_COUNT, [{"attribute": "団体種別", "value": "高校"}, {"attribute": "立場", "value": "生徒"}],
             "大人", "高校生は大人料金", ArticleStatus.PASSED),
    ]


def _scenes() -> list[dict]:
    """時系列順の場面の素。日付は後で振る。"""
    rnd = random.Random(20261015)
    early: list[dict] = []
    main: list[dict] = []

    def count(kind, role, decision, n_people, decider=Decider.STAFF, role_name="ショップ担当A",
              note=None, src=Source.CORRECTION):
        attrs = {"団体種別": kind, "立場": role}
        if note:
            attrs["記載"] = note
        return dict(q=Q_COUNT, attrs=attrs, decision=decision, decider=decider, role=role_name, src=src,
                    text=f"［{kind}］{role}{n_people}名" + (f"（{note}）" if note else ""))

    # 初めての型・溜まった型・例外は古い側に置く
    early.append(count("保育園", "園児", "子供", 22, role_name="ショップ担当B"))
    for n in (12, 8, 10):
        early.append(count("支援学校", "生徒", "子供", n))
    for n in (2, 3, 2, 2):
        early.append(count("小学校", "先生", "大人", n, decider=Decider.OWNER, role_name="決裁者",
                           note="先生も体験", src=Source.CONVERSATION))
    for n in (110, 124, 120):
        early.append(dict(q=Q_ACCEPT, attrs={"規模": "100名超", "人数": n}, decision="受ける",
                          decider=Decider.OWNER, role="決裁者", src=Source.CONVERSATION,
                          text=f"［学校（修学旅行）］生徒{n}名"))
    for n in range(8):
        early.append(count("高校", "生徒", "大人", rnd.randint(20, 40)))

    for i in range(45):
        main.append(count("小学校" if i % 3 else "中学校", "生徒", "子供", rnd.randint(20, 45),
                          role_name=rnd.choice(["ショップ担当A", "ショップ担当B"])))
    for i in range(30):
        main.append(count("小学校", "生徒", "大人", rnd.randint(20, 45),
                          role_name=rnd.choice(["ショップ担当A", "ショップ担当B"])))
    for i in range(47):
        role = ["先生", "先生", "添乗員", "ガイド"][i % 4]
        main.append(count(rnd.choice(["小学校", "中学校"]), role, "数えない", rnd.randint(1, 4),
                          decider=Decider.OWNER if i % 5 == 0 else Decider.STAFF,
                          role_name="決裁者" if i % 5 == 0 else "ショップ担当A",
                          src=Source.CONVERSATION if i % 5 == 0 else Source.CORRECTION))
    for i in range(18):
        pay = "JR東海ツアーズ" if i % 6 == 0 else "添乗員払い"
        main.append(dict(q=Q_PAY, attrs={"支払い記載": pay}, decision="クレジット" if pay != "添乗員払い" else "現金",
                         decider=Decider.STAFF, role="ショップ担当A", src=Source.CORRECTION,
                         text=f"［旅行会社］経由の団体・支払い: {pay}"))
    rnd.shuffle(main)
    # 読み取りの外れ（高校を中学校と読んだ）は直近の少し前に入れる
    miss = count("中学校", "生徒", "大人", 28)
    miss["id"] = "s-misread"
    miss["verified"] = {"団体種別": "高校"}
    main.insert(len(main) - 25, miss)
    # 直近20件は既知の型だけにする
    tail = [m for m in main[-20:] if not (m["q"] == Q_COUNT and m["decision"] == "大人")]
    head = [m for m in main[:-20]] + [m for m in main[-20:] if m not in tail]
    return early + head + tail


def build(store: Store | None = None) -> Store:
    store = store or Store()
    store.themes[THEME_ID] = THEME.model_copy(deep=True)
    for a in initial_articles():
        store.articles[a.id] = a
    rows = _scenes()
    start = date(2025, 10, 1)
    step = 364 / (len(rows) - 1)
    for i, r in enumerate(rows):
        sid = r.get("id") or f"s{i + 1:03d}"
        store.scenes[sid] = Scene(
            id=sid, theme_id=THEME_ID, question_id=r["q"], occurred_on=start + timedelta(days=round(i * step)),
            situation=r["text"], attributes=r["attrs"], decision=r["decision"], source=r["src"],
            source_ref=None, decider=r["decider"], decider_role=r["role"],
        )
    return store


# 規程に無い場面（出口②のテストで「決裁者に回します」と答えられるかを見る）
BOUNDARY_CASES = [
    {"id": "b-senmon", "question": Q_COUNT, "situation": "［専門学校］学生25名",
     "attributes": {"団体種別": "専門学校", "立場": "生徒"}, "expected": None},
    {"id": "b-furikomi", "question": Q_PAY, "situation": "［旅行会社］経由の団体・支払い: 後日振込",
     "attributes": {"支払い記載": "後日振込"}, "expected": None},
]


SAMPLES = [
    ("団体の人数（分かっている型）", "佐藤：10/21の青葉台小学校さん、FAXだと生徒38名・先生3名・添乗員1名です。担任の山本先生の携帯は090-1234-5678。先生も数えますか？\n店主：先生と添乗員は数えないで。生徒だけ子供で38にしておいて。"),
    ("幼稚園（初めての型・理由つき）", "田中：あおば幼稚園の鈴木さんから、年長さん20名で申込みです。子供でいいですか？\n店主：幼稚園も子供で。未就学児は子供料金だから。"),
    ("幼稚園（理由なし）", "田中：あおば幼稚園の鈴木さんから、年長さん20名で申込みです。子供でいいですか？\n店主：うーん、それでいいよ。"),
    ("判断のないやり取り", "佐藤：明日の天気、雨らしいですよ\n店主：了解"),
    ("パスワード入り（取り込まない）", "佐藤：予約サイトの管理画面のパスワード: sokichi2026 です"),
]
