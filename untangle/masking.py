"""伏せる層。AI に渡す前に、決まった規則で個人情報と秘密を置き換える。

AI には頼らない（AI に「個人情報は無視して」と頼むのでは固く渡したことにならない）。
順番:
  1. 認証情報を含むメッセージは丸ごと取り込まない
  2. 店の台帳（スタッフ名簿・顧客・取引先・学校名など）を辞書にして置き換える
  3. 番号類（電話・メール・URL・郵便番号・長い番号）を決まった規則で消す
  4. 日付は曜日・季節の区分にする
  5. 外部の検出器（Sensitive Data Protection など）で残った固有名詞を探す（差し込み式）
  6. 消し残しの検査。残っていれば保留にして決裁者の確認待ちにする

元の文は保存しない。名前と仮の名前の対応表は店の側だけが持つ。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Protocol

SECRET_PATTERNS = [
    re.compile(r"(パスワード|password|passwd|暗証番号|PIN)\s*[:：は]", re.I),
    re.compile(r"\b(sk|pk|rk)_(live|test)_[A-Za-z0-9]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

NUMBER_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("メール", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("URL", re.compile(r"https?://[^\s　）)」]+")),
    ("電話番号", re.compile(r"(?<!\d)(0\d{1,4}[-‐－ー(（]?\d{1,4}[-‐－ー)）]?\d{3,4})(?!\d)")),
    ("郵便番号", re.compile(r"〒?\s?\d{3}[-‐－]\d{4}")),
    ("番号", re.compile(r"(?<!\d)\d{9,}(?!\d)")),
]

# 名前を伏せても、判断に要る種別は残す（あおば幼稚園 → ［幼稚園］）
CATEGORY_SUFFIXES = ["特別支援学校", "支援学校", "小学校", "中学校", "高等学校", "高校", "幼稚園", "保育園", "こども園",
                     "専門学校", "大学", "学園", "旅行社", "トラベル", "ツアーズ", "観光", "ツーリスト", "商事", "精機"]
SUFFIX_LABEL = {"旅行社": "旅行会社", "トラベル": "旅行会社", "ツアーズ": "旅行会社", "観光": "旅行会社",
                "ツーリスト": "旅行会社", "商事": "会社", "精機": "会社", "学園": "学校", "特別支援学校": "支援学校"}


def category_of(name: str) -> str | None:
    for suf in CATEGORY_SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf):
            return SUFFIX_LABEL.get(suf, suf)
    return None


# 敬称の直前の語は名前とみなす（Gemma が見落としても決まった規則で拾う）
HONORIFIC_NAME = re.compile(r"(?<![一-龥々ァ-ヶー\d０-９])([一-龥々]{1,4}|[ァ-ヶー]{2,8})(?=さん|様|さま|くん|ちゃん|氏)")
NOT_NAMES = {"客", "お客", "皆", "奥", "神", "王", "殿", "各", "名", "子", "団体", "先方", "先生", "生徒", "添乗員", "保育士", "介助員", "担当",
             "担任", "店長", "部長", "社長", "運転手", "ガイド", "スタッフ", "オーナー", "一家", "夫妻", "家族", "子供", "親御",
             "年長", "年中", "年少", "学生", "児童", "園児", "お子", "赤ちゃん", "皆",
             "保護者", "参加者", "利用者", "見学者", "代表者", "担当者", "申込者", "引率者", "幹事"}

DATE_RE = re.compile(r"(?:(\d{4})[/年-])?(\d{1,2})[/月](\d{1,2})日?")
WEEKDAYS = "月火水木金土日"


def season_of(month: int) -> str:
    return {12: "冬", 1: "冬", 2: "冬", 3: "春", 4: "春", 5: "春", 6: "夏", 7: "夏", 8: "夏"}.get(month, "秋")


@dataclass
class DictEntry:
    surface: str        # 実際の表記（例: 佐藤 / 青葉台小学校）
    replacement: str    # 置き換え先（例: ショップ担当A / 学校（小））
    kind: str           # 辞書の種類（スタッフ / お客様 / 取引先 / 学校 …）


@dataclass
class Replacement:
    kind: str
    rule: str           # dictionary / number / date / detector
    replacement: str


@dataclass
class MaskResult:
    text: str | None                     # None なら取り込まない
    replacements: list[Replacement] = field(default_factory=list)
    residual: list[str] = field(default_factory=list)   # 消し残しの疑い（中身ではなく種類）
    rejected_reason: str | None = None

    @property
    def held(self) -> bool:
        """決裁者の確認待ちにするか。"""
        return bool(self.residual)


class Detector(Protocol):
    def __call__(self, text: str) -> list[tuple[int, int, str]]:
        """(開始, 終了, 種類) の一覧を返す。"""
        ...


class Masker:
    def __init__(self, entries: list[DictEntry] = (), detector: Detector | None = None,
                 today: Callable[[], date] = date.today):
        # 長い表記から先に置き換える（「佐藤花子」を「佐藤」より先に）
        self.entries = sorted(entries, key=lambda e: len(e.surface), reverse=True)
        self.detector = detector
        self.today = today

    def mask(self, text: str) -> MaskResult:
        for p in SECRET_PATTERNS:
            if p.search(text):
                return MaskResult(None, rejected_reason="認証情報を含むため取り込みません")

        reps: list[Replacement] = []
        out = text
        for e in self.entries:
            if e.surface and e.surface in out:
                n = out.count(e.surface)
                out = out.replace(e.surface, f"［{e.replacement}］")
                reps += [Replacement(e.kind, "dictionary", e.replacement)] * n

        for kind, pat in NUMBER_RULES:
            def _sub(m: re.Match[str], kind: str = kind) -> str:
                reps.append(Replacement(kind, "number", kind))
                return f"［{kind}］"
            out = pat.sub(_sub, out)

        def _hon(m: re.Match[str]) -> str:
            word = m.group(1)
            if word in NOT_NAMES or re.fullmatch(r"(数|何|若干)?名", word) or any(word.endswith(n) for n in NOT_NAMES if len(n) > 1):
                return word
            reps.append(Replacement("人名", "honorific", "人名"))
            return "［人名］"
        out = HONORIFIC_NAME.sub(_hon, out)

        def _date(m: re.Match[str]) -> str:
            y = int(m.group(1)) if m.group(1) else self.today().year
            mo, d = int(m.group(2)), int(m.group(3))
            try:
                wd = WEEKDAYS[date(y, mo, d).weekday()]
            except ValueError:
                return m.group(0)
            label = f"{'土日' if wd in '土日' else '平日'}・{season_of(mo)}"
            reps.append(Replacement("日付", "date", label))
            return f"［{label}］"
        out = DATE_RE.sub(_date, out)

        if self.detector:
            try:
                found = self.detector(out)
            except Exception:  # noqa: BLE001  検出できないときは渡さない側に倒す
                return MaskResult(out, reps, ["固有名詞の検出ができなかった"])
            spans = sorted(found, key=lambda s: s[0], reverse=True)
            for start, end, kind in spans:
                if "［" in out[start:end]:
                    continue
                label = category_of(out[start:end]) or kind
                out = out[:start] + f"［{label}］" + out[end:]
                reps.append(Replacement(kind, "detector", label))

        return MaskResult(out, reps, self.residual_check(out))

    def residual_check(self, masked: str) -> list[str]:
        """伏せた後の文にもう一度かける。見つかれば種類だけ返す（中身は返さない）。"""
        found: list[str] = []
        plain = re.sub(r"［[^］]*］", "", masked)
        for e in self.entries:
            if e.surface and e.surface in plain:
                found.append(e.kind)
        for kind, pat in NUMBER_RULES:
            if pat.search(plain):
                found.append(kind)
        if self.detector:
            # 伏せた形のまま検査する（伏せた箇所を抜いて文をつなげると、ありもしない語ができる）
            try:
                for _, _, kind in self.detector(masked):
                    found.append(kind)
            except Exception:  # noqa: BLE001
                found.append("固有名詞の検査ができなかった")
        return found
