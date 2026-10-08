"""Untangle のデータの形。

階層は テーマ > 問い > 条文 > 場面。
- 場面(Scene): 1つの判断の記録。AI に渡るのは伏せた後の形だけ。
- 条文(Article): 決裁者が読めて、機械で実行・テストできる規則。構造が正で、文章は表示。
"""
from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Source(StrEnum):
    CONVERSATION = "conversation"  # A: 決裁者と従業員のやり取り
    CORRECTION = "correction"      # B: 業務の中で人が直した跡・承認


class Decider(StrEnum):
    OWNER = "owner"  # 決裁者。この判断が共有知になる
    STAFF = "staff"  # スタッフ。規程との答え合わせ側に回る


class SceneFlag(StrEnum):
    EXCEPTION = "exception"                # 決裁者が「今回だけの例外」と答えた
    REGISTRATION_ERROR = "registration_error"  # 人の登録そのものの誤りと判明
    FREE_JUDGMENT = "free_judgment"        # 「その時の判断」＝揺れとして残し規程にしない
    BEFORE_RULE = "before_rule"            # 決まりができる前の答え方。決裁者が「これからは決まりどおり」と答えた


class Scene(BaseModel):
    id: str
    theme_id: str
    question_id: str | None = None      # 未分類なら None
    occurred_on: date
    situation: str                      # 伏せた後の状況（AI に渡る形）
    attributes: dict[str, Any] = Field(default_factory=dict)  # 判断に使う物差しの値
    decision: str                       # 実際に決まったこと
    reason: str | None = None           # 分かっていれば
    source: Source
    source_ref: str | None = None       # 元のメッセージや変更記録への参照（本文は持たない）
    decider: Decider
    decider_role: str                   # 役割だけ（例: 決裁者 / ショップ担当A）
    flags: list[SceneFlag] = Field(default_factory=list)
    # 読み取りの外れを切り分けるため、人が確かめた属性があれば入れる
    verified_attributes: dict[str, Any] | None = None

    def counts_for_test(self) -> bool:
        """テストの母数に入れるか。例外・その時の判断・登録の誤りは外す。"""
        excluded = {SceneFlag.EXCEPTION, SceneFlag.FREE_JUDGMENT, SceneFlag.REGISTRATION_ERROR, SceneFlag.BEFORE_RULE}
        return not excluded.intersection(self.flags)


class Op(StrEnum):
    EQ = "eq"
    IN = "in"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"   # 文字列・リストに含む
    EXISTS = "exists"       # 値がある（value が False なら「無い」）


class Condition(BaseModel):
    attribute: str
    op: Op = Op.EQ
    value: Any = None


ESCALATE = "決裁者に回す"


class ArticleStatus(StrEnum):
    DRAFT = "draft"            # エージェントの案
    CONFIRMED = "confirmed"    # 決裁者が確認済み
    PASSED = "passed"          # テスト合格
    NOT_A_RULE = "not_a_rule"  # 揺れとして規程にしない


class Article(BaseModel):
    id: str
    theme_id: str
    question_id: str
    number: int                          # 第N条
    conditions: list[Condition]          # すべて満たすと当てはまる
    decision: str                        # ESCALATE なら「決裁者に回す」
    reason: str | None = None            # 決裁者の言葉
    priority: int = 100                  # 小さいほど優先。同じなら条件の多いほうを優先
    exception_of: str | None = None      # ただし書き（例外）の元になる条文
    status: ArticleStatus = ArticleStatus.DRAFT
    version: int = 1
    evidence_scene_ids: list[str] = Field(default_factory=list)

    @property
    def specificity(self) -> int:
        return len(self.conditions)

    @property
    def usable(self) -> bool:
        """出口に載せてよい条文か。"""
        return self.status in (ArticleStatus.CONFIRMED, ArticleStatus.PASSED)


class QuestionKind(StrEnum):
    NEW_TYPE = "new_type"            # ① 初めての型で理由がない
    INCONSISTENT = "inconsistent"    # ② 同じ条件で判断が違う
    AGAINST_RULE = "against_rule"    # ③ 規程と違う判断
    UNCOVERED = "uncovered"          # ④ 答えられない場面が溜まった
    FACT = "fact"                    # スタッフへの事実の確認


class Question(BaseModel):
    id: str
    theme_id: str
    kind: QuestionKind
    to: Decider                      # 方針は決裁者へ、事実はスタッフへ
    text: str
    original_text: str | None = None   # エージェントが言い回しを整える前の文面（決まった規則で作ったもの）
    scene_ids: list[str]
    choices: list[str]
    draft_article: Article | None = None
    answer: str | None = None
    answered_on: date | None = None

    @property
    def open(self) -> bool:
        return self.answer is None


class CompletionCriteria(BaseModel):
    """テーマごとに決裁者が決める完了の基準。"""
    coverage_min: float = 0.95   # 答えられない場面は残っていないか
    recent_window: int = 30      # 答えはあなたと同じか（直近 N 件）
    max_mismatches: int = 0
    novelty_window: int = 20     # もう新しい話は出てこないか（直近 N 件）
    max_new_types: int = 0


class ThemeQuestion(BaseModel):
    id: str
    title: str                       # 例: 体験人数の数え方
    key_attributes: list[str]        # 型を見分ける物差し（新しい型の判定に使う）
    answers: list[str] = Field(default_factory=list)  # この問いの答えの選択肢（場面の読み取りで使う）


class Theme(BaseModel):
    id: str
    name: str
    shop: str = ""                   # どの店のテーマか
    questions: list[ThemeQuestion]
    vocabulary: dict[str, str] = Field(default_factory=dict)  # 属性名 → 説明
    criteria: CompletionCriteria = Field(default_factory=CompletionCriteria)
    released_version: int | None = None
    source: str = ""                 # どこから立てたテーマか（"mail"＝送ったメールのパターンから）
